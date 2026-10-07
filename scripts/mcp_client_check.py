#!/usr/bin/env python3
"""
scripts/mcp_client_check.py — End-to-end MCP protocol verification.

Starts the MCP server as a subprocess over stdio, connects via the official
mcp Python SDK client, lists tools, calls list_orders and search_products,
and exits non-zero on any failure.

Usage:
    python scripts/mcp_client_check.py

Requirements:
    - .env must be sourced (or env vars set manually)
    - The woocommerce-connector package must be installed in the virtualenv
    - The local WooCommerce store must be reachable
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# MCP SDK 2.x client API
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED_TOOLS = {
    "list_orders",
    "get_order",
    "search_orders",
    "list_products",
    "get_product",
    "search_products",
}


def _ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗  {msg}", file=sys.stderr)


async def run_checks() -> bool:
    """Run all checks. Return True if all pass."""
    # Determine the Python interpreter path
    python = sys.executable

    # Build env: inherit all vars so .env-sourced vars are picked up
    env = os.environ.copy()

    server_params = StdioServerParameters(
        command=python,
        args=["-m", "woocommerce_connector.server"],
        env=env,
    )

    print(f"Launching server: {python} -m woocommerce_connector.server")
    all_passed = True

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:

            # 1. Initialize
            try:
                await session.initialize()
                _ok("initialize")
            except Exception as e:
                _fail(f"initialize failed: {e}")
                return False

            # 2. List tools
            try:
                tools_result = await session.list_tools()
                tool_names = {t.name for t in tools_result.tools}
                missing = EXPECTED_TOOLS - tool_names
                extra = tool_names - EXPECTED_TOOLS
                if missing:
                    _fail(f"list_tools: missing tools: {sorted(missing)}")
                    all_passed = False
                elif extra:
                    _fail(f"list_tools: unexpected extra tools: {sorted(extra)}")
                    all_passed = False
                else:
                    _ok(f"list_tools → {sorted(tool_names)}")
            except Exception as e:
                _fail(f"list_tools failed: {e}")
                all_passed = False

            # 3. Call list_orders
            try:
                result = await session.call_tool("list_orders", arguments={"per_page": 3})
                if result.content:
                    import json
                    payload = json.loads(result.content[0].text)
                    total = payload.get("total", "?")
                    _ok(f"call list_orders → total={total}")
                else:
                    _fail("call list_orders returned empty content")
                    all_passed = False
            except Exception as e:
                _fail(f"call list_orders failed: {e}")
                all_passed = False

            # 4. Call search_products
            try:
                result = await session.call_tool(
                    "search_products",
                    arguments={"query": "Desk", "per_page": 5},
                )
                if result.content:
                    import json
                    payload = json.loads(result.content[0].text)
                    if "error" in payload:
                        _fail(f"call search_products returned error: {payload}")
                        all_passed = False
                    else:
                        total = payload.get("total", "?")
                        items = [i["name"] for i in payload.get("items", [])[:3]]
                        _ok(f"call search_products('Desk') → total={total}, first items: {items}")
                else:
                    _fail("call search_products returned empty content")
                    all_passed = False
            except Exception as e:
                _fail(f"call search_products failed: {e}")
                all_passed = False

    return all_passed


def main() -> None:
    print("=" * 60)
    print("MCP client check")
    print("=" * 60)

    passed = asyncio.run(run_checks())

    print()
    print("=" * 60)
    if passed:
        print("✓ All MCP checks passed.")
        sys.exit(0)
    else:
        print("✗ One or more MCP checks FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
