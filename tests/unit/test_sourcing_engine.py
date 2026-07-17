"""Sourcing engine unit tests (WO-033).

Pure-function tests for haversine distance, node scoring, condition evaluation,
split allocation, and node filtering — no database required.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.postgres.node_models import NodeStatus, NodeType
from app.models.postgres.sourcing_rule_models import ConditionOperator, SourcingStrategy
from app.services.sourcing_engine import (
    NodeCandidate,
    _compute_split_allocations,
    _evaluate_condition,
    _filter_nodes,
    _score_nodes,
    haversine_km,
    haversine_miles,
)


def _make_node(**kwargs):
    defaults = {
        "id": "node-1",
        "code": "NYC-DC",
        "name": "NYC DC",
        "node_type": NodeType.DISTRIBUTION_CENTER,
        "status": NodeStatus.ACTIVE,
        "latitude": 40.7128,
        "longitude": -74.0060,
        "can_ship": True,
        "can_pickup": False,
        "can_curbside": False,
        "can_same_day": False,
        "daily_order_capacity": 500,
        "current_daily_orders": 0,
        "shipping_cost_multiplier": 1.0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_order(**kwargs):
    defaults = {
        "channel": SimpleNamespace(value="WEB"),
        "fulfillment_type": SimpleNamespace(value="SHIP_TO_HOME"),
        "status": SimpleNamespace(value="CONFIRMED"),
        "total_amount": 100.0,
        "currency": "USD",
        "customer_email": "buyer@example.com",
        "shipping_country": "US",
        "shipping_state": "NY",
        "line_items": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_item(sku: str, qty: int):
    return SimpleNamespace(sku=sku, quantity=qty, quantity_backordered=0)


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_km(40.0, -74.0, 40.0, -74.0) == 0.0
        assert haversine_miles(40.0, -74.0, 40.0, -74.0) == 0.0

    def test_known_distance_nyc_to_philly(self):
        # NYC (40.7128, -74.0060) to Philadelphia (39.9526, -75.1652) ≈ 80 miles
        miles = haversine_miles(40.7128, -74.0060, 39.9526, -75.1652)
        assert 75 < miles < 90


class TestConditionEvaluation:
    def test_equals_channel(self):
        order = _make_order()
        assert _evaluate_condition(order, {
            "field": "channel", "operator": ConditionOperator.EQUALS, "value": "WEB",
        })

    def test_greater_than_total_amount(self):
        order = _make_order(total_amount=150.0)
        assert _evaluate_condition(order, {
            "field": "total_amount", "operator": ConditionOperator.GREATER_THAN, "value": 100,
        })

    def test_max_item_weight(self):
        order = _make_order(line_items=[SimpleNamespace(sku="HEAVY", weight_lbs=25.0)])
        assert _evaluate_condition(order, {
            "field": "max_item_weight_lbs", "operator": ConditionOperator.GREATER_THAN, "value": 20,
        })


class TestNodeScoring:
    def _candidates(self):
        near = NodeCandidate(
            node=_make_node(id="near", code="NEAR"),
            inventory_by_sku={"SKU-1": 10},
            distance_miles=5.0,
            estimated_cost=6.0,
        )
        far = NodeCandidate(
            node=_make_node(id="far", code="FAR"),
            inventory_by_sku={"SKU-1": 10},
            distance_miles=50.0,
            estimated_cost=8.0,
        )
        return near, far

    def test_distance_optimal_prefers_nearest(self):
        near, far = self._candidates()
        scored = _score_nodes([near, far], SourcingStrategy.DISTANCE_OPTIMAL, None)
        assert scored[0].node.code == "NEAR"

    def test_distance_optimal_tiebreaker_uses_inventory(self):
        near, far = self._candidates()
        near.inventory_by_sku = {"SKU-1": 5}
        far.inventory_by_sku = {"SKU-1": 50}
        far.distance_miles = near.distance_miles  # equal distance
        scored = _score_nodes([near, far], SourcingStrategy.DISTANCE_OPTIMAL, None)
        assert scored[0].node.code == "FAR"

    def test_cost_optimal_prefers_cheapest(self):
        near, far = self._candidates()
        near.estimated_cost = 100.0
        far.estimated_cost = 1.0
        near.distance_miles = 10.0
        far.distance_miles = 10.0
        scored = _score_nodes([near, far], SourcingStrategy.COST_OPTIMAL, None)
        assert scored[0].node.code == "FAR"

    def test_inventory_reservation_prefers_deepest_stock(self):
        near, far = self._candidates()
        near.inventory_by_sku = {"SKU-1": 5}
        far.inventory_by_sku = {"SKU-1": 100}
        scored = _score_nodes([near, far], SourcingStrategy.INVENTORY_RESERVATION, None)
        assert scored[0].node.code == "FAR"


class TestNodeFilter:
    def test_inactive_nodes_excluded(self):
        active = _make_node(status=NodeStatus.ACTIVE)
        inactive = _make_node(id="inactive", code="INACTIVE", status=NodeStatus.INACTIVE)
        order = _make_order()
        filtered = _filter_nodes([active, inactive], None, order)
        assert len(filtered) == 1
        assert filtered[0].code == "NYC-DC"

    def test_capacity_limit_enforced(self):
        at_capacity = _make_node(
            code="FULL",
            daily_order_capacity=100,
            current_daily_orders=100,
        )
        order = _make_order()
        filtered = _filter_nodes([at_capacity], None, order)
        assert filtered == []


class TestSplitAllocation:
    def test_splits_when_no_single_node_can_fulfill(self):
        items = [_make_item("SKU-A", 10), _make_item("SKU-B", 5)]
        node_a = NodeCandidate(
            node=_make_node(id="a", code="A"),
            inventory_by_sku={"SKU-A": 10, "SKU-B": 0},
            score=0.9,
        )
        node_b = NodeCandidate(
            node=_make_node(id="b", code="B"),
            inventory_by_sku={"SKU-A": 0, "SKU-B": 5},
            score=0.8,
        )
        decisions = _compute_split_allocations(items, [node_a, node_b], max_nodes=3)
        skus = {d.sku for d in decisions}
        assert skus == {"SKU-A", "SKU-B"}

    def test_respects_max_split_nodes(self):
        items = [_make_item("SKU-A", 1)]
        candidates = [
            NodeCandidate(
                node=_make_node(id=f"n{i}", code=f"N{i}"),
                inventory_by_sku={"SKU-A": 1},
                score=1.0 - i * 0.1,
            )
            for i in range(5)
        ]
        decisions = _compute_split_allocations(items, candidates, max_nodes=2)
        node_ids = {d.node_id for d in decisions}
        assert len(node_ids) <= 2
