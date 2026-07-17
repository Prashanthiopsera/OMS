"""Inventory Intelligence agent — forecasting and transfer recommendations."""
from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent


class InventoryIntelligenceAgent(BaseAgent):
    agent_id = "inventory_intelligence"
    description = "Identifies low stock, slow movers, and transfer opportunities."

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        low_stock = await self.run_tool(
            "get_inventory_status",
            {"low_stock_only": True, "limit": task.get("limit", 30)},
        )
        top_items = await self.run_tool(
            "get_top_selling_items",
            {"limit": 10, "days": task.get("days", 30)},
        )
        return {
            "agent": self.agent_id,
            "low_stock": low_stock,
            "top_sellers": top_items,
            "recommendation": "Review low-stock SKUs against top sellers for replenishment priority.",
        }
