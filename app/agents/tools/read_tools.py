"""Read-only agent tools migrated from app/routers/ai.py."""
from __future__ import annotations

from app.agents.registry import register_tool


def _ensure_read_tools_loaded() -> None:
    """Import side-effect registers all read tools once."""
    pass


@register_tool(
    name="search_orders",
    description="Search and filter orders from the OMS database.",
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "channel": {"type": "string"},
            "customer_email": {"type": "string"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "limit": {"type": "integer"},
        },
    },
    permission_scope="orders:read",
)
async def search_orders(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _search_orders

    return await _search_orders(db, tool_input)


@register_tool(
    name="get_order_details",
    description="Get full details of a specific order by ID or order number.",
    input_schema={
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
    permission_scope="orders:read",
)
async def get_order_details(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _get_order_details

    return await _get_order_details(db, tool_input)


@register_tool(
    name="get_inventory_status",
    description="Get current inventory levels across all nodes.",
    input_schema={
        "type": "object",
        "properties": {
            "sku": {"type": "string"},
            "node_id": {"type": "string"},
            "low_stock_only": {"type": "boolean"},
            "limit": {"type": "integer"},
        },
    },
    permission_scope="inventory:read",
)
async def get_inventory_status(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _get_inventory_status

    return await _get_inventory_status(db, tool_input)


@register_tool(
    name="get_analytics_summary",
    description="Get OMS analytics summary.",
    input_schema={"type": "object", "properties": {"days": {"type": "integer"}}},
    permission_scope="analytics:read",
)
async def get_analytics_summary(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _get_analytics_summary

    return await _get_analytics_summary(db, tool_input)


@register_tool(
    name="get_sourcing_rules",
    description="Get all sourcing rules.",
    input_schema={"type": "object", "properties": {"active_only": {"type": "boolean"}}},
    permission_scope="sourcing:read",
)
async def get_sourcing_rules(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _get_sourcing_rules

    return await _get_sourcing_rules(db, tool_input)


@register_tool(
    name="get_nodes",
    description="Get fulfillment nodes with capacity.",
    input_schema={
        "type": "object",
        "properties": {"node_type": {"type": "string"}, "active_only": {"type": "boolean"}},
    },
    permission_scope="nodes:read",
)
async def get_nodes(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _get_nodes

    return await _get_nodes(db, tool_input)


@register_tool(
    name="get_top_selling_items",
    description="Get best-selling products ranked by units or revenue.",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "days": {"type": "integer"},
            "rank_by": {"type": "string"},
        },
    },
    permission_scope="analytics:read",
)
async def get_top_selling_items(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _get_top_selling_items

    return await _get_top_selling_items(db, tool_input)


@register_tool(
    name="aggregate_orders",
    description="Flexible aggregation query for order analytics.",
    input_schema={
        "type": "object",
        "properties": {
            "group_by": {"type": "string"},
            "metric": {"type": "string"},
            "sort_order": {"type": "string"},
            "days": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["group_by"],
    },
    permission_scope="analytics:read",
)
async def aggregate_orders(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _aggregate_orders

    return await _aggregate_orders(db, tool_input)


@register_tool(
    name="get_brands",
    description="Get all brands configured in the system.",
    input_schema={"type": "object", "properties": {"active_only": {"type": "boolean"}}},
    permission_scope="brands:read",
)
async def get_brands(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _get_brands

    return await _get_brands(db, tool_input)


@register_tool(
    name="get_b2b_accounts",
    description="Get B2B customer accounts with credit limits.",
    input_schema={
        "type": "object",
        "properties": {
            "search": {"type": "string"},
            "brand_id": {"type": "string"},
            "limit": {"type": "integer"},
        },
    },
    permission_scope="b2b:read",
)
async def get_b2b_accounts(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _get_b2b_accounts

    return await _get_b2b_accounts(db, tool_input)


@register_tool(
    name="get_returns",
    description="Get return/RMA records.",
    input_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "order_id": {"type": "string"},
            "days": {"type": "integer"},
            "limit": {"type": "integer"},
        },
    },
    permission_scope="returns:read",
)
async def get_returns(tool_input: dict, db=None) -> dict:
    from app.routers.ai import _get_returns

    return await _get_returns(db, tool_input)
