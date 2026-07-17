"""Unit tests for OrderService (WO-035 / WO-038 / WO-039)."""
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.postgres.order_models import OrderStatus
from app.schemas.orders import CancelOrderRequest, OrderCreate, OrderItemCreate, OrderStatusUpdate
from app.services.exceptions import InvalidStatusTransitionError, OrderNotFoundError
from app.services.order_service import OrderService, calculate_order_totals, generate_order_number
from tests.conftest import persist_user
from tests.factories import make_lifecycle, make_order

pytestmark = pytest.mark.requires_db


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


@pytest.mark.asyncio
async def test_get_order_not_found(test_session):
    service = OrderService(test_session)
    with pytest.raises(OrderNotFoundError):
        await service.get_order(uuid4())


@pytest.mark.asyncio
async def test_update_status_invalid_transition(test_session, order_factory):
    lifecycle = make_lifecycle()
    test_session.add(lifecycle)
    await test_session.flush()

    order = make_order(status=OrderStatus.CONFIRMED, lifecycle_id=lifecycle.id)
    test_session.add(order)
    await test_session.flush()

    service = OrderService(test_session)
    with pytest.raises(InvalidStatusTransitionError):
        await service.update_status(
            order.id,
            OrderStatusUpdate(status=OrderStatus.DELIVERED),
        )


@pytest.mark.asyncio
async def test_cancel_order_from_confirmed(test_session, order_factory):
    order = make_order(status=OrderStatus.CONFIRMED)
    test_session.add(order)
    await test_session.flush()

    service = OrderService(test_session)
    cancelled = await service.cancel_order(
        order.id,
        CancelOrderRequest(reason="Customer request"),
    )
    assert cancelled.status == OrderStatus.CANCELLED
    assert "Customer request" in cancelled.notes
