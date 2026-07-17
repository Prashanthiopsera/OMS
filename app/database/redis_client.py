import redis.asyncio as redis_asyncio
from typing import Optional, AsyncGenerator
from app.config import settings
import logging

logger = logging.getLogger(__name__)

redis_pool: Optional[redis_asyncio.ConnectionPool] = None


async def init_redis():
    global redis_pool
    redis_pool = redis_asyncio.ConnectionPool.from_url(
        settings.REDIS_URL,
        max_connections=50,
        decode_responses=True,
    )
    # Verify connection
    client = redis_asyncio.Redis(connection_pool=redis_pool)
    await client.ping()
    await client.aclose()
    logger.info("Redis connection pool initialized")


async def close_redis():
    global redis_pool
    if redis_pool:
        await redis_pool.disconnect()
        redis_pool = None
        logger.info("Redis connection pool closed")


async def get_redis() -> AsyncGenerator[redis_asyncio.Redis, None]:
    if redis_pool is None:
        raise RuntimeError("Redis pool not initialized. Call init_redis() first.")
    client = redis_asyncio.Redis(connection_pool=redis_pool)
    try:
        yield client
    finally:
        await client.aclose()


def get_redis_client() -> Optional[redis_asyncio.Redis]:
    """Return a Redis client directly (caller is responsible for aclose). Returns None if not initialized."""
    if redis_pool is None:
        return None
    return redis_asyncio.Redis(connection_pool=redis_pool)


def get_redis_sync():
    """Synchronous Redis client for Celery tasks."""
    import redis
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


# Cache helpers
async def cache_get(key: str, redis_client: redis_asyncio.Redis) -> Optional[str]:
    return await redis_client.get(key)


async def cache_set(key: str, value: str, ttl: int, redis_client: redis_asyncio.Redis):
    await redis_client.setex(key, ttl, value)


async def cache_delete(key: str, redis_client: redis_asyncio.Redis):
    await redis_client.delete(key)


async def cache_delete_pattern(pattern: str, redis_client: redis_asyncio.Redis):
    keys = await redis_client.keys(pattern)
    if keys:
        await redis_client.delete(*keys)
