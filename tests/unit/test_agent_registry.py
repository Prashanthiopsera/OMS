"""Unit tests for agent tool registry (WO-054/055)."""
import app.agents  # noqa: F401 — registers tools on first import
from app.agents.registry import get_tool, list_tools, tool_schemas


def test_tools_register_on_import():
    tools = list_tools()
    names = {t.name for t in tools}
    assert "search_orders" in names
    assert "create_order" in names
    assert len(tools) >= 16


def test_read_tools_have_no_side_effects():
    read_tools = [t for t in list_tools() if not t.has_side_effects]
    assert any(t.name == "get_order_details" for t in read_tools)


def test_write_tools_require_confirmation():
    cancel = get_tool("cancel_order")
    assert cancel is not None
    assert cancel.has_side_effects is True
    assert cancel.requires_confirmation is True


def test_tool_schemas_export():
    schemas = tool_schemas(include_write=False)
    assert all("input_schema" in s for s in schemas)
    assert not any(s["name"] == "cancel_order" for s in schemas)
