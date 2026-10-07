#!/usr/bin/env python3
"""
scripts/smoke_test.py — Run all five MCP tools against the live local store.

Records search_orders empirical behavior and prints it clearly for documentation.
Exits non-zero on any tool failure.

Usage:
    python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Allow running from the project root without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from woocommerce_connector.auth import load_settings
from woocommerce_connector.client import WooCommerceClient
from woocommerce_connector import tools

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"

failures: list[str] = []


def _ok(label: str) -> None:
    print(f"  {PASS} {label}")


def _fail(label: str, exc: Exception) -> None:
    print(f"  {FAIL} {label}: {exc}")
    failures.append(label)


def _summarize(label: str, result: dict) -> None:
    meta = {k: v for k, v in result.items() if k not in ("items", "_untrusted_fields")}
    meta["item_count"] = len(result.get("items", []))
    print(f"    {json.dumps(meta)}")


async def run_smoke_tests() -> None:
    settings = load_settings()
    print(f"{BOLD}Smoke test against: {settings.store_url}{RESET}")
    print(f"PII redaction: {settings.redact_pii}")
    print()

    async with WooCommerceClient(settings) as client:

        # ---- 1. list_orders (no filter) ----
        print(f"{BOLD}1. list_orders(){RESET}")
        try:
            result = await tools.list_orders(client, settings, per_page=5)
            _summarize("list_orders", result)
            _ok("list_orders")
        except Exception as e:
            _fail("list_orders", e)

        # ---- 2. list_orders(status=processing) ----
        print(f"\n{BOLD}2. list_orders(status='processing'){RESET}")
        try:
            result = await tools.list_orders(client, settings, status="processing", per_page=10)
            _summarize("list_orders(processing)", result)
            items = result.get("items", [])
            for o in items[:3]:
                city = o.get("shipping", {}).get("city") or o.get("billing", {}).get("city")
                print(f"    order_id={o['id']} status={o['status']} city={city}")
            _ok("list_orders(status=processing)")
        except Exception as e:
            _fail("list_orders(status=processing)", e)

        # ---- 3. list_orders(status=on-hold) ----
        print(f"\n{BOLD}3. list_orders(status='on-hold'){RESET}")
        try:
            result = await tools.list_orders(client, settings, status="on-hold", per_page=10)
            _summarize("list_orders(on-hold)", result)
            _ok("list_orders(status=on-hold)")
        except Exception as e:
            _fail("list_orders(status=on-hold)", e)

        # ---- 4. get_order ----
        print(f"\n{BOLD}4. get_order(){RESET}")
        try:
            orders = await tools.list_orders(client, settings, per_page=1)
            if orders["items"]:
                first_id = orders["items"][0]["id"]
                result = await tools.get_order(client, settings, first_id)
                print(f"    id={result['id']} status={result['status']} total={result['total']}")
                _ok(f"get_order({first_id})")
            else:
                print("    No orders found — skipping get_order")
        except Exception as e:
            _fail("get_order", e)

        # ---- 5. search_orders — EMPIRICAL BEHAVIOR RECORDING ----
        print(f"\n{BOLD}5. search_orders() — empirical search behavior{RESET}")
        print("    (Results below document actual WooCommerce search behavior)")

        search_queries = [
            ("billing first name",   "Alice"),
            ("billing last name",    "Farnsworth"),
            ("email fragment",       "example.com"),
            ("full email",           "alice.farnsworth@example.com"),
            ("city name",            "Springfield"),
            ("numeric order ID",     "1"),
        ]

        print()
        print("    Query                            → total  page_items  match_example")
        print("    " + "-" * 65)
        for label, query in search_queries:
            try:
                result = await tools.search_orders(client, settings, query, page=1)
                total = result.get("total", 0)
                page_items = len(result.get("items", []))
                first = result["items"][0] if result["items"] else None
                example = ""
                if first:
                    b = first.get("billing", {})
                    example = f"order#{first['id']} {first['status']}"
                print(
                    f"    {label:32s} → {total:5d}  {page_items:10d}  {example}"
                )
            except Exception as e:
                print(f"    {label:32s} → ERROR: {e}")
                failures.append(f"search_orders({label})")

        _ok("search_orders (all queries executed)")

        # ---- 6. list_products (no filter) ----
        print(f"\n{BOLD}6. list_products(){RESET}")
        try:
            result = await tools.list_products(client, settings, per_page=5)
            _summarize("list_products", result)
            _ok("list_products")
        except Exception as e:
            _fail("list_products", e)

        # ---- 7. list_products(stock_status=outofstock) ----
        print(f"\n{BOLD}7. list_products(stock_status='outofstock'){RESET}")
        try:
            result = await tools.list_products(client, settings, stock_status="outofstock", per_page=10)
            _summarize("list_products(outofstock)", result)
            for p in result.get("items", [])[:5]:
                print(f"    product_id={p['id']} sku={p['sku']!r} qty={p['stock_quantity']}")
            _ok("list_products(stock_status=outofstock)")
        except Exception as e:
            _fail("list_products(stock_status=outofstock)", e)

        # ---- 8. list_products(stock_status=instock) — low-stock awareness ----
        print(f"\n{BOLD}8. list_products(stock_status='instock') — low-stock check{RESET}")
        try:
            all_instock: list[dict] = []
            page = 1
            while True:
                result = await tools.list_products(client, settings, stock_status="instock",
                                                    page=page, per_page=30)
                all_instock.extend(result.get("items", []))
                if result["next_page"] is None:
                    break
                page += 1
                if page > 5:
                    break

            low = [p for p in all_instock if (p.get("stock_quantity") or 0) <= 5]
            print(f"    Total instock: {len(all_instock)}  Low stock (≤5): {len(low)}")
            for p in low[:5]:
                print(f"    LOW  {p['sku']:20s} qty={p['stock_quantity']:3d}  {p['name']!r}")
            _ok("list_products(stock_status=instock, low-stock detection)")
        except Exception as e:
            _fail("list_products(low-stock)", e)

        # ---- 9. get_product ----
        print(f"\n{BOLD}9. get_product(){RESET}")
        try:
            products = await tools.list_products(client, settings, per_page=1)
            if products["items"]:
                first_id = products["items"][0]["id"]
                result = await tools.get_product(client, settings, first_id)
                print(
                    f"    id={result['id']} name={result['name']!r} "
                    f"stock={result['stock_status']} qty={result['stock_quantity']}"
                )
                _ok(f"get_product({first_id})")
            else:
                print("    No products found — skipping get_product")
        except Exception as e:
            _fail("get_product", e)

        # ---- 10. search_products ----
        print(f"\n{BOLD}10. search_products(){RESET}")
        try:
            # By product name
            result_name = await tools.search_products(client, settings, "Desk", per_page=10)
            _summarize("search_products('Desk')", result_name)
            for p in result_name.get("items", [])[:3]:
                print(f"    product_id={p['id']} name={p['name']!r} sku={p['sku']!r}")

            # By SKU
            result_sku = await tools.search_products(client, settings, "SEED-DSK", per_page=10)
            _summarize("search_products('SEED-DSK')", result_sku)
            for p in result_sku.get("items", [])[:3]:
                print(f"    product_id={p['id']} name={p['name']!r} sku={p['sku']!r}")
            _ok("search_products")
        except Exception as e:
            _fail("search_products", e)

    # ---- Summary ----
    print()
    print("=" * 60)
    if failures:
        print(f"{FAIL} FAILED — {len(failures)} issue(s): {', '.join(failures)}")
        sys.exit(1)
    else:
        print(f"{PASS} All smoke tests passed.")


if __name__ == "__main__":
    asyncio.run(run_smoke_tests())
