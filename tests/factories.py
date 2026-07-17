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

import hashlib
import secrets

from app.core.security import hash_password
from app.models.postgres.api_key_models import ApiKey
from app.models.postgres.auth_models import User
from app.models.postgres.brand_models import Brand, BrandTenantMode, InventoryMode
from app.models.postgres.inventory_models import InventoryItem
from app.models.postgres.node_models import FulfillmentNode, NodeStatus, NodeType
from app.models.postgres.org_models import (
    Environment,
    EnvironmentStatus,
    EnvironmentType,
    Organization,
)
from app.models.postgres.user_brand_role_models import UserBrandRole
from app.models.postgres.order_models import (
    FulfillmentType,
    Order,
    OrderChannel,
    OrderItem,
    OrderItemStatus,
    OrderStatus,
)


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


def make_organization(**overrides: Any) -> Organization:
    slug = overrides.pop("slug", f"org-{uuid.uuid4().hex[:6]}")
    defaults: dict[str, Any] = {
        "slug": slug,
        "name": "Test Organization",
        "is_active": True,
    }
    defaults.update(overrides)
    return Organization(**defaults)


def make_environment(**overrides: Any) -> Environment:
    slug = overrides.pop("slug", f"env-{uuid.uuid4().hex[:6]}")
    db_name = overrides.pop("db_name", f"oms_test_{uuid.uuid4().hex[:8]}")
    defaults: dict[str, Any] = {
        "name": "Test Environment",
        "slug": slug,
        "env_type": EnvironmentType.DEV,
        "status": EnvironmentStatus.ACTIVE,
        "db_name": db_name,
        "mongo_events_db": f"{db_name}_events",
        "mongo_ai_db": f"{db_name}_ai",
        "es_index_prefix": db_name,
        "is_default": False,
    }
    defaults.update(overrides)
    return Environment(**defaults)


def make_user_brand_role(**overrides: Any) -> UserBrandRole:
    defaults: dict[str, Any] = {
        "role": "OPERATOR",
    }
    defaults.update(overrides)
    return UserBrandRole(**defaults)


def make_api_key(**overrides: Any) -> tuple[ApiKey, str]:
    """Return (ApiKey model, raw_key) — raw key is only available at creation time."""
    raw_key = overrides.pop("raw_key", f"kr_{secrets.token_urlsafe(32)}")
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    defaults: dict[str, Any] = {
        "key_prefix": raw_key[:12],
        "key_hash": key_hash,
        "name": "Test API Key",
        "scopes": ["orders:read"],
        "is_active": True,
    }
    defaults.update(overrides)
    return ApiKey(**defaults), raw_key


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
