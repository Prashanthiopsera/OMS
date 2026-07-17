"""Sample behavioral tests validating test infrastructure (WO-025/026)."""
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.requires_db


@pytest.mark.asyncio
async def test_health_endpoint(async_client):
    response = await async_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_transaction_rollback_isolation(test_engine, user_factory):
    """Committed-looking writes inside a rolled-back connection must not persist."""
    from app.models.postgres.auth_models import User

    email = f"isolation-{uuid.uuid4().hex}@example.com"

    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        session.add(user_factory(email=email))
        await session.flush()
        await session.close()
        await transaction.rollback()

    async with test_engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        count = (
            await session.execute(
                select(func.count()).select_from(User).where(User.email == email)
            )
        ).scalar_one()
        await session.close()
        assert count == 0
