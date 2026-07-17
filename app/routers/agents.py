"""Agent framework API — orchestrator dispatch and tool listing."""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import app.agents  # noqa: F401 — register tools
from app.agents.orchestrator import AgentOrchestrator
from app.agents.registry import list_tools, tool_schemas
from app.agents.storage import ensure_agent_collections
from app.database.postgres import get_db
from app.dependencies.auth import get_current_user, require_superadmin

router = APIRouter(prefix="/agents", tags=["Agents"])


class AgentTaskRequest(BaseModel):
    agent_id: Optional[str] = None
    intent: Optional[str] = None
    task: dict[str, Any] = {}


class AgentTaskResponse(BaseModel):
    agent_id: str
    result: dict[str, Any]


@router.get("/")
async def list_available_agents(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await ensure_agent_collections()
    orchestrator = AgentOrchestrator(db)
    return {"agents": orchestrator.list_agents(), "tools": len(list_tools())}


@router.get("/tools")
async def list_agent_tools(_: dict = Depends(require_superadmin)):
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "permission_scope": t.permission_scope,
                "has_side_effects": t.has_side_effects,
                "requires_confirmation": t.requires_confirmation,
            }
            for t in list_tools()
        ]
    }


@router.post("/dispatch", response_model=AgentTaskResponse)
async def dispatch_agent_task(
    body: AgentTaskRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    orchestrator = AgentOrchestrator(db)
    user_id = user.get("sub")
    try:
        if body.agent_id:
            result = await orchestrator.dispatch(body.agent_id, body.task, user_id=user_id)
            return AgentTaskResponse(agent_id=body.agent_id, result=result)
        if body.intent:
            result = await orchestrator.route_by_intent(body.intent, body.task)
            return AgentTaskResponse(agent_id="routed", result=result)
        raise HTTPException(status_code=400, detail="Provide agent_id or intent")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
