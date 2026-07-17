"""Sourcing orchestration service — wraps sourcing_engine for agent/API use."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres.sourcing_rule_models import SourcingStrategy
from app.services.exceptions import OrderNotFoundError
from app.services.order_service import OrderService
from app.services.sourcing_engine import SourcingEngine, SourcingResult


class SourcingService:
    """Thin service facade over the sourcing engine for consistent agent invocation."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._orders = OrderService(db)
        self._engine = SourcingEngine(db)

    async def get_order_for_sourcing(self, order_id: UUID):
        return await self._orders.get_order(order_id)

    async def source_order(
        self,
        order_id: UUID,
        *,
        force_strategy: Optional[SourcingStrategy] = None,
        skip_rule: bool = False,
    ) -> SourcingResult:
        try:
            order = await self.get_order_for_sourcing(order_id)
        except OrderNotFoundError:
            raise
        return await self._engine.source_order(
            order,
            force_strategy=force_strategy,
            skip_rule=skip_rule,
        )

    async def source_order_by_id(
        self,
        order_id: UUID,
        *,
        strategy_name: Optional[str] = None,
    ) -> dict[str, Any]:
        force = SourcingStrategy(strategy_name) if strategy_name else None
        result = await self.source_order(order_id, force_strategy=force)
        return {
            "order_id": str(result.order_id),
            "strategy_used": result.strategy_used.value if result.strategy_used else None,
            "total_split_nodes": result.total_split_nodes,
            "sourcing_score": result.sourcing_score,
            "processing_time_ms": result.processing_time_ms,
            "allocations": len(result.allocations),
        }
