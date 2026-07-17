"""Order lifecycle behavioral tests (WO-031 / WO-032).

Covers order creation, financial calculations, valid/invalid status transitions,
cancellation rules, line-item persistence, and background sourcing enqueue.
"""
from __future__ import annotations

import re
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.postgres.lifecycle_models import Lifecycle
from app.models.postgres.order_models import Order, OrderStatus
from app.models.postgres.org_models import Environment, EnvironmentStatus
from app.routers.orders import _generate_order_number
from tests.conftest import auth_headers, env_headers, issue_token, persist_user
from tests.factories import make_lifecycle

pytestmark = pytest.mark.requires_db


@pytest_asyncio.fixture
async def default_environment(behavioral_session: AsyncSession):
    result = await behavioral_session.execute(
        select(Environment)
        .where(Environment.is_default.is_(True))
        .where(Environment.status == EnvironmentStatus.ACTIVE)
        .limit(1)
    )
    env = result.scalar_one_or_none()
    if env is None:
        pytest.skip("No default ACTIVE environment in database — run seed/migrations")
    return env


@pytest_asyncio.fixture
async def superadmin_headers(behavioral_session: AsyncSession, user_factory):
    user = await persist_user(
        behavioral_session,
        user_factory,
        is_superadmin=True,
        platform_role="SUPERADMIN",
    )
    token = await issue_token(user)
    return auth_headers(token)


def _order_payload(**overrides) -> dict:
    payload = {
        "channel": "WEB",
        "fulfillment_type": "SHIP_TO_HOME",
        "customer_email": "buyer@example.com",
        "line_items": [
            {
                "sku": "SKU-LIFE-001",
                "product_name": "Lifecycle Widget",
                "quantity": 2,
                "unit_price": "10.00",
                "discount_amount": "1.00",
                "tax_amount": "0.50",
            }
        ],
        "shipping_address": {
            "address1": "123 Main St",
            "city": "New York",
            "state": "NY",
            "postal_code": "10001",
            "country": "US",
            "latitude": 40.7128,
            "longitude": -74.0060,
        },
        "shipping_amount": "5.00",
        "discount_amount": "2.00",
    }
    payload.update(overrides)
    return payload


async def _seed_lifecycle(session: AsyncSession) -> Lifecycle:
    """Insert a scoped lifecycle governing SHIP_TO_HOME transitions."""
    lc = make_lifecycle(
        name="Behavioral Test Shipping Lifecycle",
        fulfillment_types=["SHIP_TO_HOME"],
        channels=["WEB"],
    )
    session.add(lc)
    await session.flush()
    return lc


@pytest.mark.asyncio
async def test_order_number_format():
    order_number = _generate_order_number()
    assert re.match(r"^ORD-\d{8}-[A-Z0-9]{6}$", order_number)


