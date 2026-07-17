"""
Behavioral test infrastructure (WO-024..026).

Async httpx client with ASGITransport, NullPool test engine, and transaction
rollback isolation per test.
"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings
from app.database.postgres import Base, get_db


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", settings.DATABASE_URL)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL is not configured")
    return url


@pytest_asyncio.fixture(scope="session")
async def test_engine(database_url: str):
    engine = create_async_engine(database_url, poolclass=NullPool, echo=False)
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


@pytest_asyncio.fixture
async def async_client(test_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    from fastapi import Request

    from app.main import app

    async def override_get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
        try:
            yield test_session
            await test_session.flush()
        except Exception:
            await test_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock(return_value=None)

    transport = ASGITransport(app=app)
    with patch("app.database.redis_client.get_redis_client", return_value=mock_redis):
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

    app.dependency_overrides.clear()


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
