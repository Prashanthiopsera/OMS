"""Unit tests for LLM circuit breaker (WO-062)."""
import pytest

from app.agents.circuit_breaker import CircuitBreaker, CircuitState


@pytest.mark.asyncio
async def test_circuit_opens_after_failures():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)

    async def fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await cb.call(fail)
    with pytest.raises(RuntimeError):
        await cb.call(fail)
    assert cb.state == CircuitState.OPEN

    with pytest.raises(RuntimeError, match="OPEN"):
        await cb.call(fail)
