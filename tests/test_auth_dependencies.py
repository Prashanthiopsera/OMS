"""
Unit tests for app.dependencies.auth — WO-006 (auth dependencies for the
PyJWT migration). These dependencies never touch python-jose/PyJWT directly;
they consume the already-validated payload the auth middleware attaches to
`request.state.user` after calling verify_token_async (see app/main.py).

Run with: PYTHONPATH=. pytest tests/test_auth_dependencies.py -v
"""
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.dependencies.auth import (
    get_current_user,
    require_superadmin,
    require_platform_owner,
)


def _make_request(user=None):
    return SimpleNamespace(state=SimpleNamespace(user=user))


@pytest.mark.asyncio
async def test_get_current_user_returns_user_when_state_populated():
    """Represents the 'valid token' case: the auth middleware already ran
    verify_token_async successfully and attached the decoded payload."""
    payload = {"sub": "user-1", "email": "a@b.com", "is_superadmin": False}
    user = await get_current_user(_make_request(payload))
    assert user == payload


@pytest.mark.asyncio
async def test_get_current_user_raises_401_when_no_user_on_state():
    """Represents the 'invalid/expired/missing token' case: the middleware
    never populated request.state.user (it would have already returned 401
    itself, but downstream dependencies must also fail closed)."""
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_make_request(user=None))
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_superadmin_allows_superadmin():
    payload = {"sub": "user-1", "is_superadmin": True}
    result = await require_superadmin(payload)
    assert result == payload


@pytest.mark.asyncio
async def test_require_superadmin_rejects_regular_user():
    payload = {"sub": "user-1", "is_superadmin": False}
    with pytest.raises(HTTPException) as exc_info:
        await require_superadmin(payload)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_platform_owner_allows_platform_owner():
    payload = {"sub": "user-1", "platform_role": "PLATFORM_OWNER"}
    result = await require_platform_owner(payload)
    assert result == payload


@pytest.mark.asyncio
async def test_require_platform_owner_rejects_superadmin_without_role():
    payload = {"sub": "user-1", "platform_role": "SUPERADMIN"}
    with pytest.raises(HTTPException) as exc_info:
        await require_platform_owner(payload)
    assert exc_info.value.status_code == 403
