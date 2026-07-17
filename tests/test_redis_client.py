"""
Unit tests for app.database.redis_client — WO-011 (aioredis -> redis.asyncio
migration). Verifies the module exclusively uses redis.asyncio (redis-py's
built-in async client, not the abandoned standalone `aioredis` package) and
that clients are backed by a shared connection pool rather than a
per-operation connect/close pattern.

Run with: PYTHONPATH=. pytest tests/test_redis_client.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_module_uses_redis_asyncio_not_standalone_aioredis():
    import importlib.util
    assert importlib.util.find_spec("aioredis") is None, (
        "the abandoned standalone `aioredis` package must not be installed"
    )

    import app.database.redis_client as redis_client_module
    import redis.asyncio as redis_asyncio

    assert redis_client_module.aioredis is redis_asyncio


def test_get_redis_client_uses_shared_connection_pool():
    from app.database import redis_client

    redis_client.redis_pool = redis_client.aioredis.ConnectionPool.from_url(
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
