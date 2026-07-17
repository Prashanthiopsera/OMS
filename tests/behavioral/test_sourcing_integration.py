"""Sourcing engine integration tests (WO-031 / WO-033).

Full pipeline tests from order → SourcingEngine.source_order → allocations.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.postgres.node_models import NodeType
from app.models.postgres.order_models import Order, OrderStatus
from app.models.postgres.sourcing_rule_models import SourcingStrategy
from app.services.sourcing_engine import SourcingEngine
from tests.factories import (
    make_fulfillment_node,
    make_inventory_item,
    make_order,
    make_order_item,
    make_sourcing_rule,
)

pytestmark = pytest.mark.requires_db

# Customer location: Manhattan
CUST_LAT, CUST_LON = 40.7128, -74.0060


async def _persist_order_with_items(session: AsyncSession, **order_kwargs) -> Order:
    order = make_order(
        status=OrderStatus.CONFIRMED,
        shipping_latitude=CUST_LAT,
        shipping_longitude=CUST_LON,
        **order_kwargs,
    )
    session.add(order)
    await session.flush()
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.line_items))
        .where(Order.id == order.id)
    )
    return result.scalar_one()


async def _setup_nodes(session: AsyncSession, sku: str = "SKU-SRC-001"):
    """Near node (5 mi north), far node (200 mi south), store node (10 mi east)."""
    near = make_fulfillment_node(
        code="SRC-NEAR",
        latitude=CUST_LAT + 0.07,
        longitude=CUST_LON,
        shipping_cost_multiplier=1.0,
    )
    far = make_fulfillment_node(
        code="SRC-FAR",
        latitude=CUST_LAT - 3.0,
        longitude=CUST_LON,
        shipping_cost_multiplier=0.5,
    )
    store = make_fulfillment_node(
        code="SRC-STORE",
        node_type=NodeType.RETAIL_STORE,
        latitude=CUST_LAT,
        longitude=CUST_LON + 0.15,
        shipping_cost_multiplier=2.0,
    )
    session.add_all([near, far, store])
    await session.flush()

    for node, qty in [(near, 50), (far, 50), (store, 50)]:
        session.add(make_inventory_item(node_id=node.id, sku=sku, quantity_on_hand=qty))
    await session.flush()
    return near, far, store


@pytest.mark.asyncio
async def test_distance_optimal_selects_nearest_node(behavioral_session: AsyncSession):
    near, _far, _store = await _setup_nodes(behavioral_session)
    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku="SKU-SRC-001", quantity=1)],
    )

    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.DISTANCE_OPTIMAL, skip_rule=True,
    )

    assert len(result.allocations) == 1
    assert result.allocations[0]["node_id"] == str(near.id)


@pytest.mark.asyncio
async def test_distance_optimal_equal_distance_inventory_tiebreaker(behavioral_session: AsyncSession):
    node_low = make_fulfillment_node(
        code="EQ-LOW",
        latitude=CUST_LAT + 0.05,
        longitude=CUST_LON,
    )
    node_high = make_fulfillment_node(
        code="EQ-HIGH",
        latitude=CUST_LAT + 0.05,
        longitude=CUST_LON + 0.001,
    )
    behavioral_session.add_all([node_low, node_high])
    await behavioral_session.flush()

    sku = "SKU-TIE"
    behavioral_session.add(make_inventory_item(node_id=node_low.id, sku=sku, quantity_on_hand=5))
    behavioral_session.add(make_inventory_item(node_id=node_high.id, sku=sku, quantity_on_hand=100))
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku=sku, quantity=1)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.DISTANCE_OPTIMAL, skip_rule=True,
    )
    assert result.allocations[0]["node_code"] == "EQ-HIGH"


@pytest.mark.asyncio
async def test_cost_optimal_selects_lowest_cost_node(behavioral_session: AsyncSession):
    expensive = make_fulfillment_node(
        code="COST-HIGH",
        latitude=CUST_LAT + 0.01,
        longitude=CUST_LON,
        shipping_cost_multiplier=100.0,
    )
    cheap = make_fulfillment_node(
        code="COST-LOW",
        latitude=CUST_LAT + 0.01,
        longitude=CUST_LON + 0.001,
        shipping_cost_multiplier=0.01,
    )
    behavioral_session.add_all([expensive, cheap])
    await behavioral_session.flush()

    sku = "SKU-COST"
    behavioral_session.add(make_inventory_item(node_id=expensive.id, sku=sku, quantity_on_hand=10))
    behavioral_session.add(make_inventory_item(node_id=cheap.id, sku=sku, quantity_on_hand=10))
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku=sku, quantity=1)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.COST_OPTIMAL, skip_rule=True,
    )
    assert result.allocations[0]["node_code"] == "COST-LOW"


@pytest.mark.asyncio
async def test_store_nearest_filters_to_stores_only(behavioral_session: AsyncSession):
    _near, _far, store = await _setup_nodes(behavioral_session)
    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku="SKU-SRC-001", quantity=1)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.STORE_NEAREST, skip_rule=True,
    )
    assert len(result.allocations) == 1
    assert result.allocations[0]["node_id"] == str(store.id)


@pytest.mark.asyncio
async def test_store_nearest_no_store_inventory_returns_empty(behavioral_session: AsyncSession):
    dc = make_fulfillment_node(code="DC-ONLY", latitude=CUST_LAT, longitude=CUST_LON)
    behavioral_session.add(dc)
    await behavioral_session.flush()
    behavioral_session.add(make_inventory_item(node_id=dc.id, sku="SKU-NOSTORE", quantity_on_hand=10))
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku="SKU-NOSTORE", quantity=1)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.STORE_NEAREST, skip_rule=True,
    )
    assert result.allocations == []


@pytest.mark.asyncio
async def test_inventory_reservation_selects_deepest_stock(behavioral_session: AsyncSession):
    low = make_fulfillment_node(code="INV-LOW", latitude=CUST_LAT, longitude=CUST_LON)
    high = make_fulfillment_node(code="INV-HIGH", latitude=CUST_LAT + 0.01, longitude=CUST_LON)
    behavioral_session.add_all([low, high])
    await behavioral_session.flush()

    sku = "SKU-DEEP"
    behavioral_session.add(make_inventory_item(node_id=low.id, sku=sku, quantity_on_hand=5))
    behavioral_session.add(make_inventory_item(node_id=high.id, sku=sku, quantity_on_hand=200))
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku=sku, quantity=3)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.INVENTORY_RESERVATION, skip_rule=True,
    )
    assert result.allocations[0]["node_code"] == "INV-HIGH"


@pytest.mark.asyncio
async def test_inventory_reservation_partial_availability_backorders(behavioral_session: AsyncSession):
    node = make_fulfillment_node(code="INV-PARTIAL", latitude=CUST_LAT, longitude=CUST_LON)
    behavioral_session.add(node)
    await behavioral_session.flush()
    sku = "SKU-PARTIAL"
    behavioral_session.add(make_inventory_item(node_id=node.id, sku=sku, quantity_on_hand=2))
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku=sku, quantity=5)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.INVENTORY_RESERVATION, skip_rule=True,
    )
    assert len(result.allocations) == 1
    assert result.allocations[0]["quantity"] == 2

    await behavioral_session.refresh(order, ["line_items"])
    assert order.status == OrderStatus.BACKORDERED


@pytest.mark.asyncio
async def test_least_cost_split_across_multiple_nodes(behavioral_session: AsyncSession):
    node_a = make_fulfillment_node(code="SPLIT-A", latitude=CUST_LAT, longitude=CUST_LON)
    node_b = make_fulfillment_node(code="SPLIT-B", latitude=CUST_LAT + 0.02, longitude=CUST_LON)
    behavioral_session.add_all([node_a, node_b])
    await behavioral_session.flush()

    behavioral_session.add(make_inventory_item(node_id=node_a.id, sku="SKU-X", quantity_on_hand=10))
    behavioral_session.add(make_inventory_item(node_id=node_a.id, sku="SKU-Y", quantity_on_hand=0))
    behavioral_session.add(make_inventory_item(node_id=node_b.id, sku="SKU-X", quantity_on_hand=0))
    behavioral_session.add(make_inventory_item(node_id=node_b.id, sku="SKU-Y", quantity_on_hand=10))
    await behavioral_session.flush()

    rule = make_sourcing_rule(
        strategy=SourcingStrategy.LEAST_COST_SPLIT,
        max_split_nodes=3,
    )
    behavioral_session.add(rule)
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[
            make_order_item(sku="SKU-X", quantity=3),
            make_order_item(sku="SKU-Y", quantity=2),
        ],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.LEAST_COST_SPLIT, skip_rule=False,
    )
    node_codes = {a["node_code"] for a in result.allocations}
    assert node_codes == {"SPLIT-A", "SPLIT-B"}
    assert result.total_split_nodes == 2


@pytest.mark.asyncio
async def test_least_cost_split_respects_max_split_nodes(behavioral_session: AsyncSession):
    nodes = []
    for i in range(4):
        n = make_fulfillment_node(
            code=f"MAX-{i}",
            latitude=CUST_LAT + i * 0.01,
            longitude=CUST_LON,
        )
        nodes.append(n)
        behavioral_session.add(n)
    await behavioral_session.flush()

    sku = "SKU-SPLIT-LIM"
    for n in nodes:
        behavioral_session.add(
            make_inventory_item(node_id=n.id, sku=sku, quantity_on_hand=1)
        )
    await behavioral_session.flush()

    rule = make_sourcing_rule(
        strategy=SourcingStrategy.LEAST_COST_SPLIT,
        max_split_nodes=2,
    )
    behavioral_session.add(rule)
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku=sku, quantity=4)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.LEAST_COST_SPLIT, skip_rule=False,
    )
    unique_nodes = {a["node_id"] for a in result.allocations}
    assert len(unique_nodes) <= 2


@pytest.mark.asyncio
async def test_inactive_node_excluded(behavioral_session: AsyncSession):
    from app.models.postgres.node_models import NodeStatus

    active = make_fulfillment_node(code="ACTIVE-N", latitude=CUST_LAT, longitude=CUST_LON)
    inactive = make_fulfillment_node(
        code="INACTIVE-N",
        latitude=CUST_LAT + 0.001,
        longitude=CUST_LON,
        status=NodeStatus.INACTIVE,
    )
    behavioral_session.add_all([active, inactive])
    await behavioral_session.flush()

    sku = "SKU-ACTIVE"
    behavioral_session.add(make_inventory_item(node_id=active.id, sku=sku, quantity_on_hand=10))
    behavioral_session.add(make_inventory_item(node_id=inactive.id, sku=sku, quantity_on_hand=100))
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku=sku, quantity=1)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.INVENTORY_RESERVATION, skip_rule=True,
    )
    assert result.allocations[0]["node_code"] == "ACTIVE-N"


@pytest.mark.asyncio
async def test_at_capacity_node_excluded(behavioral_session: AsyncSession):
    full = make_fulfillment_node(
        code="FULL-N",
        latitude=CUST_LAT,
        longitude=CUST_LON,
        daily_order_capacity=10,
        current_daily_orders=10,
    )
    available = make_fulfillment_node(
        code="OPEN-N",
        latitude=CUST_LAT + 0.02,
        longitude=CUST_LON,
    )
    behavioral_session.add_all([full, available])
    await behavioral_session.flush()

    sku = "SKU-CAP"
    behavioral_session.add(make_inventory_item(node_id=full.id, sku=sku, quantity_on_hand=50))
    behavioral_session.add(make_inventory_item(node_id=available.id, sku=sku, quantity_on_hand=50))
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku=sku, quantity=1)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.DISTANCE_OPTIMAL, skip_rule=True,
    )
    assert result.allocations[0]["node_code"] == "OPEN-N"


@pytest.mark.asyncio
async def test_sourcing_rule_cost_weights_applied(behavioral_session: AsyncSession):
    expensive_near = make_fulfillment_node(
        code="WEIGHT-NEAR",
        latitude=CUST_LAT + 0.01,
        longitude=CUST_LON,
        shipping_cost_multiplier=10.0,
    )
    cheap_far = make_fulfillment_node(
        code="WEIGHT-FAR",
        latitude=CUST_LAT - 2.0,
        longitude=CUST_LON,
        shipping_cost_multiplier=0.1,
    )
    behavioral_session.add_all([expensive_near, cheap_far])
    await behavioral_session.flush()

    sku = "SKU-WEIGHT"
    behavioral_session.add(make_inventory_item(node_id=expensive_near.id, sku=sku, quantity_on_hand=10))
    behavioral_session.add(make_inventory_item(node_id=cheap_far.id, sku=sku, quantity_on_hand=10))
    await behavioral_session.flush()

    rule = make_sourcing_rule(
        strategy=SourcingStrategy.COST_OPTIMAL,
        cost_weight=0.9,
        distance_weight=0.1,
        conditions=[{"field": "channel", "operator": "EQUALS", "value": "WEB"}],
    )
    behavioral_session.add(rule)
    await behavioral_session.flush()

    order = await _persist_order_with_items(
        behavioral_session,
        line_items=[make_order_item(sku=sku, quantity=1)],
    )
    result = await SourcingEngine(behavioral_session).source_order(
        order, force_strategy=SourcingStrategy.COST_OPTIMAL, skip_rule=False,
    )
    assert result.allocations[0]["node_code"] == "WEIGHT-FAR"
    assert result.rule_applied == rule.name
