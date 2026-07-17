"""Sourcing Optimizer agent — recommends and executes sourcing strategies."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from app.agents.base import BaseAgent
from app.services.sourcing_service import SourcingService


class SourcingOptimizerAgent(BaseAgent):
    agent_id = "sourcing_optimizer"
    description = "Analyzes and optimizes order sourcing allocations."
    allow_write_tools = True

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        order_id = task.get("order_id")
        if not order_id:
            rules = await self.run_tool("get_sourcing_rules", {"active_only": True})
            return {"agent": self.agent_id, "recommendation": "Provide order_id to optimize sourcing", "rules": rules}

        if task.get("execute"):
            result = await self.run_tool(
                "source_order",
                {"order_id": order_id, "strategy": task.get("strategy")},
            )
            return {"agent": self.agent_id, "executed": True, "result": result}

        service = SourcingService(self.db)
        summary = await service.source_order_by_id(UUID(order_id), strategy_name=task.get("strategy"))
        return {"agent": self.agent_id, "preview": summary}
