"""Redis pub/sub channels for inter-agent messaging."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Callable, Awaitable

AGENT_CHANNEL_PREFIX = "oms:agents:"
BROADCAST_CHANNEL = f"{AGENT_CHANNEL_PREFIX}broadcast"


async def publish_agent_event(agent_id: str, event: dict[str, Any]) -> None:
    try:
        from app.database.redis_client import get_redis_client

        client = get_redis_client()
        if not client:
            return
        channel = f"{AGENT_CHANNEL_PREFIX}{agent_id}"
        await client.publish(channel, json.dumps(event))
        await client.publish(BROADCAST_CHANNEL, json.dumps({"agent_id": agent_id, **event}))
    except Exception:
        pass


async def subscribe_agent_events(
    agent_id: str,
    handler: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    try:
        from app.database.redis_client import get_redis_client

        client = get_redis_client()
        if not client:
            return
        pubsub = client.pubsub()
        channel = f"{AGENT_CHANNEL_PREFIX}{agent_id}"
        await pubsub.subscribe(channel, BROADCAST_CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode()
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            await handler(payload)
    except Exception:
        pass
