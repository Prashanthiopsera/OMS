"""RBAC and API key behavioral tests (WO-028 / WO-030).

Covers superadmin access, brand-scoped users, API key lookup/scopes, and
cross-brand denial using the async behavioral test harness.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_user_or_api_key, require_scope
from app.models.postgres.order_models import OrderChannel, FulfillmentType
from app.models.postgres.org_models import Environment, EnvironmentStatus
from tests.conftest import (
    _TestSessionFactory,
    auth_headers,
    env_headers,
    issue_token,
    persist_user,
)

pytestmark = pytest.mark.requires_db


@pytest_asyncio.fixture
async def default_environment(behavioral_session: AsyncSession):
    result = await behavioral_session.execute(
        select(Environment)
        .where(Environment.is_default.is_(True))
        .where(Environment.status == EnvironmentStatus.ACTIVE)
        .limit(1)
    )
    env = result.scalar_one_or_none()
    if env is None:
        pytest.skip("No default ACTIVE environment in database — run seed/migrations")
    return env


def _minimal_order_payload(brand_id: str | None = None) -> dict:
    payload = {
        "channel": OrderChannel.WEB.value,
        "fulfillment_type": FulfillmentType.SHIP_TO_HOME.value,
        "customer_email": "buyer@example.com",
        "line_items": [
            {
                "sku": "SKU-TEST-001",
                "product_name": "Test Widget",
                "quantity": 1,
                "unit_price": "29.99",
            }
        ],
        "shipping_address": {
            "address1": "123 Main St",
            "city": "New York",
            "state": "NY",
            "postal_code": "10001",
            "country": "US",
        },
    }
    if brand_id:
        payload["brand_id"] = brand_id
    return payload


@pytest.mark.asyncio
async def test_superadmin_can_list_orders(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
    default_environment,
):
    user = await persist_user(
        behavioral_session,
        user_factory,
        is_superadmin=True,
        platform_role="SUPERADMIN",
    )
    token = await issue_token(user)
    response = await behavioral_client.get(
        "/orders/",
        headers={**auth_headers(token), **env_headers(default_environment.id)},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_regular_user_without_brand_access_gets_empty_orders(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
    default_environment,
):
    user = await persist_user(behavioral_session, user_factory, is_superadmin=False)
    token = await issue_token(user)
    response = await behavioral_client.get(
        "/orders/",
        headers={**auth_headers(token), **env_headers(default_environment.id)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_brand_scoped_user_can_list_orders_in_assigned_brand(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
    brand_factory,
    user_brand_role_factory,
    default_environment,
):
    user = await persist_user(behavioral_session, user_factory)
    brand = brand_factory()
    behavioral_session.add(brand)
    await behavioral_session.flush()
    behavioral_session.add(
        user_brand_role_factory(
            user_id=user.id,
            brand_id=brand.id,
            environment_id=default_environment.id,
            role="OPERATOR",
        )
    )
    await behavioral_session.flush()

    token = await issue_token(user)
    response = await behavioral_client.get(
        "/orders/",
        headers={**auth_headers(token), **env_headers(default_environment.id)},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_brand_scoped_user_cannot_create_order_for_foreign_brand(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
    brand_factory,
    user_brand_role_factory,
    default_environment,
):
    user = await persist_user(behavioral_session, user_factory)
    allowed_brand = brand_factory()
    foreign_brand = brand_factory()
    behavioral_session.add_all([allowed_brand, foreign_brand])
    await behavioral_session.flush()
    behavioral_session.add(
        user_brand_role_factory(
            user_id=user.id,
            brand_id=allowed_brand.id,
            environment_id=default_environment.id,
        )
    )
    await behavioral_session.flush()

    token = await issue_token(user)
    response = await behavioral_client.post(
        "/orders/",
        headers={**auth_headers(token), **env_headers(default_environment.id)},
        json=_minimal_order_payload(str(foreign_brand.id)),
    )
    assert response.status_code == 403
    assert "access" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_user_with_no_brand_access_cannot_create_order(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
    brand_factory,
    default_environment,
):
    user = await persist_user(behavioral_session, user_factory)
    brand = brand_factory()
    behavioral_session.add(brand)
    await behavioral_session.flush()

    token = await issue_token(user)
    response = await behavioral_client.post(
        "/orders/",
        headers={**auth_headers(token), **env_headers(default_environment.id)},
        json=_minimal_order_payload(str(brand.id)),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_can_create_api_key(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
):
    admin = await persist_user(
        behavioral_session,
        user_factory,
        is_superadmin=True,
        platform_role="SUPERADMIN",
    )
    token = await issue_token(admin)
    response = await behavioral_client.post(
        "/api-keys",
        headers=auth_headers(token),
        json={"name": "behavioral-test-key", "scopes": ["orders:read"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith("kr_")
    assert body["scopes"] == ["orders:read"]


@pytest.mark.asyncio
async def test_non_superadmin_cannot_create_api_key(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    user_factory,
):
    user = await persist_user(behavioral_session, user_factory)
    token = await issue_token(user)
    response = await behavioral_client.post(
        "/api-keys",
        headers=auth_headers(token),
        json={"name": "forbidden-key", "scopes": ["orders:read"]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_api_key_lookup_returns_owner_context(
    behavioral_session: AsyncSession,
    user_factory,
    api_key_factory,
):
    owner = await persist_user(behavioral_session, user_factory)
    api_key, raw_key = api_key_factory(owner_user_id=owner.id, scopes=["orders:read", "orders:write"])
    behavioral_session.add(api_key)
    await behavioral_session.flush()

    request = SimpleNamespace(
        state=SimpleNamespace(user=None),
        headers={"X-API-Key": raw_key},
    )
    with patch("app.database.postgres.async_session_factory", _TestSessionFactory(behavioral_session)):
        ctx = await get_current_user_or_api_key(request)
    assert ctx["sub"] == str(owner.id)
    assert ctx["api_key_id"] == str(api_key.id)
    assert "orders:write" in ctx["scopes"]


@pytest.mark.asyncio
async def test_api_key_invalid_key_rejected(behavioral_session: AsyncSession):
    request = SimpleNamespace(
        state=SimpleNamespace(user=None),
        headers={"X-API-Key": "kr_invalidkeythatdoesnotexistindatabase000000"},
    )
    from fastapi import HTTPException

    with patch("app.database.postgres.async_session_factory", _TestSessionFactory(behavioral_session)):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user_or_api_key(request)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_require_scope_allows_jwt_user_without_scope_check():
    jwt_user = {"sub": "user-1", "email": "a@b.com", "is_superadmin": False}
    checker = require_scope("orders:write")
    result = await checker(jwt_user)
    assert result == jwt_user


@pytest.mark.asyncio
async def test_require_scope_enforces_api_key_scopes():
    from fastapi import HTTPException

    api_key_user = {
        "sub": "user-1",
        "api_key_id": str(uuid.uuid4()),
        "scopes": ["orders:read"],
    }
    checker = require_scope("orders:write")
    with pytest.raises(HTTPException) as exc_info:
        await checker(api_key_user)
    assert exc_info.value.status_code == 403

    allowed = await checker({"sub": "user-1", "api_key_id": str(uuid.uuid4()), "scopes": ["orders:write"]})
    assert allowed["scopes"] == ["orders:write"]
