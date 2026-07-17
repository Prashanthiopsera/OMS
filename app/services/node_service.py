"""Fulfillment node business logic — no FastAPI imports."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres.node_models import FulfillmentNode, NodeStatus, NodeType
from app.schemas.nodes import NodeCreate, NodeListResponse, NodeUpdate
from app.services.exceptions import DuplicateResourceError, NodeNotFoundError


class NodeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_node(self, node_id: UUID) -> FulfillmentNode:
        result = await self.db.execute(select(FulfillmentNode).where(FulfillmentNode.id == node_id))
        node = result.scalar_one_or_none()
        if not node:
            raise NodeNotFoundError("Node not found")
        return node

    async def create_node(self, payload: NodeCreate) -> FulfillmentNode:
        result = await self.db.execute(
            select(FulfillmentNode).where(FulfillmentNode.code == payload.code)
        )
        if result.scalar_one_or_none():
            raise DuplicateResourceError(f"Node with code '{payload.code}' already exists")

        node = FulfillmentNode(**payload.model_dump())
        self.db.add(node)
        await self.db.flush()
        await self.db.refresh(node)
        return node

    async def list_nodes(
        self,
        *,
        node_type: Optional[NodeType] = None,
        status: Optional[NodeStatus] = None,
        can_ship: Optional[bool] = None,
        can_pickup: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> NodeListResponse:
        query = select(FulfillmentNode)
        if node_type:
            query = query.where(FulfillmentNode.node_type == node_type)
        if status:
            query = query.where(FulfillmentNode.status == status)
        if can_ship is not None:
            query = query.where(FulfillmentNode.can_ship == can_ship)
        if can_pickup is not None:
            query = query.where(FulfillmentNode.can_pickup == can_pickup)

        count_result = await self.db.execute(select(func.count()).select_from(query.subquery()))
        total = count_result.scalar_one()

        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        nodes = result.scalars().all()
        return NodeListResponse(items=nodes, total=total)

    async def update_node(self, node_id: UUID, payload: NodeUpdate) -> FulfillmentNode:
        node = await self.get_node(node_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(node, field, value)
        await self.db.flush()
        await self.db.refresh(node)
        return node

    async def deactivate_node(self, node_id: UUID) -> None:
        node = await self.get_node(node_id)
        node.status = NodeStatus.INACTIVE
        await self.db.flush()

    async def get_capacity(self, node_id: UUID) -> dict:
        node = await self.get_node(node_id)
        return {
            "node_id": str(node.id),
            "daily_capacity": node.daily_order_capacity,
            "current_orders": node.current_daily_orders,
            "available_capacity": node.daily_order_capacity - node.current_daily_orders,
            "utilization_pct": round(
                node.current_daily_orders / max(node.daily_order_capacity, 1) * 100, 2
            ),
        }
