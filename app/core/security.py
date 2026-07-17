import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, status
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.config import settings

ALGORITHM = "HS256"
_BLOCKLIST_PREFIX = "jwt:blocked:"

# Dual-algorithm password hashing: Argon2id (OWASP-recommended default) is used
# for all new hashes; BcryptHasher is kept only so verify() can still validate
# passwords hashed before this migration. New hashes are never bcrypt.
_password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))


def hash_password(password: str) -> str:
    """Hash a password with Argon2id."""
    return _password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against either an Argon2id or a legacy bcrypt hash."""
    return _password_hash.verify(plain_password, hashed_password)


def verify_and_upgrade_password(plain_password: str, hashed_password: str) -> tuple[bool, str | None]:
    """Verify a password and, if it was stored with a legacy/outdated hasher
    (bcrypt), return a freshly computed Argon2id hash so the caller can
    transparently re-hash it (e.g. on successful login). Returns
    ``(is_valid, new_hash_or_None)`` — ``new_hash`` is None when no upgrade
    is needed (already Argon2id) or the password was invalid.
    """
    return _password_hash.verify_and_update(plain_password, hashed_password)


def create_access_token(data: dict[str, Any]) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode["exp"] = expire
    to_encode.setdefault("jti", str(_uuid.uuid4()))  # unique token ID for revocation
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


_REDIS_UNAVAILABLE = object()  # sentinel


async def _is_token_revoked(jti: str):
    """Return True if the token ID is on the Redis blocklist.

    Returns the sentinel _REDIS_UNAVAILABLE when Redis cannot be reached so
    callers can fail-closed (deny the request) rather than fail-open.
    """
    try:
        from app.database.redis_client import get_redis_client
        redis = get_redis_client()
        if redis is None:
            # Redis not configured at all — treat as unavailable (fail-closed)
            return _REDIS_UNAVAILABLE
        result = await redis.exists(f"{_BLOCKLIST_PREFIX}{jti}")
        await redis.aclose()
        return bool(result)
    except Exception:
        return _REDIS_UNAVAILABLE  # fail-closed: Redis outage → deny


async def revoke_token(token: str) -> None:
    """Add a token's jti to the Redis blocklist with TTL = remaining token lifetime."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        exp = payload.get("exp")
        if not jti or not exp:
            return
        ttl = max(1, int(exp - datetime.now(timezone.utc).timestamp()))
        from app.database.redis_client import get_redis_client
        redis = get_redis_client()
        if redis is None:
            return
        await redis.setex(f"{_BLOCKLIST_PREFIX}{jti}", ttl, "1")
        await redis.aclose()
    except Exception:
        pass  # best-effort


def verify_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("sub") is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            )
        return payload
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


async def verify_token_async(token: str) -> dict[str, Any]:
    """Async variant that additionally checks the Redis revocation blocklist
    and the per-user disable key set when an account is deactivated.

    Fails closed: if Redis is unavailable the request is denied with 401 to
    prevent revoked tokens from being accepted during an outage.
    """
    payload = verify_token(token)
    jti = payload.get("jti")
    if jti:
        revoked = await _is_token_revoked(jti)
        if revoked is _REDIS_UNAVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication service temporarily unavailable",
            )
        if revoked:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
            )
    # Check per-user disable marker (set when account is deactivated via admin)
    sub = payload.get("sub")
    if sub:
        try:
            from app.database.redis_client import get_redis_client
            redis = get_redis_client()
            if redis is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication service temporarily unavailable",
                )
            disabled = await redis.exists(f"user:disabled:{sub}")
            await redis.aclose()
            if disabled:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Account has been disabled",
                )
        except HTTPException:
            raise
        except Exception:
            # Redis connection error — fail-closed
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication service temporarily unavailable",
            )
    return payload
