"""Integration Health agent — connector status and webhook health."""
from __future__ import annotations

from typing import Any

from app.agents.base import BaseAgent
from app.services.connector_service import ConnectorService


class IntegrationHealthAgent(BaseAgent):
    agent_id = "integration_health"
    description = "Monitors connector integrations and flags unhealthy connections."

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        service = ConnectorService(self.db)
        connectors = await service.list_connectors()
        summary = []
        for c in connectors:
            summary.append(
                {
                    "id": str(c.id),
                    "name": c.name,
                    "type": c.connector_type.value if hasattr(c.connector_type, "value") else str(c.connector_type),
                    "status": c.status.value if hasattr(c.status, "value") else str(c.status),
                    "last_error": c.last_error,
                }
            )
        unhealthy = [s for s in summary if s["status"] != "ACTIVE" or s.get("last_error")]
        return {
            "agent": self.agent_id,
            "total": len(summary),
            "unhealthy_count": len(unhealthy),
            "connectors": summary,
            "unhealthy": unhealthy,
        }
