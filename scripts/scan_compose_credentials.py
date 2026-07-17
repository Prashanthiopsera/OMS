#!/usr/bin/env python3
"""
Credential regression scanner for docker-compose files.

Fails (non-zero exit) if any Docker Compose file in the repo assigns a
plaintext value to a known credential-bearing key/env-var instead of an
``${VAR:-default}`` / ``${VAR:?...}`` interpolation. This exists because the
root docker-compose.yml AND kuberiva_oms/assets/docker-compose.yml (bundled
and shipped via PyPI) previously hardcoded "oms_pass" everywhere — see
WO-015..WO-018. A hardcoded local-dev default is fine as the fallback inside
``${VAR:-default}``; a bare literal assigned directly to the key is not.

Usage: python scripts/scan_compose_credentials.py
Exit code 0 = clean, 1 = plaintext credential(s) found.
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

COMPOSE_FILES = [
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "kuberiva_oms" / "assets" / "docker-compose.yml",
]

# key: value  (mapping-style) — flags a plaintext credential value
MAPPING_KEYS = (
    "POSTGRES_PASSWORD",
    "MONGO_INITDB_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    "FLOWER_PASSWORD",
)

# - KEY=value  (list-style env entries) — flags a plaintext credential in a URL
LIST_KEY_PATTERNS = (
    "DATABASE_URL",
    "SYNC_DATABASE_URL",
    "MONGODB_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
)

# --requirepass <value> on the redis command line
REQUIREPASS_RE = re.compile(r"--requirepass\s+(\S+)")


def _is_interpolated(value: str) -> bool:
    """True if the value is entirely a ${...} interpolation (safe)."""
    value = value.strip().strip('"').strip("'")
    return value.startswith("${") and value.endswith("}")


def scan_file(path: Path) -> list[str]:
    violations: list[str] = []
    if not path.exists():
        return violations

    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        stripped = line.strip()

        for key in MAPPING_KEYS:
            m = re.match(rf"{key}:\s*(.+)$", stripped)
            if m and not _is_interpolated(m.group(1)):
                violations.append(
                    f"{path}:{lineno}: plaintext credential for {key!r}: {stripped!r}"
                )

        m = REQUIREPASS_RE.search(stripped)
        if m and not _is_interpolated(m.group(1)):
            violations.append(
                f"{path}:{lineno}: plaintext --requirepass value: {stripped!r}"
            )

        for key in LIST_KEY_PATTERNS:
            if stripped.startswith(f"- {key}="):
                url = stripped.split("=", 1)[1]
                # Credential sits between "://" (or "://:" for no-username
                # redis URLs) and the next "@". Flag if that segment has no
                # ${...} interpolation inside it (i.e. it's a bare literal).
                cred_match = re.search(r"://[^@]*?:([^@/]+)@", url)
                if cred_match and not _is_interpolated(cred_match.group(1)):
                    violations.append(
                        f"{path}:{lineno}: plaintext credential in {key} URL: {stripped!r}"
                    )
    return violations


def main() -> int:
    all_violations: list[str] = []
    for compose_file in COMPOSE_FILES:
        all_violations.extend(scan_file(compose_file))

    if all_violations:
        print("Plaintext credentials found in Docker Compose files:\n")
        for v in all_violations:
            print(f"  - {v}")
        print(
            "\nUse ${VAR:-local-dev-default} (or ${VAR:?must be set}) instead of a "
            "bare literal so real deployments can override it via environment "
            "variables / secrets managers."
        )
        return 1

    print(f"No plaintext credentials found in {len(COMPOSE_FILES)} compose file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
