"""Write-capable agent tools invoking domain services."""
from __future__ import annotations

from uuid import UUID

from app.agents.registry import register_tool
from app.schemas.orders import CancelOrderRequest, OrderStatusUpdate
from app.services.error_mapping import http_exception_from_domain
from app.services.exceptions import DomainError
from app.services.inventory_service import InventoryService
from app.services.order_service import OrderService
from app.services.sourcing_service import SourcingService


@register_tool(
    name="create_order",
    description="Create a new order via OrderService.",
    input_schema={"type": "object", "properties": {"payload": {"type": "object"}}},
    permission_scope="orders:write",
    has_side_effects=True,
    requires_confirmation=True,
)
async def create_order(tool_input: dict, db=None) -> dict:
    from app.schemas.orders import OrderCreate

    service = OrderService(db)
    try:
        payload = OrderCreate.model_validate(tool_input["payload"])
        order = await service.create_order(payload)
        return {"order_id": str(order.id), "order_number": order.order_number}
    except DomainError as exc:
        err = http_exception_from_domain(exc)
        return {"error": err.detail}


@register_tool(
    name="update_order_status",
    description="Update order status with lifecycle validation.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "status": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["order_id", "status"],
    },
    permission_scope="orders:write",
    has_side_effects=True,
    requires_confirmation=True,
)
async def update_order_status(tool_input: dict, db=None) -> dict:
    from app.models.postgres.order_models import OrderStatus

    service = OrderService(db)
    try:
        payload = OrderStatusUpdate(status=OrderStatus(tool_input["status"].upper()), notes=tool_input.get("notes"))
        order, old = await service.update_status(UUID(tool_input["order_id"]), payload)
        return {"order_id": str(order.id), "old_status": old.value, "new_status": order.status.value}
    except DomainError as exc:
        err = http_exception_from_domain(exc)
        return {"error": err.detail}


@register_tool(
    name="cancel_order",
    description="Cancel an order and release B2B credit if applicable.",
    input_schema={
        "type": "object",
        "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["order_id", "reason"],
    },
    permission_scope="orders:write",
    has_side_effects=True,
    requires_confirmation=True,
)
async def cancel_order(tool_input: dict, db=None) -> dict:
    service = OrderService(db)
    try:
        order = await service.cancel_order(
            UUID(tool_input["order_id"]),
            CancelOrderRequest(reason=tool_input["reason"]),
        )
        return {"order_id": str(order.id), "status": order.status.value}
    except DomainError as exc:
        err = http_exception_from_domain(exc)
        return {"error": err.detail}


@register_tool(
    name="adjust_inventory",
    description="Adjust inventory quantity at a node.",
    input_schema={
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "quantity_delta": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["item_id", "quantity_delta"],
    },
    permission_scope="inventory:write",
    has_side_effects=True,
    requires_confirmation=True,
)
async def adjust_inventory(tool_input: dict, db=None) -> dict:
    from app.models.postgres.inventory_models import InventoryAdjustmentReason
    from app.schemas.inventory import InventoryAdjustmentCreate

    service = InventoryService(db)
    try:
        payload = InventoryAdjustmentCreate(
            quantity_delta=int(tool_input["quantity_delta"]),
            reason=InventoryAdjustmentReason.CORRECTION,
        )
        adj, item = await service.adjust_inventory(UUID(tool_input["item_id"]), payload)
        return {
            "adjustment_id": str(adj.id),
            "quantity_available": item.quantity_available,
        }
    except DomainError as exc:
        err = http_exception_from_domain(exc)
        return {"error": err.detail}


@register_tool(
    name="source_order",
    description="Run sourcing engine for an order.",
    input_schema={
        "type": "object",
        "properties": {"order_id": {"type": "string"}, "strategy": {"type": "string"}},
        "required": ["order_id"],
    },
    permission_scope="sourcing:write",
    has_side_effects=True,
    requires_confirmation=True,
)
async def source_order(tool_input: dict, db=None) -> dict:
    service = SourcingService(db)
    try:
        return await service.source_order_by_id(
            UUID(tool_input["order_id"]),
            strategy_name=tool_input.get("strategy"),
        )
    except DomainError as exc:
        err = http_exception_from_domain(exc)
        return {"error": err.detail}
