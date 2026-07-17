"""
Unit tests for scripts/scan_compose_credentials.py — WO-015..018 (Docker
Compose credential externalization). Also acts as a regression guard: the
real repo compose files must stay clean.

Run with: PYTHONPATH=. pytest tests/test_credential_scan.py -v
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from scan_compose_credentials import scan_file, COMPOSE_FILES  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_repo_compose_files_have_no_plaintext_credentials():
    """Regression guard for the real, committed compose files."""
    violations = []
    for f in COMPOSE_FILES:
        violations.extend(scan_file(f))
    assert violations == [], f"Found plaintext credentials: {violations}"


def test_root_and_bundled_compose_files_are_identical():
    """The bundled PyPI asset must not silently drift from the root file."""
    root = (REPO_ROOT / "docker-compose.yml").read_text()
    bundled = (REPO_ROOT / "kuberiva_oms" / "assets" / "docker-compose.yml").read_text()
    assert root == bundled


def test_scanner_detects_plaintext_mapping_password(tmp_path):
    bad_file = tmp_path / "docker-compose.yml"
    bad_file.write_text(
        "services:\n"
        "  postgres:\n"
        "    environment:\n"
        "      POSTGRES_PASSWORD: hardcoded-secret\n"
    )
    violations = scan_file(bad_file)
    assert len(violations) == 1
    assert "POSTGRES_PASSWORD" in violations[0]


def test_scanner_detects_plaintext_requirepass(tmp_path):
    bad_file = tmp_path / "docker-compose.yml"
    bad_file.write_text(
        "services:\n"
        "  redis:\n"
        "    command: redis-server --requirepass hardcoded-secret\n"
    )
    violations = scan_file(bad_file)
    assert len(violations) == 1
    assert "requirepass" in violations[0]


def test_scanner_detects_plaintext_credential_in_url(tmp_path):
    bad_file = tmp_path / "docker-compose.yml"
    bad_file.write_text(
        "services:\n"
        "  api:\n"
        "    environment:\n"
        "      - DATABASE_URL=postgresql+asyncpg://oms_user:hardcoded-secret@postgres:5432/oms_db\n"
    )
    violations = scan_file(bad_file)
    assert len(violations) == 1
    assert "DATABASE_URL" in violations[0]


def test_scanner_allows_interpolated_values(tmp_path):
    good_file = tmp_path / "docker-compose.yml"
    good_file.write_text(
        "services:\n"
        "  postgres:\n"
        "    environment:\n"
        "      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-oms_pass}\n"
        "  redis:\n"
        "    command: redis-server --requirepass ${REDIS_PASSWORD:-oms_pass}\n"
        "  api:\n"
        "    environment:\n"
        "      - DATABASE_URL=postgresql+asyncpg://oms_user:${POSTGRES_PASSWORD:-oms_pass}@postgres:5432/oms_db\n"
    )
    assert scan_file(good_file) == []


def test_scanner_returns_empty_for_missing_file(tmp_path):
    assert scan_file(tmp_path / "does-not-exist.yml") == []
