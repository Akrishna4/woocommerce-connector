"""
server.py — MCP server entrypoint for the WooCommerce read-only connector.

Exposes the five WooCommerce tools over the stdio transport using the
official ``mcp`` Python SDK.  Start with::

    wc-mcp-server          # installed entry point
    python -m woocommerce_connector.server   # alternative

The server validates and loads config on startup, logs a warning if
STORE_URL is plain HTTP, and maps all typed connector exceptions to clean
MCP error responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import tools as wc_tools
from .auth import Settings, load_settings
from .client import AuthError, NotFoundError, RateLimitError, UpstreamError, WooCommerceClient

logger = logging.getLogger(__name__)

server = Server("woocommerce-connector")

# Module-level app context, populated in main() before the server loop starts.
_client: WooCommerceClient | None = None
_settings: Settings | None = None


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_TOOLS: list[types.Tool] = [
    types.Tool(
        name="list_orders",
        description=(
            "List WooCommerce orders with optional filters. "
            "Returns normalized order summaries with pagination metadata. "
            "Customer PII (email, phone, address) is redacted by default."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by order status.",
                    "enum": [
                        "pending", "processing", "on-hold", "completed",
                        "cancelled", "refunded", "failed", "trash", "any",
                    ],
                },
                "after": {
                    "type": "string",
                    "description": "Return orders created after this date (YYYY-MM-DD).",
                },
                "before": {
                    "type": "string",
                    "description": "Return orders created before this date (YYYY-MM-DD).",
                },
                "customer": {
                    "type": "integer",
                    "description": "Filter by WooCommerce customer ID.",
                },
                "page": {
                    "type": "integer",
                    "description": "Page number (1-indexed).",
                    "default": 1,
                    "minimum": 1,
                },
                "per_page": {
                    "type": "integer",
                    "description": "Results per page (max 30).",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 30,
                },
            },
        },
    ),
    types.Tool(
        name="get_order",
        description=(
            "Retrieve a single WooCommerce order by ID. "
            "Includes billing/shipping info (PII redacted by default), "
            "line items, and a sanitized customer note."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The WooCommerce order ID.",
                },
            },
            "required": ["order_id"],
        },
    ),
    types.Tool(
        name="search_orders",
        description=(
            "Search orders using WooCommerce's built-in search parameter. "
            "The query is validated and sanitized before sending. "
            "See AGENT_CAPABILITIES.md for observed search behavior."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search string (max 200 chars).",
                    "maxLength": 200,
                },
                "page": {
                    "type": "integer",
                    "description": "Page number (1-indexed).",
                    "default": 1,
                    "minimum": 1,
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="list_products",
        description=(
            "List WooCommerce products with optional filters. "
            "Each result includes stock_quantity and stock_status. "
            "Descriptions are HTML-stripped and truncated."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "stock_status": {
                    "type": "string",
                    "description": "Filter by stock status.",
                    "enum": ["instock", "outofstock", "onbackorder"],
                },
                "category": {
                    "type": "integer",
                    "description": (
                        "Filter by category term ID (integer, not slug). "
                        "Find IDs in WooCommerce admin > Products > Categories."
                    ),
                },
                "page": {
                    "type": "integer",
                    "description": "Page number (1-indexed).",
                    "default": 1,
                    "minimum": 1,
                },
                "per_page": {
                    "type": "integer",
                    "description": "Results per page (max 30).",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 30,
                },
            },
        },
    ),
    types.Tool(
        name="get_product",
        description=(
            "Retrieve a single WooCommerce product by ID. "
            "Includes manage_stock, stock_quantity, and stock_status."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "product_id": {
                    "type": "integer",
                    "description": "The WooCommerce product ID.",
                },
            },
            "required": ["product_id"],
        },
    ),
]


# ---------------------------------------------------------------------------
# MCP handler callbacks
# ---------------------------------------------------------------------------

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Return the list of available tools."""
    return _TOOLS


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> list[types.TextContent]:
    """Dispatch a tool call and return the result as MCP TextContent."""
    assert _client is not None and _settings is not None, (
        "Server not initialised — this is a bug in main()"
    )

    args = arguments or {}

    try:
        if name == "list_orders":
            result = await wc_tools.list_orders(_client, _settings, **args)
        elif name == "get_order":
            result = await wc_tools.get_order(_client, _settings, **args)
        elif name == "search_orders":
            result = await wc_tools.search_orders(_client, _settings, **args)
        elif name == "list_products":
            result = await wc_tools.list_products(_client, _settings, **args)
        elif name == "get_product":
            result = await wc_tools.get_product(_client, _settings, **args)
        else:
            result = {"error": "UnknownTool", "message": f"No tool named {name!r}."}

    except (AuthError, NotFoundError, RateLimitError, UpstreamError) as exc:
        result = {"error": type(exc).__name__, "message": str(exc)}
    except ValueError as exc:
        result = {"error": "InvalidArgument", "message": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in tool %r", name)
        result = {"error": "InternalError", "message": str(exc)}

    return [types.TextContent(type="text", text=json.dumps(result, default=str))]


# ---------------------------------------------------------------------------
# Startup and entrypoint
# ---------------------------------------------------------------------------

async def main() -> None:
    """Load config, open the HTTP client, and start the MCP stdio server."""
    global _client, _settings

    _settings = load_settings()

    # Warn at startup if STORE_URL is plain HTTP (even for localhost).
    parsed = urlparse(_settings.store_url)
    if parsed.scheme == "http":
        logger.warning(
            "STORE_URL is plain HTTP (%s). For production use HTTPS to protect credentials.",
            _settings.store_url,
        )

    logger.info(
        "Starting woocommerce-connector MCP server (store=%s, redact_pii=%s, rpm=%d)",
        _settings.store_url,
        _settings.redact_pii,
        _settings.rpm,
    )

    async with WooCommerceClient(_settings) as client:
        _client = client
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )


def main_sync() -> None:
    """Synchronous entrypoint called by the ``wc-mcp-server`` script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
