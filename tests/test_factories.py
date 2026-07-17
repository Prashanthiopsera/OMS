"""Unit tests for entity factory fixtures (WO-027)."""
from decimal import Decimal

from tests.factories import (
    make_brand,
    make_fulfillment_node,
    make_inventory_item,
    make_order,
    make_order_item,
    make_user,
)


def test_user_factory_produces_valid_user():
    user = make_user(email="factory-user@example.com", is_superadmin=True)
    assert user.email == "factory-user@example.com"
    assert user.is_superadmin is True
    assert user.hashed_password
    assert user.is_active is True


def test_brand_factory_produces_valid_brand():
    brand = make_brand(slug="acme-test", name="Acme Test")
    assert brand.slug == "acme-test"
    assert brand.name == "Acme Test"
    assert brand.is_active is True


def test_node_factory_produces_valid_node():
    node = make_fulfillment_node(code="NYC-01", latitude=40.0, longitude=-73.0)
    assert node.code == "NYC-01"
    assert node.latitude == 40.0
    assert node.longitude == -73.0


def test_inventory_factory_produces_valid_item():
    node_id = make_fulfillment_node().id
    item = make_inventory_item(node_id=node_id, sku="WIDGET-1", quantity_on_hand=25)
    assert item.node_id == node_id
    assert item.sku == "WIDGET-1"
    assert item.quantity_on_hand == 25
    assert item.quantity_available == 25


def test_order_factory_produces_valid_order_with_line_items():
    order = make_order(customer_email="buyer@example.com", total_amount=Decimal("150.00"))
    assert order.customer_email == "buyer@example.com"
    assert order.total_amount == Decimal("150.00")
    assert len(order.line_items) == 1
    assert order.line_items[0].quantity >= 1


def test_order_item_factory_accepts_overrides():
    item = make_order_item(sku="PART-A", quantity=3, unit_price=Decimal("10.00"))
    assert item.sku == "PART-A"
    assert item.quantity == 3
    assert item.total_price == Decimal("30.00")
