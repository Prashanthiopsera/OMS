"""SLA Guardian agent — monitors order SLA breaches."""
from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent


class SLAGuardianAgent(BaseAgent):
    agent_id = "sla_guardian"
    description = "Monitors orders at risk of SLA breach and flags overdue fulfillments."

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        status_filter = task.get("status", "CONFIRMED")
        orders = await self.run_tool(
            "search_orders",
            {"status": status_filter, "limit": task.get("limit", 50)},
        )
        at_risk = []
        for order in orders.get("orders", []):
            if order.get("status") in ("CONFIRMED", "SOURCING", "BACKORDERED"):
                at_risk.append(
                    {
                        "order_number": order.get("order_number"),
                        "status": order.get("status"),
                        "created_at": order.get("created_at"),
                    }
                )
        return {"agent": self.agent_id, "at_risk_count": len(at_risk), "orders": at_risk[:20]}
