"""MongoDB collections for agent audit, context, and conversations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

AUDIT_COLLECTION = "agent_audit_log"
CONTEXT_COLLECTION = "agent_context"
CONVERSATIONS_COLLECTION = "agent_conversations"

TTL_SECONDS = 60 * 60 * 24 * 90  # 90 days


async def ensure_agent_collections() -> None:
    """Create agent collections and TTL indexes if they do not exist."""
    try:
        from app.database.mongodb import get_mongo_db

        db = await get_mongo_db()
        for name in (AUDIT_COLLECTION, CONTEXT_COLLECTION, CONVERSATIONS_COLLECTION):
            if name not in await db.list_collection_names():
                await db.create_collection(name)

        await db[AUDIT_COLLECTION].create_index("created_at", expireAfterSeconds=TTL_SECONDS)
        await db[CONTEXT_COLLECTION].create_index("updated_at", expireAfterSeconds=TTL_SECONDS)
        await db[CONVERSATIONS_COLLECTION].create_index("updated_at", expireAfterSeconds=TTL_SECONDS)
        await db[AUDIT_COLLECTION].create_index([("agent_id", 1), ("created_at", -1)])
    except Exception:
        pass  # Non-blocking during startup


async def log_agent_audit(
    *,
    agent_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> None:
    try:
        from app.database.mongodb import get_mongo_db

        db = await get_mongo_db()
        await db[AUDIT_COLLECTION].insert_one(
            {
                "agent_id": agent_id,
                "action": action,
                "payload": payload or {},
                "user_id": user_id,
                "created_at": datetime.now(timezone.utc),
            }
        )
    except Exception:
        pass


async def save_conversation(session_id: str, messages: list[dict[str, Any]]) -> None:
    try:
        from app.database.mongodb import get_mongo_db

        db = await get_mongo_db()
        await db[CONVERSATIONS_COLLECTION].update_one(
            {"session_id": session_id},
            {
                "$set": {"messages": messages, "updated_at": datetime.now(timezone.utc)},
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
    except Exception:
        pass