@pytest.mark.asyncio
async def test_create_order_financial_calculations(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    superadmin_headers: dict,
    default_environment,
):
    await _seed_lifecycle(behavioral_session)
    response = await behavioral_client.post(
        "/orders/",
        json=_order_payload(),
        headers={**superadmin_headers, **env_headers(default_environment.id)},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # subtotal = (10*2 - 1) = 19; tax = 0.50*2 = 1; total = 19 + 1 + 5 - 2 = 23
    assert Decimal(str(body["subtotal"])) == Decimal("19.00")
    assert Decimal(str(body["tax_amount"])) == Decimal("1.00")
    assert Decimal(str(body["total_amount"])) == Decimal("23.00")


@pytest.mark.asyncio
async def test_create_order_number_format(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    superadmin_headers: dict,
    default_environment,
):
    await _seed_lifecycle(behavioral_session)
    response = await behavioral_client.post(
        "/orders/",
        json=_order_payload(),
        headers={**superadmin_headers, **env_headers(default_environment.id)},
    )
    assert response.status_code == 201
    assert re.match(r"^ORD-\d{8}-[A-Z0-9]{6}$", response.json()["order_number"])


@pytest.mark.asyncio
async def test_create_order_persists_line_items(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    superadmin_headers: dict,
    default_environment,
):
    await _seed_lifecycle(behavioral_session)
    response = await behavioral_client.post(
        "/orders/",
        json=_order_payload(),
        headers={**superadmin_headers, **env_headers(default_environment.id)},
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["line_items"]) == 1
    item = body["line_items"][0]
    assert item["sku"] == "SKU-LIFE-001"
    assert item["quantity"] == 2
    assert Decimal(str(item["unit_price"])) == Decimal("10.00")
    assert item["product_name"] == "Lifecycle Widget"


@pytest.mark.asyncio
async def test_create_order_queues_sourcing_task(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    superadmin_headers: dict,
    default_environment,
):
    await _seed_lifecycle(behavioral_session)
    mock_celery = MagicMock()
    with patch("app.workers.celery_app.celery_app", mock_celery):
        response = await behavioral_client.post(
            "/orders/",
            json=_order_payload(),
            headers={**superadmin_headers, **env_headers(default_environment.id)},
        )
    assert response.status_code == 201
    order_id = str(response.json()["id"])
    sourcing_calls = [
        c for c in mock_celery.send_task.call_args_list
        if c.args and c.args[0] == "app.workers.sourcing.source_order"
    ]
    assert len(sourcing_calls) >= 1
    task_args = sourcing_calls[0].kwargs.get("args", [])
    assert task_args[0] == order_id


@pytest.mark.asyncio
async def test_valid_transition_confirmed_to_sourcing(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    superadmin_headers: dict,
    default_environment,
):
    lc = await _seed_lifecycle(behavioral_session)
    create_resp = await behavioral_client.post(
        "/orders/",
        json=_order_payload(),
        headers={**superadmin_headers, **env_headers(default_environment.id)},
    )
    order_id = create_resp.json()["id"]

    response = await behavioral_client.patch(
        f"/orders/{order_id}/status",
        json={"status": "SOURCING"},
        headers={**superadmin_headers, **env_headers(default_environment.id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "SOURCING"

    # lifecycle was attached at creation
    result = await behavioral_session.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one()
    assert str(order.lifecycle_id) == str(lc.id)


@pytest.mark.asyncio
async def test_invalid_transition_confirmed_to_delivered_rejected(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    superadmin_headers: dict,
    default_environment,
):
    await _seed_lifecycle(behavioral_session)
    create_resp = await behavioral_client.post(
        "/orders/",
        json=_order_payload(),
        headers={**superadmin_headers, **env_headers(default_environment.id)},
    )
    order_id = create_resp.json()["id"]

    response = await behavioral_client.patch(
        f"/orders/{order_id}/status",
        json={"status": "DELIVERED"},
        headers={**superadmin_headers, **env_headers(default_environment.id)},
    )
    assert response.status_code == 422
    assert "does not allow transition" in response.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_from_confirmed_succeeds(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    superadmin_headers: dict,
    default_environment,
):
    await _seed_lifecycle(behavioral_session)
    with patch("app.workers.celery_app.celery_app", MagicMock()):
        create_resp = await behavioral_client.post(
            "/orders/",
            json=_order_payload(),
            headers={**superadmin_headers, **env_headers(default_environment.id)},
        )
    order_id = create_resp.json()["id"]

    with patch("app.workers.celery_app.celery_app", MagicMock()):
        response = await behavioral_client.post(
            f"/orders/{order_id}/cancel",
            json={"reason": "Customer request", "notify_customer": False},
            headers={**superadmin_headers, **env_headers(default_environment.id)},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_cancel_from_shipped_rejected(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    superadmin_headers: dict,
    default_environment,
    order_factory,
):
    await _seed_lifecycle(behavioral_session)
    order = order_factory(status=OrderStatus.SHIPPED)
    behavioral_session.add(order)
    await behavioral_session.flush()

    response = await behavioral_client.post(
        f"/orders/{order.id}/cancel",
        json={"reason": "Too late", "notify_customer": False},
        headers={**superadmin_headers, **env_headers(default_environment.id)},
    )
    assert response.status_code == 400
    assert "Cannot cancel" in response.json()["detail"]


@pytest.mark.asyncio
async def test_full_happy_path_transitions(
    behavioral_client: AsyncClient,
    behavioral_session: AsyncSession,
    superadmin_headers: dict,
    default_environment,
):
    """Walk CONFIRMED → SOURCING → SOURCED → PICKING → PACKING → READY_TO_SHIP → SHIPPED → DELIVERED."""
    await _seed_lifecycle(behavioral_session)
    with patch("app.workers.celery_app.celery_app", MagicMock()):
        create_resp = await behavioral_client.post(
            "/orders/",
            json=_order_payload(),
            headers={**superadmin_headers, **env_headers(default_environment.id)},
        )
    order_id = create_resp.json()["id"]
    headers = {**superadmin_headers, **env_headers(default_environment.id)}

    for status in (
        "SOURCING",
        "SOURCED",
        "PICKING",
        "PACKING",
        "READY_TO_SHIP",
        "SHIPPED",
        "DELIVERED",
    ):
        with patch("app.workers.celery_app.celery_app", MagicMock()):
            resp = await behavioral_client.patch(
                f"/orders/{order_id}/status",
                json={"status": status},
                headers=headers,
            )
        assert resp.status_code == 200, f"Transition to {status} failed: {resp.text}"
        assert resp.json()["status"] == status
