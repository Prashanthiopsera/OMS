"""Auth flow behavioral tests (WO-028 / WO-029).

Covers login, logout, token revocation, disabled-user rejection, and login
rate limiting using the async httpx + ASGITransport test harness.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import FakeRedis, auth_headers, issue_token, login_user, persist_user

pytestmark = pytest.mark.requires_db


@pytest.mark.asyncio
async def test_login_success_returns_jwt_and_user_info(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
):
    user = await persist_user(behavioral_session, user_factory)
    response = await behavioral_client.post(
        "/auth/login",
        json={"email": user.email, "password": "test-password-123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["user"]["email"] == user.email
    assert "access_token" in response.cookies


@pytest.mark.asyncio
async def test_login_wrong_password_rejected(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
):
    user = await persist_user(behavioral_session, user_factory)
    response = await behavioral_client.post(
        "/auth/login",
        json={"email": user.email, "password": "wrong-password"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_disabled_user_rejected(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
):
    user = await persist_user(behavioral_session, user_factory, is_active=False)
    response = await behavioral_client.post(
        "/auth/login",
        json={"email": user.email, "password": "test-password-123"},
    )
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_authenticated_me_via_bearer_token(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
):
    user = await persist_user(behavioral_session, user_factory)
    token = await issue_token(user)
    response = await behavioral_client.get("/auth/me", headers=auth_headers(token))
    assert response.status_code == 200
    assert response.json()["email"] == user.email


@pytest.mark.asyncio
async def test_unauthenticated_me_rejected(behavioral_client: AsyncClient):
    response = await behavioral_client.get("/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_token(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
    behavioral_redis: FakeRedis,
):
    user = await persist_user(behavioral_session, user_factory)
    token = await issue_token(user)

    logout_resp = await behavioral_client.post("/auth/logout", headers=auth_headers(token))
    assert logout_resp.status_code == 204
    assert behavioral_redis.setex_calls, "logout must blocklist the token jti in Redis"

    rejected = await behavioral_client.get("/auth/me", headers=auth_headers(token))
    assert rejected.status_code == 401
    assert "revoked" in rejected.json()["detail"].lower()


@pytest.mark.asyncio
async def test_disabled_user_token_rejected_after_redis_marker(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
    behavioral_redis: FakeRedis,
):
    user = await persist_user(behavioral_session, user_factory)
    token = await issue_token(user)
    behavioral_redis.mark_user_disabled(str(user.id))

    response = await behavioral_client.get("/auth/me", headers=auth_headers(token))
    assert response.status_code == 401
    assert "disabled" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_redis_unavailable_fails_closed_with_503(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
):
    user = await persist_user(behavioral_session, user_factory)
    token = await issue_token(user)

    with patch("app.database.redis_client.get_redis_client", return_value=None):
        response = await behavioral_client.get("/auth/me", headers=auth_headers(token))
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_login_rate_limit_enforced(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
):
    """Login endpoint allows 5 attempts/minute per IP — the 6th must return 429."""
    from app.routers import auth as auth_router

    auth_router.limiter.reset()
    auth_router.limiter._key_func = lambda _req: "rate-limit-test-ip"

    email = f"rate-limit-{uuid.uuid4().hex[:8]}@example.com"
    await persist_user(behavioral_session, user_factory, email=email)

    statuses: list[int] = []
    for _ in range(6):
        resp = await behavioral_client.post("/auth/login", json={"email": email, "password": "wrong"})
        statuses.append(resp.status_code)

    assert statuses[:5].count(401) == 5
    assert statuses[5] == 429


@pytest.mark.asyncio
async def test_re_login_issues_fresh_token_after_logout(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
):
    """Re-authentication after logout yields a new usable token (token refresh surrogate)."""
    from app.routers import auth as auth_router

    auth_router.limiter.reset()
    auth_router.limiter._key_func = lambda _req: "relogin-test-ip"

    user = await persist_user(behavioral_session, user_factory)
    first_token = await login_user(behavioral_client, user.email)
    await behavioral_client.post("/auth/logout", headers=auth_headers(first_token))

    second_token = await login_user(behavioral_client, user.email)
    assert second_token != first_token

    me_resp = await behavioral_client.get("/auth/me", headers=auth_headers(second_token))
    assert me_resp.status_code == 200
