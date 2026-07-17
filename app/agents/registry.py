"""Agent tool registry — decorator-based registration replacing if/elif dispatch."""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    permission_scope: str = "read"
    has_side_effects: bool = False
    requires_confirmation: bool = False


_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    permission_scope: str = "read",
    has_side_effects: bool = False,
    requires_confirmation: bool = False,
) -> Callable[[ToolHandler], ToolHandler]:
    """Register an async tool handler with metadata for RBAC and agent schema export."""

    def decorator(fn: ToolHandler) -> ToolHandler:
        _REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=fn,
            permission_scope=permission_scope,
            has_side_effects=has_side_effects,
            requires_confirmation=requires_confirmation,
        )
        return fn

    return decorator


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def list_tools(*, include_write: bool = True) -> list[ToolSpec]:
    if include_write:
        return list(_REGISTRY.values())
    return [t for t in _REGISTRY.values() if not t.has_side_effects]


def tool_schemas(*, include_write: bool = True) -> list[dict[str, Any]]:
    """Anthropic/OpenAI-compatible tool definitions."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        }
        for spec in list_tools(include_write=include_write)
    ]


async def execute_tool(name: str, tool_input: dict[str, Any], *, db=None) -> dict[str, Any]:
    spec = get_tool(name)
    if spec is None:
        return {"error": f"Unknown tool: {name}"}

    sig = inspect.signature(spec.handler)
    kwargs: dict[str, Any] = {"tool_input": tool_input}
    if "db" in sig.parameters and db is not None:
        kwargs["db"] = db

    try:
        return await spec.handler(**kwargs)
    except Exception as exc:
        return {"error": str(exc)}


def clear_registry() -> None:
    """Test helper — reset registry between test modules."""
    _REGISTRY.clear()
