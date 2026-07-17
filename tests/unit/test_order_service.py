"""Unit tests for OrderService (WO-035 / WO-038)."""
from decimal import Decimal

from app.schemas.orders import OrderCreate, OrderItemCreate
from app.services.order_service import calculate_order_totals, generate_order_number


def test_generate_order_number_format():
    assert generate_order_number().startswith("ORD-")


def test_calculate_order_totals():
    payload = OrderCreate(
        channel="WEB",
        fulfillment_type="SHIP_TO_HOME",
        customer_email="buyer@example.com",
        line_items=[
            OrderItemCreate(
                sku="A",
                product_name="Widget",
                quantity=2,
                unit_price=Decimal("10.00"),
                discount_amount=Decimal("1.00"),
                tax_amount=Decimal("0.50"),
            )
        ],
        shipping_amount=Decimal("5.00"),
        discount_amount=Decimal("2.00"),
    )
    subtotal, tax, total = calculate_order_totals(payload)
    assert subtotal == Decimal("19.00")
    assert tax == Decimal("1.00")
    assert total == Decimal("23.00")
