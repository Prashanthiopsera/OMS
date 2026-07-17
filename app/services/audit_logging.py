"""Structured audit logging for service-layer mutations (WO-088)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("oms.audit")


async def audit_log(
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    actor_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "actor_id": actor_id,
        "details": details or {},
    }
    logger.info("audit %s", entry)
    try:
        from app.agents.storage import log_agent_audit

        await log_agent_audit(
            agent_id="system",
            action=f"{resource_type}:{action}",
            payload=entry,
            user_id=actor_id,
        )
    except Exception:
        pass
