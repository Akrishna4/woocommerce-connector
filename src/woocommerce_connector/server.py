"""
server.py — MCP server entrypoint for the WooCommerce read-only connector.

Exposes six WooCommerce tools over stdio (default) or streamable HTTP.

Transports
----------
stdio (default)
    The standard MCP transport.  Used by Claude Desktop, cline, and most
    MCP-compatible clients.  Start with::

        wc-mcp-server                              # installed entry point
        python -m woocommerce_connector.server     # alternative

Streamable HTTP (optional)
    MCP SDK 2.3.0 ships native support for the streamable-HTTP transport.
    Start with::

        wc-mcp-server --http                       # default: 127.0.0.1:8001/mcp
        wc-mcp-server --http --host 0.0.0.0 --port 9000

    WARNING: This connector is NOT verified to work with any specific HTTP MCP
    client (e.g. Agent Studio).  It is provided as a best-effort addition.
    Test the stdio transport first.

The server validates config on startup, logs a warning if STORE_URL is plain
HTTP, and maps all typed connector exceptions to clean MCP error responses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from . import tools as wc_tools
from .auth import Settings, load_settings
from .client import AuthError, NotFoundError, RateLimitError, UpstreamError, WooCommerceClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool definitions (module-level so test_schema_drift.py can import them)
# ---------------------------------------------------------------------------

_TOOLS: list[types.Tool] = [
    types.Tool(
        name="list_orders",
        description=(
            "List WooCommerce orders with optional filters. "
            "Returns normalized order summaries with pagination metadata. "
            "Customer PII (email, phone, address) is redacted by default."
        ),
        input_schema={
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
        input_schema={
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
            "If query is purely numeric and matches an existing order ID, ONLY "
            "that order is returned (total=1, with an '_exact_id_match' marker), "
            "hiding any other potential matches. If no order has that ID, normal "
            "substring search results are returned. See AGENT_CAPABILITIES.md "
            "for observed search behavior."
        ),
        input_schema={
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
        input_schema={
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
        input_schema={
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
    types.Tool(
        name="search_products",
        description=(
            "Search products by name (partial text) or SKU (exact match). "
            "At least one of query or sku must be provided. "
            "If only query is provided on page 1, an exact-SKU lookup is also "
            "performed automatically. If the SKU exists, it is returned with an "
            "'_exact_sku_match' marker (note that total may be 0 if the name "
            "search itself found no matches)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional partial text search (e.g., product name). Validated and sanitized.",
                },
                "sku": {
                    "type": "string",
                    "description": "Optional exact SKU lookup.",
                },
                "stock_status": {
                    "type": "string",
                    "description": "Filter by stock status ('instock', 'outofstock', or 'onbackorder').",
                    "enum": ["instock", "outofstock", "onbackorder"],
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
]


# ---------------------------------------------------------------------------
# Handler functions (MCP 2.x constructor-based API)
# ---------------------------------------------------------------------------

async def _on_list_tools(
    ctx: Any,
    params: types.PaginatedRequestParams | None,
) -> types.ListToolsResult:
    """Return the list of available tools."""
    return types.ListToolsResult(tools=_TOOLS)


async def _on_call_tool(
    ctx: Any,
    params: types.CallToolRequestParams,
) -> types.CallToolResult:
    """Dispatch a tool call and return the result as MCP TextContent."""
    client: WooCommerceClient = ctx.lifespan_context["client"]
    settings: Settings = ctx.lifespan_context["settings"]

    name = params.name
    args = dict(params.arguments) if params.arguments else {}

    try:
        if name == "list_orders":
            result = await wc_tools.list_orders(client, settings, **args)
        elif name == "get_order":
            result = await wc_tools.get_order(client, settings, **args)
        elif name == "search_orders":
            result = await wc_tools.search_orders(client, settings, **args)
        elif name == "list_products":
            result = await wc_tools.list_products(client, settings, **args)
        elif name == "get_product":
            result = await wc_tools.get_product(client, settings, **args)
        elif name == "search_products":
            result = await wc_tools.search_products(client, settings, **args)
        else:
            result = {"error": "UnknownTool", "message": f"No tool named {name!r}."}

    except (AuthError, NotFoundError, RateLimitError, UpstreamError) as exc:
        result = {"error": type(exc).__name__, "message": str(exc)}
    except ValueError as exc:
        result = {"error": "InvalidArgument", "message": str(exc)}
    except Exception as exc:
        logger.exception("Unexpected error in tool %r", name)
        result = {"error": "InternalError", "message": str(exc)}

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result, default=str))]
    )


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def _build_server(settings: Settings, client: WooCommerceClient) -> Server:
    """Create and return a configured MCP Server instance."""

    @asynccontextmanager
    async def lifespan(_server: Server) -> Any:  # type: ignore[override]
        yield {"client": client, "settings": settings}

    from contextlib import asynccontextmanager  # local import to avoid shadowing
    return Server(
        "woocommerce-connector",
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
        lifespan=lifespan,
    )


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

def _load_and_warn() -> Settings:
    """Load settings and warn if STORE_URL is plain HTTP."""
    settings = load_settings()
    parsed = urlparse(settings.store_url)
    if parsed.scheme == "http":
        logger.warning(
            "STORE_URL is plain HTTP (%s). For production use HTTPS to protect credentials.",
            settings.store_url,
        )
    logger.info(
        "Starting woocommerce-connector MCP server (store=%s, redact_pii=%s, rpm=%d)",
        settings.store_url,
        settings.redact_pii,
        settings.rpm,
    )
    return settings


# ---------------------------------------------------------------------------
# stdio entrypoint
# ---------------------------------------------------------------------------

async def main_stdio() -> None:
    """Load config, open the HTTP client, and start the MCP stdio server."""
    settings = _load_and_warn()

    async with WooCommerceClient(settings) as client:

        @asynccontextmanager
        async def lifespan(_server: Server) -> Any:  # type: ignore[override]
            yield {"client": client, "settings": settings}

        server = Server(
            "woocommerce-connector",
            on_list_tools=_on_list_tools,
            on_call_tool=_on_call_tool,
            lifespan=lifespan,
        )
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )


# ---------------------------------------------------------------------------
# Streamable HTTP entrypoint (optional)
# ---------------------------------------------------------------------------

async def main_http(host: str = "127.0.0.1", port: int = 8001) -> None:
    """Start the MCP server over streamable HTTP (uvicorn)."""
    import uvicorn

    settings = _load_and_warn()

    async with WooCommerceClient(settings) as client:

        @asynccontextmanager
        async def lifespan(_server: Server) -> Any:  # type: ignore[override]
            yield {"client": client, "settings": settings}

        server = Server(
            "woocommerce-connector",
            on_list_tools=_on_list_tools,
            on_call_tool=_on_call_tool,
            lifespan=lifespan,
        )
        app = server.streamable_http_app(host=host)

        logger.info("Serving MCP over streamable HTTP on http://%s:%d/mcp", host, port)
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        uvi_server = uvicorn.Server(config)
        await uvi_server.serve()


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main_sync() -> None:
    """Synchronous CLI entrypoint (``wc-mcp-server``)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser(description="WooCommerce MCP server")
    parser.add_argument(
        "--http", action="store_true",
        help="Use streamable HTTP transport instead of stdio.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8001, help="HTTP port (default: 8001).")
    args = parser.parse_args()

    if args.http:
        asyncio.run(main_http(host=args.host, port=args.port))
    else:
        asyncio.run(main_stdio())


# Keep backward-compatible alias
main = main_stdio


if __name__ == "__main__":
    main_sync()
