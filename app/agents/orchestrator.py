"""Agent orchestrator — routes tasks to specialized agents."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.agents.implementations.integration_health import IntegrationHealthAgent
from app.agents.implementations.inventory_intelligence import InventoryIntelligenceAgent
from app.agents.implementations.ops_copilot import OpsCopilotAgent
from app.agents.implementations.sla_guardian import SLAGuardianAgent
from app.agents.implementations.sourcing_optimizer import SourcingOptimizerAgent
from app.agents.pubsub import publish_agent_event
from app.agents.storage import log_agent_audit


class AgentOrchestrator:
    AGENT_MAP: dict[str, type[BaseAgent]] = {
        "ops_copilot": OpsCopilotAgent,
        "sourcing_optimizer": SourcingOptimizerAgent,
        "sla_guardian": SLAGuardianAgent,
        "inventory_intelligence": InventoryIntelligenceAgent,
        "integration_health": IntegrationHealthAgent,
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    def list_agents(self) -> list[dict[str, str]]:
        return [
            {"id": agent_id, "description": cls.description}
            for agent_id, cls in self.AGENT_MAP.items()
        ]

    def get_agent(self, agent_id: str) -> BaseAgent:
        cls = self.AGENT_MAP.get(agent_id)
        if cls is None:
            raise ValueError(f"Unknown agent: {agent_id}")
        return cls(self.db)

    async def dispatch(self, agent_id: str, task: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        await log_agent_audit(agent_id=agent_id, action="dispatch", payload=task, user_id=user_id)
        await publish_agent_event(agent_id, {"type": "task_started", "task": task})
        result = await agent.execute(task)
        await publish_agent_event(agent_id, {"type": "task_completed", "result": result})
        return result

    async def route_by_intent(self, intent: str, task: dict[str, Any]) -> dict[str, Any]:
        intent_lower = intent.lower()
        if "sourc" in intent_lower or "alloc" in intent_lower:
            agent_id = "sourcing_optimizer"
        elif "sla" in intent_lower or "breach" in intent_lower:
            agent_id = "sla_guardian"
        elif "invent" in intent_lower or "stock" in intent_lower:
            agent_id = "inventory_intelligence"
        elif "connector" in intent_lower or "integr" in intent_lower:
            agent_id = "integration_health"
        else:
            agent_id = "ops_copilot"
        return await self.dispatch(agent_id, task)
