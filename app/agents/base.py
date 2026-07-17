"""Base agent interface for the multi-agent operations framework."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import get_llm_client, safe_llm_call
from app.agents.registry import execute_tool, list_tools, tool_schemas
from app.agents.storage import log_agent_audit


class BaseAgent(ABC):
    agent_id: str = "base"
    description: str = ""
    allow_write_tools: bool = False

    def __init__(self, db: AsyncSession):
        self.db = db

    def available_tools(self) -> list[dict[str, Any]]:
        return tool_schemas(include_write=self.allow_write_tools)

    async def run_tool(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        await log_agent_audit(agent_id=self.agent_id, action=f"tool:{name}", payload=tool_input)
        return await execute_tool(name, tool_input, db=self.db)

    async def think(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        client = await get_llm_client()
        if hasattr(client, "messages"):
            async def _call():
                return await client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    messages=messages,
                    tools=self.available_tools(),
                )

            response = await safe_llm_call(_call)
            return {"raw": response}
        return await client.complete(messages, tools=self.available_tools())

    @abstractmethod
    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run the agent's primary workflow."""
