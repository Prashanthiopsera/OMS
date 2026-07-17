"""LLM client abstraction with mock support for tests."""
from __future__ import annotations

import json
import os
from typing import Any

from app.agents.circuit_breaker import llm_circuit_breaker


class MockLLMClient:
    """Deterministic LLM responses for CI and local dev without API keys."""

    async def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict[str, Any]:
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        if tools and "order" in str(last_user).lower():
            return {
                "content": [{"type": "tool_use", "id": "mock-1", "name": "search_orders", "input": {"limit": 5}}],
                "stop_reason": "tool_use",
            }
        return {
            "content": [{"type": "text", "text": "Mock LLM response — configure OPENAI_API_KEY or ANTHROPIC_API_KEY for live inference."}],
            "stop_reason": "end_turn",
        }


async def get_llm_client():
    if os.environ.get("OMS_USE_MOCK_LLM", "").lower() in ("1", "true", "yes"):
        return MockLLMClient()

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return MockLLMClient()

    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        return anthropic.AsyncAnthropic(api_key=api_key)
    return MockLLMClient()


async def safe_llm_call(fn, *args, **kwargs):
    return await llm_circuit_breaker.call(fn, *args, **kwargs)
