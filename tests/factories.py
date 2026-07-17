"""Deterministic test entity factories (WO-027)."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

# Ensure SQLAlchemy mappers for all related models are configured before use.
from app.models.postgres import (  # noqa: F401
    ai_models,
    auth_models,
    b2b_models,
    brand_models,
    connector_models,
    customer_models,
    inventory_models,
    lifecycle_models,
    node_models,
    order_models,
    org_models,
    return_models,
    sourcing_rule_models,
    user_brand_role_models,
)

from app.core.security import hash_password
from app.models.postgres.auth_models import User
from app.models.postgres.brand_models import Brand, BrandTenantMode, InventoryMode
from app.models.postgres.inventory_models import InventoryItem
from app.models.postgres.node_models import FulfillmentNode, NodeStatus, NodeType
from app.models.postgres.lifecycle_models import Lifecycle, LifecycleStep
from app.models.postgres.order_models import (
    FulfillmentType,
    Order,
    OrderChannel,
    OrderItem,
    OrderItemStatus,
    OrderStatus,
)
from app.models.postgres.sourcing_rule_models import SourcingRule, SourcingStrategy


def make_user(**overrides: Any) -> User:
    defaults: dict[str, Any] = {
        "email": f"user-{uuid.uuid4().hex[:8]}@example.com",
        "full_name": "Test User",
        "hashed_password": hash_password("test-password-123"),
        "is_active": True,
        "is_superadmin": False,
    }
    defaults.update(overrides)
    return User(**defaults)


def make_brand(**overrides: Any) -> Brand:
    slug = overrides.pop("slug", f"brand-{uuid.uuid4().hex[:6]}")
    defaults: dict[str, Any] = {
        "slug": slug,
        "name": "Test Brand",
        "tenant_mode": BrandTenantMode.HYBRID,
        "is_active": True,
        "inventory_mode": InventoryMode.SHARED.value,
    }
    defaults.update(overrides)
    return Brand(**defaults)


def make_fulfillment_node(**overrides: Any) -> FulfillmentNode:
    code = overrides.pop("code", f"NODE-{uuid.uuid4().hex[:6].upper()}")
    defaults: dict[str, Any] = {
        "code": code,
        "name": "Test Fulfillment Node",
        "node_type": NodeType.DISTRIBUTION_CENTER,
        "status": NodeStatus.ACTIVE,
        "latitude": 40.7128,
        "longitude": -74.0060,
        "city": "New York",
        "state": "NY",
        "country": "US",
        "can_ship": True,
    }
    defaults.update(overrides)
    return FulfillmentNode(**defaults)


def make_inventory_item(**overrides: Any) -> InventoryItem:
    qty = overrides.pop("quantity_on_hand", 100)
    reserved = overrides.pop("quantity_reserved", 0)
    defaults: dict[str, Any] = {
        "sku": overrides.pop("sku", f"SKU-{uuid.uuid4().hex[:6].upper()}"),
        "product_name": "Test Product",
        "quantity_on_hand": qty,
        "quantity_reserved": reserved,
        "quantity_available": qty - reserved,
        "is_active": True,
    }
    defaults.update(overrides)
    return InventoryItem(**defaults)


def make_order_item(**overrides: Any) -> OrderItem:
    qty = overrides.pop("quantity", 1)
    unit_price = overrides.pop("unit_price", Decimal("50.00"))
    defaults: dict[str, Any] = {
        "sku": overrides.pop("sku", f"SKU-{uuid.uuid4().hex[:6].upper()}"),
        "product_name": "Test Product",
        "quantity": qty,
        "quantity_pending": qty,
        "status": OrderItemStatus.PENDING,
        "unit_price": unit_price,
        "total_price": unit_price * qty,
    }
    defaults.update(overrides)
    return OrderItem(**defaults)


def make_lifecycle(**overrides: Any) -> Lifecycle:
    steps_data = overrides.pop("steps", None)
    defaults: dict[str, Any] = {
        "name": "Test Lifecycle",
        "pipeline_type": "ORDER",
        "fulfillment_types": ["SHIP_TO_HOME"],
        "channels": [],
        "is_active": True,
        "is_default": False,
    }
    defaults.update(overrides)
    lc = Lifecycle(**defaults)
    if steps_data is None:
        steps_data = _default_shipping_lifecycle_steps()
    lc.steps = [make_lifecycle_step(lifecycle=lc, **s) for s in steps_data]
    return lc


def _default_shipping_lifecycle_steps() -> list[dict[str, Any]]:
    return [
        {"status": "PENDING", "label": "Pending", "step_order": 0, "allowed_next_statuses": ["CONFIRMED", "CANCELLED"]},
        {"status": "CONFIRMED", "label": "Confirmed", "step_order": 1, "allowed_next_statuses": ["SOURCING", "CANCELLED"]},
        {"status": "SOURCING", "label": "Sourcing", "step_order": 2, "allowed_next_statuses": ["SOURCED", "BACKORDERED"]},
        {"status": "SOURCED", "label": "Sourced", "step_order": 3, "allowed_next_statuses": ["PICKING"]},
        {"status": "BACKORDERED", "label": "Backordered", "step_order": 4, "allowed_next_statuses": ["SOURCING", "CANCELLED"]},
        {"status": "PICKING", "label": "Picking", "step_order": 5, "allowed_next_statuses": ["PACKING"]},
        {"status": "PACKING", "label": "Packing", "step_order": 6, "allowed_next_statuses": ["READY_TO_SHIP"]},
        {"status": "READY_TO_SHIP", "label": "Ready to Ship", "step_order": 7, "allowed_next_statuses": ["SHIPPED"], "action_type": "book_shipment"},
        {"status": "SHIPPED", "label": "Shipped", "step_order": 8, "allowed_next_statuses": ["DELIVERED", "OUT_FOR_DELIVERY"]},
        {"status": "OUT_FOR_DELIVERY", "label": "Out for Delivery", "step_order": 9, "allowed_next_statuses": ["DELIVERED"]},
        {"status": "DELIVERED", "label": "Delivered", "step_order": 10, "allowed_next_statuses": []},
        {"status": "CANCELLED", "label": "Cancelled", "step_order": 11, "allowed_next_statuses": []},
    ]


def make_lifecycle_step(**overrides: Any) -> LifecycleStep:
    defaults: dict[str, Any] = {
        "status": "CONFIRMED",
        "label": "Confirmed",
        "step_order": 0,
        "allowed_next_statuses": [],
    }
    defaults.update(overrides)
    return LifecycleStep(**defaults)


def make_sourcing_rule(**overrides: Any) -> SourcingRule:
    defaults: dict[str, Any] = {
        "name": "Test Sourcing Rule",
        "priority": 10,
        "is_active": True,
        "strategy": SourcingStrategy.DISTANCE_OPTIMAL,
        "conditions": [],
        "max_split_nodes": 3,
        "cost_weight": 0.5,
        "distance_weight": 0.5,
    }
    defaults.update(overrides)
    return SourcingRule(**defaults)


def make_order(**overrides: Any) -> Order:
    line_items = overrides.pop("line_items", None)
    defaults: dict[str, Any] = {
        "order_number": overrides.pop("order_number", f"ORD-{uuid.uuid4().hex[:8].upper()}"),
        "channel": OrderChannel.WEB,
        "fulfillment_type": FulfillmentType.SHIP_TO_HOME,
        "status": OrderStatus.CONFIRMED,
        "customer_email": "customer@example.com",
        "customer_name": "Test Customer",
        "total_amount": Decimal("100.00"),
        "subtotal": Decimal("100.00"),
        "currency": "USD",
    }
    defaults.update(overrides)
    order = Order(**defaults)
    if line_items is None:
        line_items = [make_order_item()]
    for item in line_items:
        item.order = order
    order.line_items = line_items
    return order
