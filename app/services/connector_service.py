"""Connector integration business logic — no FastAPI imports."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.postgres.connector_models import (
    Connector,
    ConnectorEvent,
    ConnectorStatus,
    ConnectorType,
)
from app.schemas.connectors import ConnectorCreate, ConnectorTestResult, ConnectorUpdate
from app.services.exceptions import ConnectorNotFoundError


def webhook_url(connector_id: UUID) -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}/connectors/{connector_id}/webhook"


class ConnectorService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_connector(self, connector_id: UUID) -> Connector:
        connector = await self.db.get(Connector, connector_id)
        if not connector:
            raise ConnectorNotFoundError("Connector not found")
        return connector

    async def create_connector(self, payload: ConnectorCreate) -> Connector:
        connector = Connector(
            name=payload.name,
            connector_type=payload.connector_type,
            direction=payload.direction,
            status=ConnectorStatus.INACTIVE,
            config=payload.config or {},
        )
        self.db.add(connector)
        await self.db.flush()
        await self.db.refresh(connector)
        return connector

    async def list_connectors(
        self,
        status: Optional[ConnectorStatus] = None,
        connector_type: Optional[ConnectorType] = None,
    ) -> list[Connector]:
        q = select(Connector).order_by(Connector.created_at.desc())
        if status:
            q = q.where(Connector.status == status)
        if connector_type:
            q = q.where(Connector.connector_type == connector_type)
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def update_connector(self, connector_id: UUID, payload: ConnectorUpdate) -> Connector:
        connector = await self.get_connector(connector_id)

        if payload.name is not None:
            connector.name = payload.name
        if payload.direction is not None:
            connector.direction = payload.direction
        if payload.status is not None:
            connector.status = payload.status
        if payload.config is not None:
            existing = connector.config or {}
            for k, v in payload.config.items():
                if v != "***":
                    existing[k] = v
            connector.config = existing

        await self.db.flush()
        await self.db.refresh(connector)
        return connector

    async def delete_connector(self, connector_id: UUID) -> None:
        connector = await self.get_connector(connector_id)
        await self.db.delete(connector)

    async def toggle_connector(self, connector_id: UUID) -> Connector:
        connector = await self.get_connector(connector_id)
        if connector.status == ConnectorStatus.ACTIVE:
            connector.status = ConnectorStatus.INACTIVE
        else:
            connector.status = ConnectorStatus.ACTIVE
            connector.last_error = None
        await self.db.flush()
        await self.db.refresh(connector)
        return connector

    async def test_connector(self, connector_id: UUID) -> ConnectorTestResult:
        from app.services.connectors.registry import get_connector

        connector = await self.get_connector(connector_id)
        try:
            impl = get_connector(connector)
            result = await impl.test_connection()
            return ConnectorTestResult(
                success=result.get("success", False),
                message=result.get("message", ""),
                details=result.get("details"),
            )
        except ValueError as exc:
            return ConnectorTestResult(success=False, message=str(exc), details=None)

    async def list_events(
        self,
        connector_id: UUID,
        *,
        direction: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConnectorEvent]:
        await self.get_connector(connector_id)
        q = (
            select(ConnectorEvent)
            .where(ConnectorEvent.connector_id == connector_id)
            .order_by(ConnectorEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if direction:
            q = q.where(ConnectorEvent.direction == direction)
        if status:
            q = q.where(ConnectorEvent.status == status)
        result = await self.db.execute(q)
        return list(result.scalars().all())
