"""Ops Copilot — general operations assistant with read/write order tools."""
from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent


class OpsCopilotAgent(BaseAgent):
    agent_id = "ops_copilot"
    description = "General OMS operations copilot for orders, inventory, and analytics."
    allow_write_tools = True

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        query = task.get("query", "")
        if task.get("tool") and task.get("tool_input"):
            result = await self.run_tool(task["tool"], task["tool_input"])
            return {"agent": self.agent_id, "result": result}

        # Default: search recent orders as a sensible first action
        result = await self.run_tool("search_orders", {"limit": task.get("limit", 10)})
        return {"agent": self.agent_id, "query": query, "result": result}
