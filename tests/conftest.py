"""
Behavioral test infrastructure (WO-024..026).

Async httpx client with ASGITransport, NullPool test engine, and transaction
rollback isolation per test.
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Callable
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool


class FakeRedis:
    """In-memory Redis stand-in for JWT blocklist and user-disable checks."""

    def __init__(self) -> None:
        self._blocked: set[str] = set()
        self._disabled: set[str] = set()
        self._kv: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    async def exists(self, key: str) -> bool:
        if key.startswith("jwt:blocked:"):
            return key.removeprefix("jwt:blocked:") in self._blocked
        if key.startswith("user:disabled:"):
            return key.removeprefix("user:disabled:") in self._disabled
        return key in self._kv

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))
        if key.startswith("jwt:blocked:"):
            self._blocked.add(key.removeprefix("jwt:blocked:"))
        else:
            self._kv[key] = value

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass

    def mark_user_disabled(self, user_id: str) -> None:
        self._disabled.add(user_id)


def _database_url() -> str:
    from app.config import settings

    return os.environ.get("DATABASE_URL", settings.DATABASE_URL)


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    from app.database.postgres import Base

    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL is not configured")
    engine = create_async_engine(url, poolclass=NullPool, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """One rolled-back transaction per test — no data persists."""
    connection = await test_engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest_asyncio.fixture
async def async_client(
    test_session: AsyncSession,
    fake_redis: FakeRedis,
) -> AsyncGenerator[AsyncClient, None]:
    from fastapi import Request

    from app.database.postgres import get_db
    from app.main import app

    async def override_get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
        yield test_session
        await test_session.flush()

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    with patch("app.database.redis_client.get_redis_client", return_value=fake_redis):
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def default_environment(test_session: AsyncSession):
    """Return the system default ACTIVE environment (committed control-plane row)."""
    from app.models.postgres.org_models import Environment, EnvironmentStatus

    result = await test_session.execute(
        select(Environment)
        .where(Environment.is_default.is_(True))
        .where(Environment.status == EnvironmentStatus.ACTIVE)
        .limit(1)
    )
    env = result.scalar_one_or_none()
    if env is None:
        pytest.skip("No default ACTIVE environment in database — run seed/migrations")
    return env


async def persist_user(session: AsyncSession, user_factory: Callable[..., Any], **overrides: Any):
    """Insert a user into the test session and flush."""
    user = user_factory(**overrides)
    session.add(user)
    await session.flush()
    return user


async def issue_token(user) -> str:
    """Issue a JWT for a persisted user without hitting the rate-limited login endpoint."""
    from app.core.security import create_access_token

    platform_role = user.effective_platform_role if hasattr(user, "effective_platform_role") else "USER"
    is_superadmin = platform_role in ("SUPERADMIN", "PLATFORM_OWNER")
    return create_access_token(
        {
            "sub": str(user.id),
            "email": user.email,
            "full_name": user.full_name or "",
            "is_superadmin": is_superadmin,
            "platform_role": platform_role,
            "permissions": ["*"] if is_superadmin else [],
            "brand_ids": None if is_superadmin else [],
        }
    )


async def login_user(
    client: AsyncClient,
    email: str,
    password: str = "test-password-123",
) -> str:
    """Login and return the JWT access token."""
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def env_headers(environment_id: str) -> dict[str, str]:
    return {"X-OMS-Environment": str(environment_id)}


@pytest.fixture
def user_factory():
    from tests.factories import make_user

    return make_user


@pytest.fixture
def brand_factory():
    from tests.factories import make_brand

    return make_brand


@pytest.fixture
def node_factory():
    from tests.factories import make_fulfillment_node

    return make_fulfillment_node


@pytest.fixture
def inventory_factory():
    from tests.factories import make_inventory_item

    return make_inventory_item


@pytest.fixture
def order_factory():
    from tests.factories import make_order

    return make_order
