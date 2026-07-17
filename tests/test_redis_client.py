"""
Unit tests for app.database.redis_client — WO-012 (aioredis -> redis.asyncio
migration). Verifies the module exclusively uses redis.asyncio (redis-py's
built-in async client, not the abandoned standalone `aioredis` package) and
that clients are backed by a shared connection pool rather than a
per-operation connect/close pattern.

Run with: PYTHONPATH=. pytest tests/test_redis_client.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_module_uses_redis_asyncio_not_standalone_aioredis():
    import importlib.util
    assert importlib.util.find_spec("aioredis") is None, (
        "the abandoned standalone `aioredis` package must not be installed"
    )

    import app.database.redis_client as redis_client_module
    import redis.asyncio as redis_asyncio

    assert redis_client_module.redis_asyncio is redis_asyncio


def test_get_redis_client_uses_shared_connection_pool():
    from app.database import redis_client

    redis_client.redis_pool = redis_client.redis_asyncio.ConnectionPool.from_url(
        "redis://localhost:6380/0", decode_responses=True,
    )
    try:
        client_a = redis_client.get_redis_client()
        client_b = redis_client.get_redis_client()
        assert client_a.connection_pool is client_b.connection_pool is redis_client.redis_pool
        # Passing an explicit shared pool means the client must never
        # auto-close (and thereby disconnect) that shared pool.
        assert client_a.auto_close_connection_pool is False
    finally:
        redis_client.redis_pool = None


def test_get_redis_client_returns_none_when_not_initialized():
    from app.database import redis_client

    previous = redis_client.redis_pool
    redis_client.redis_pool = None
    try:
        assert redis_client.get_redis_client() is None
    finally:
        redis_client.redis_pool = previous


@pytest.mark.integration
class TestCacheHelpers:
    """WO-013: get/set/delete/exists operations against a real Redis
    instance (see docker-compose.yml / CI's redis service)."""

    @pytest.fixture(autouse=True)
    def _pool(self):
        from app.config import settings
        from app.database import redis_client
        redis_client.redis_pool = redis_client.redis_asyncio.ConnectionPool.from_url(
            settings.REDIS_URL, decode_responses=True,
        )
        yield
        redis_client.redis_pool = None

    @pytest.mark.asyncio
    async def test_cache_set_get_delete_roundtrip(self):
        from app.database.redis_client import cache_get, cache_set, cache_delete, get_redis_client

        client = get_redis_client()
        key = "test:wo013:roundtrip"
        try:
            assert await cache_get(key, client) is None
            await cache_set(key, "hello", ttl=30, redis_client=client)
            assert await cache_get(key, client) == "hello"
            await cache_delete(key, client)
            assert await cache_get(key, client) is None
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_cache_set_respects_ttl(self):
        from app.database.redis_client import cache_set, get_redis_client

        client = get_redis_client()
        key = "test:wo013:ttl"
        try:
            await cache_set(key, "value", ttl=60, redis_client=client)
            ttl = await client.ttl(key)
            assert 0 < ttl <= 60
        finally:
            await client.delete(key)
            await client.aclose()

    @pytest.mark.asyncio
    async def test_cache_delete_pattern_removes_matching_keys(self):
        from app.database.redis_client import cache_delete_pattern, get_redis_client

        client = get_redis_client()
        keys = ["test:wo013:pattern:1", "test:wo013:pattern:2"]
        try:
            for k in keys:
                await client.set(k, "1")
            await cache_delete_pattern("test:wo013:pattern:*", client)
            for k in keys:
                assert await client.exists(k) == 0
        finally:
            await client.aclose()
