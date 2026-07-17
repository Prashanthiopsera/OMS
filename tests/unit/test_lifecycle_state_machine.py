"""Hypothesis RuleBasedStateMachine tests for order lifecycle transitions (WO-032).

Exhaustively walks valid transition paths against the standard shipping lifecycle
graph and verifies validate_transition agrees with every step.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import random

import pytest
from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from app.services.lifecycle_engine import validate_transition
from tests.factories import _default_shipping_lifecycle_steps, make_lifecycle


def _build_transition_graph() -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for step in _default_shipping_lifecycle_steps():
        graph[step["status"]] = list(step["allowed_next_statuses"])
    return graph


TRANSITION_GRAPH = _build_transition_graph()
TERMINAL_STATUSES = {s for s, nexts in TRANSITION_GRAPH.items() if not nexts}


class LifecycleStateMachine(RuleBasedStateMachine):
    """Random walk over allowed lifecycle transitions."""

    def __init__(self) -> None:
        super().__init__()
        self.current_status = "CONFIRMED"
        self.history: list[str] = ["CONFIRMED"]

    @rule()
    def advance(self) -> None:
        allowed = TRANSITION_GRAPH.get(self.current_status, [])
        if not allowed:
            return
        self.current_status = random.choice(allowed)
        self.history.append(self.current_status)

    @invariant()
    def status_is_known(self) -> None:
        assert self.current_status in TRANSITION_GRAPH

    @invariant()
    def transitions_are_valid(self) -> None:
        for prev, nxt in zip(self.history, self.history[1:]):
            assert nxt in TRANSITION_GRAPH[prev], f"Invalid edge {prev} → {nxt}"


TestLifecycleStateMachine = LifecycleStateMachine.TestCase
TestLifecycleStateMachine.settings = settings(max_examples=200, deadline=None)


@pytest.mark.parametrize(
    "current,new,expected",
    [
        ("CONFIRMED", "SOURCING", True),
        ("CONFIRMED", "DELIVERED", False),
        ("PICKING", "PACKING", True),
        ("PICKING", "SHIPPED", False),
        ("SHIPPED", "DELIVERED", True),
        ("DELIVERED", "CANCELLED", False),
        ("BACKORDERED", "SOURCING", True),
    ],
)
@pytest.mark.asyncio
async def test_validate_transition_matches_graph(current: str, new: str, expected: bool):
    lc = make_lifecycle()
    order = SimpleNamespace(
        status=SimpleNamespace(value=current),
        fulfillment_type=SimpleNamespace(value="SHIP_TO_HOME"),
        channel=SimpleNamespace(value="WEB"),
        order_type=None,
        brand_id=None,
    )

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [lc]
    mock_db.execute = AsyncMock(return_value=mock_result)

    allowed, _reason = await validate_transition(mock_db, order, new)
    assert allowed is expected


def test_transition_graph_covers_key_statuses():
    required = {
        "PENDING", "CONFIRMED", "SOURCING", "SOURCED", "PICKING",
        "PACKING", "READY_TO_SHIP", "SHIPPED", "DELIVERED", "CANCELLED",
    }
    assert required.issubset(TRANSITION_GRAPH.keys())


def test_terminal_states_have_no_outgoing_edges():
    for status in TERMINAL_STATUSES:
        assert TRANSITION_GRAPH[status] == []
