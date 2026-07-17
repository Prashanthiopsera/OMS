"""Behavioral test fixtures — isolated from root conftest to avoid slowapi/Request injection issues."""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.postgres import Base, get_db
from tests.conftest import FakeRedis

_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://oms_user:oms_pass@localhost:5433/oms_db",
)


@pytest_asyncio.fixture(scope="session")
async def behavioral_engine():
    engine = create_async_engine(_DB_URL, poolclass=NullPool, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def behavioral_session(behavioral_engine) -> AsyncGenerator[AsyncSession, None]:
    connection = await behavioral_engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


@pytest.fixture
def behavioral_redis() -> FakeRedis:
    return FakeRedis()


@pytest_asyncio.fixture
async def behavioral_client(
    behavioral_session: AsyncSession,
    behavioral_redis: FakeRedis,
) -> AsyncGenerator[AsyncClient, None]:
    from app.main import app

    async def override_get_db(request: Request):
        yield behavioral_session
        await behavioral_session.flush()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    with patch("app.database.redis_client.get_redis_client", return_value=behavioral_redis):
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    app.dependency_overrides.clear()
