"""
Unit tests for app.core.security — JWT encode/decode (PyJWT), password hashing,
and Redis-backed token revocation.

Run with: PYTHONPATH=. pytest tests/test_security.py -v
"""
import sys
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import jwt
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from app.core.security import (
    ALGORITHM,
    create_access_token,
    verify_token,
    verify_token_async,
    revoke_token,
)


# ─── Encode / decode (PyJWT) ────────────────────────────────────────────────

def test_create_and_verify_token_roundtrip():
    token = create_access_token({"sub": "user-123", "email": "a@b.com"})
    assert isinstance(token, str)
    payload = verify_token(token)
    assert payload["sub"] == "user-123"
    assert payload["email"] == "a@b.com"
    assert "jti" in payload
    assert "exp" in payload


def test_token_encoded_with_explicit_algorithm():
    token = create_access_token({"sub": "user-123"})
    header = jwt.get_unverified_header(token)
    assert header["alg"] == ALGORITHM == "HS256"


def test_expired_token_is_rejected():
    expired_payload = {
        "sub": "user-123",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
        "jti": "expired-jti",
    }
    token = jwt.encode(expired_payload, settings.SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(Exception) as exc_info:
        verify_token(token)
    assert exc_info.value.status_code == 401


def test_algorithm_mismatch_is_rejected():
    """A token signed with a different algorithm than the whitelist must be rejected
    (prevents algorithm-confusion / none-algorithm attacks)."""
    payload = {
        "sub": "user-123",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "jti": "some-jti",
    }
    # Sign with HS384 while verify_token only accepts HS256
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS384")
    with pytest.raises(Exception) as exc_info:
        verify_token(token)
    assert exc_info.value.status_code == 401


def test_tampered_signature_is_rejected():
    token = create_access_token({"sub": "user-123"})
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    with pytest.raises(Exception) as exc_info:
        verify_token(tampered)
    assert exc_info.value.status_code == 401


def test_missing_sub_claim_is_rejected():
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "jti": "no-sub-jti",
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(Exception) as exc_info:
        verify_token(token)
    assert exc_info.value.status_code == 401


# ─── Revocation (Redis jti blocklist) — async ───────────────────────────────

class _FakeRedis:
    def __init__(self, blocked: set[str] | None = None, disabled: set[str] | None = None):
        self._blocked = blocked or set()
        self._disabled = disabled or set()
        self.setex_calls: list[tuple] = []

    async def exists(self, key: str) -> bool:
        if key.startswith("jwt:blocked:"):
            return key.removeprefix("jwt:blocked:") in self._blocked
        if key.startswith("user:disabled:"):
            return key.removeprefix("user:disabled:") in self._disabled
        return False

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_verify_token_async_accepts_valid_unrevoked_token():
    token = create_access_token({"sub": "user-123"})
    fake_redis = _FakeRedis()
    with patch("app.database.redis_client.get_redis_client", return_value=fake_redis):
        payload = await verify_token_async(token)
    assert payload["sub"] == "user-123"


@pytest.mark.asyncio
async def test_verify_token_async_rejects_revoked_token():
    token = create_access_token({"sub": "user-123", "jti": "revoked-jti"})
    fake_redis = _FakeRedis(blocked={"revoked-jti"})
    with patch("app.database.redis_client.get_redis_client", return_value=fake_redis):
        with pytest.raises(Exception) as exc_info:
            await verify_token_async(token)
    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_verify_token_async_fails_closed_when_redis_unavailable():
    token = create_access_token({"sub": "user-123"})
    with patch("app.database.redis_client.get_redis_client", return_value=None):
        with pytest.raises(Exception) as exc_info:
            await verify_token_async(token)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_revoke_token_adds_jti_to_blocklist():
    token = create_access_token({"sub": "user-123", "jti": "to-revoke"})
    fake_redis = _FakeRedis()
    with patch("app.database.redis_client.get_redis_client", return_value=fake_redis):
        await revoke_token(token)
    assert len(fake_redis.setex_calls) == 1
    key, ttl, _ = fake_redis.setex_calls[0]
    assert key == "jwt:blocked:to-revoke"
    assert ttl > 0


# ─── Password hashing ───────────────────────────────────────────────────────

def test_password_hash_and_verify():
    from app.core.security import hash_password, verify_password
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"
    assert verify_password("correct-horse-battery-staple", hashed) is True
    assert verify_password("wrong-password", hashed) is False
