#!/usr/bin/env python3
"""
scripts/seed_fake_data.py — Populate the local WooCommerce store with fictional demo data.

This script is setup-only and is NOT part of the read-only connector.
It uses the WC_SEED_KEY / WC_SEED_SECRET (read/write) keys, which must be
separate from the read-only keys used by the connector.

Idempotency
-----------
- Products: identified by SKU prefix "SEED-".  Already-present SKUs are skipped.
- Orders: identified by "[seed-demo]" marker in customer_note.  Detection uses a
  paginated GET scan — WooCommerce search does not index customer_note.  If ≥ 25
  such orders exist, creation is skipped unless --force is passed.

Flags
-----
  --dry-run   Preview what would be created without making any API calls.
  --force     Re-seed even if existing data is detected (does NOT delete first).

Usage
-----
  python scripts/seed_fake_data.py
  python scripts/seed_fake_data.py --dry-run
  python scripts/seed_fake_data.py --force
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STORE_URL = os.environ.get("STORE_URL", "http://localhost:8080").rstrip("/")
SEED_KEY = os.environ.get("WC_SEED_KEY", "")
SEED_SECRET = os.environ.get("WC_SEED_SECRET", "")
BASE_URL = STORE_URL + "/wp-json/wc/v3"

SEED_MARKER = "[seed-demo]"
SKU_PREFIX = "SEED-"
SEED_ORDER_THRESHOLD = 25  # skip order creation if this many already exist


# ---------------------------------------------------------------------------
# Fictional demo data — clearly fake names, example.com emails only
# ---------------------------------------------------------------------------

FICTIONAL_PRODUCTS: list[dict[str, Any]] = [
    # Normal in-stock products
    {"name": "Zephyr Wireless Keyboard",    "sku": "SEED-KBD-001", "price": "79.99",  "qty": 45,  "stock": "instock"},
    {"name": "Luminos Desk Lamp",           "sku": "SEED-LMP-002", "price": "34.50",  "qty": 30,  "stock": "instock"},
    {"name": "Vortex USB-C Hub",            "sku": "SEED-HUB-003", "price": "49.99",  "qty": 60,  "stock": "instock"},
    {"name": "Nimbus Laptop Stand",         "sku": "SEED-STD-004", "price": "29.95",  "qty": 80,  "stock": "instock"},
    {"name": "Aether Noise-Cancel Headset", "sku": "SEED-AUD-005", "price": "149.00", "qty": 15,  "stock": "instock"},
    {"name": "Solaris Solar Charger",       "sku": "SEED-CHG-006", "price": "59.99",  "qty": 25,  "stock": "instock"},
    {"name": "Cascade Water Bottle",        "sku": "SEED-BTL-007", "price": "24.99",  "qty": 120, "stock": "instock"},
    {"name": "Meridian Notebook A5",        "sku": "SEED-NTB-008", "price": "12.99",  "qty": 200, "stock": "instock"},
    {"name": "Prism Webcam 1080p",          "sku": "SEED-CAM-009", "price": "89.00",  "qty": 20,  "stock": "instock"},
    {"name": "Flux Mechanical Pencil Set",  "sku": "SEED-PEN-010", "price": "18.50",  "qty": 75,  "stock": "instock"},
    {"name": "Orbit Mouse Pad XL",          "sku": "SEED-MPD-011", "price": "22.00",  "qty": 50,  "stock": "instock"},
    {"name": "Terra Plant Pot Set",         "sku": "SEED-PLT-012", "price": "39.95",  "qty": 40,  "stock": "instock"},
    {"name": "Helios Sunrise Alarm Clock",  "sku": "SEED-CLK-013", "price": "54.99",  "qty": 18,  "stock": "instock"},
    {"name": "Zenith Ergonomic Wrist Rest", "sku": "SEED-WRS-014", "price": "19.99",  "qty": 65,  "stock": "instock"},
    {"name": "Nova HDMI Cable 2m",          "sku": "SEED-CBL-015", "price": "14.99",  "qty": 90,  "stock": "instock"},
    # Low-stock (interesting for "which products are low on stock?" demo prompt)
    {"name": "Apex Standing Desk",          "sku": "SEED-DSK-016", "price": "399.00", "qty": 3,   "stock": "instock"},
    {"name": "Comet Portable Monitor",      "sku": "SEED-MON-017", "price": "279.99", "qty": 2,   "stock": "instock"},
    {"name": "Quasar Wireless Charger",     "sku": "SEED-WCH-018", "price": "44.99",  "qty": 4,   "stock": "instock"},
    # Out-of-stock
    {"name": "Pulsar Gaming Chair",         "sku": "SEED-CHR-019", "price": "499.00", "qty": 0,   "stock": "outofstock"},
    {"name": "Stratos 4K Webcam",           "sku": "SEED-4KW-020", "price": "189.00", "qty": 0,   "stock": "outofstock"},
]

# Clearly fictional customers — all example.com emails
FICTIONAL_CUSTOMERS: list[dict[str, Any]] = [
    {"first": "Alice",  "last": "Farnsworth",  "email": "alice.farnsworth@example.com",  "city": "Springfield",   "state": "IL", "country": "US", "postcode": "62701"},
    {"first": "Bob",    "last": "Nightingale", "email": "bob.nightingale@example.com",   "city": "Shelbyville",   "state": "IL", "country": "US", "postcode": "62565"},
    {"first": "Carol",  "last": "Ashby",       "email": "carol.ashby@example.com",       "city": "Capital City",  "state": "IL", "country": "US", "postcode": "62700"},
    {"first": "Dave",   "last": "Pemberton",   "email": "dave.pemberton@example.com",    "city": "Ogdenville",    "state": "IL", "country": "US", "postcode": "62300"},
    {"first": "Eve",    "last": "Clearwater",  "email": "eve.clearwater@example.com",    "city": "Springfield",   "state": "IL", "country": "US", "postcode": "62702"},
    {"first": "Frank",  "last": "Montague",    "email": "frank.montague@example.com",    "city": "North Haverbrook","state": "IL","country": "US", "postcode": "62400"},
    {"first": "Grace",  "last": "Holloway",    "email": "grace.holloway@example.com",    "city": "Brockway",      "state": "IL", "country": "US", "postcode": "62100"},
    {"first": "Hank",   "last": "Underwood",   "email": "hank.underwood@example.com",    "city": "Cypress Creek", "state": "IL", "country": "US", "postcode": "62200"},
    {"first": "Irene",  "last": "Blackwood",   "email": "irene.blackwood@example.com",   "city": "Springfield",   "state": "IL", "country": "US", "postcode": "62703"},
    {"first": "Jack",   "last": "Thornton",    "email": "jack.thornton@example.com",     "city": "Shelbyville",   "state": "IL", "country": "US", "postcode": "62566"},
]

# 30 order statuses — mix of all types for interesting demo results
ORDER_STATUSES: list[str] = (
    ["processing"] * 9
    + ["completed"] * 10
    + ["on-hold"] * 4
    + ["cancelled"] * 3
    + ["refunded"] * 2
    + ["pending"] * 1
    + ["failed"] * 1
)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _auth() -> tuple[str, str]:
    return (SEED_KEY, SEED_SECRET)


def _get(path: str, params: dict | None = None) -> Any:
    resp = httpx.get(f"{BASE_URL}/{path}", auth=_auth(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, data: dict, dry_run: bool) -> Any:
    if dry_run:
        return {"id": 0, "_dry_run": True}
    resp = httpx.post(f"{BASE_URL}/{path}", auth=_auth(), json=data, timeout=15)
    if not resp.is_success:
        print(f"  WARN: POST /{path} → {resp.status_code}: {resp.text[:200]}")
        return None
    return resp.json()


# ---------------------------------------------------------------------------
# Idempotency checks
# ---------------------------------------------------------------------------

def _existing_seed_skus() -> set[str]:
    """Return all product SKUs already present with the SEED- prefix."""
    skus: set[str] = set()
    page = 1
    while True:
        products = _get("products", {"per_page": 100, "page": page})
        if not products:
            break
        for p in products:
            sku = p.get("sku", "")
            if sku.startswith(SKU_PREFIX):
                skus.add(sku)
        if len(products) < 100:
            break
        page += 1
    return skus


def _count_seed_orders() -> int:
    """Count orders that contain SEED_MARKER in customer_note.

    Note: WooCommerce's ``search`` parameter does NOT index ``customer_note``,
    so we cannot rely on a search query to find seeded orders.  Instead we
    page through all orders in reverse-creation order and check the field in
    Python.  We cap at 10 pages × 100 = 1,000 orders to avoid long runtimes
    on large stores.
    """
    count = 0
    page = 1
    max_scan_pages = 10
    while page <= max_scan_pages:
        orders = _get("orders", {"per_page": 100, "page": page})
        if not orders:
            break
        for o in orders:
            if SEED_MARKER in (o.get("customer_note") or ""):
                count += 1
        if len(orders) < 100:
            break
        page += 1
    return count


def _get_existing_product_ids() -> list[int]:
    """Return WooCommerce IDs for all already-seeded products."""
    ids: list[int] = []
    page = 1
    while True:
        products = _get("products", {"per_page": 100, "page": page})
        if not products:
            break
        for p in products:
            if (p.get("sku") or "").startswith(SKU_PREFIX):
                ids.append(p["id"])
        if len(products) < 100:
            break
        page += 1
    return ids


# ---------------------------------------------------------------------------
# Seeding logic
# ---------------------------------------------------------------------------

def seed_products(dry_run: bool, force: bool) -> list[int]:
    """Create fictional products; return list of WooCommerce product IDs."""
    print("\n=== Products ===")

    existing_skus = _existing_seed_skus() if not dry_run else set()
    print(f"  Existing SEED- products: {len(existing_skus)}")

    created_ids: list[int] = []
    skipped = 0

    for p in FICTIONAL_PRODUCTS:
        sku = p["sku"]
        if sku in existing_skus and not force:
            print(f"  SKIP   {sku}")
            skipped += 1
            continue

        payload: dict[str, Any] = {
            "name": p["name"],
            "sku": sku,
            "type": "simple",
            "status": "publish",
            "regular_price": p["price"],
            "manage_stock": True,
            "stock_quantity": p["qty"],
            "stock_status": p["stock"],
            "tags": [{"name": "seed-demo"}],
        }

        action = "DRY  " if dry_run else "CREATE"
        print(f"  {action} {sku} — {p['name']!r} (qty={p['qty']}, {p['stock']})")

        result = _post("products", payload, dry_run)
        if result and result.get("id"):
            created_ids.append(result["id"])

        if not dry_run:
            time.sleep(0.15)

    print(f"  Result: {len(created_ids)} created, {skipped} skipped")
    return created_ids


def seed_orders(product_ids: list[int], dry_run: bool, force: bool) -> None:
    """Create fictional orders using the given product IDs."""
    print("\n=== Orders ===")

    if not dry_run and not force:
        count = _count_seed_orders()
        if count >= SEED_ORDER_THRESHOLD:
            print(
                f"  Found {count} existing seed orders (≥ {SEED_ORDER_THRESHOLD}) "
                "— skipping. Use --force to re-seed."
            )
            return
        print(f"  Found {count} existing seed orders — creating up to 30.")

    rng = random.Random(42)  # fixed seed → reproducible dry-run output
    target = 30

    for i in range(target):
        status = ORDER_STATUSES[i % len(ORDER_STATUSES)]
        customer = FICTIONAL_CUSTOMERS[i % len(FICTIONAL_CUSTOMERS)]

        # Pick 1–3 products per order
        num_items = rng.randint(1, 3)
        line_items: list[dict] = []
        if product_ids:
            chosen = rng.sample(product_ids, min(num_items, len(product_ids)))
            line_items = [{"product_id": pid, "quantity": rng.randint(1, 3)} for pid in chosen]

        note = f"{SEED_MARKER} order #{i + 1:02d}"

        payload: dict[str, Any] = {
            "status": status,
            "customer_note": note,
            "billing": {
                "first_name": customer["first"],
                "last_name": customer["last"],
                "address_1": f"{100 + i} Demo Street",
                "city": customer["city"],
                "state": customer["state"],
                "postcode": customer["postcode"],
                "country": customer["country"],
                "email": customer["email"],
                "phone": f"555-{1000 + i:04d}",
            },
            "shipping": {
                "first_name": customer["first"],
                "last_name": customer["last"],
                "address_1": f"{100 + i} Demo Street",
                "city": customer["city"],
                "state": customer["state"],
                "postcode": customer["postcode"],
                "country": customer["country"],
            },
            "line_items": line_items,
            "payment_method": "bacs",
            "payment_method_title": "Direct Bank Transfer",
            "set_paid": status == "completed",
        }

        action = "DRY  " if dry_run else "CREATE"
        print(
            f"  {action} order #{i + 1:02d} status={status:12s} "
            f"customer={customer['first']:6s} items={len(line_items)}"
        )

        _post("orders", payload, dry_run)
        if not dry_run:
            time.sleep(0.15)

    print(f"  Result: {target} {'would be ' if dry_run else ''}created")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without making any API calls.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-seed even if data is already detected.",
    )
    args = parser.parse_args()

    # Validate env vars
    missing = []
    if not STORE_URL:
        missing.append("STORE_URL")
    if not SEED_KEY:
        missing.append("WC_SEED_KEY")
    if not SEED_SECRET:
        missing.append("WC_SEED_SECRET")
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        print("Set them in .env and run: set -a; source .env; set +a", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("=" * 60)
        print("DRY RUN — no API calls will be made")
        print("=" * 60)

    print(f"Store : {STORE_URL}")
    print(f"Flags : dry_run={args.dry_run}, force={args.force}")

    # Seed products
    new_ids = seed_products(args.dry_run, args.force)

    # Collect existing IDs if none were just created
    product_ids = new_ids
    if not product_ids and not args.dry_run:
        product_ids = _get_existing_product_ids()
        if product_ids:
            print(f"\n  Using {len(product_ids)} existing SEED- product IDs for orders.")

    # Seed orders
    seed_orders(product_ids or list(range(1, 21)), args.dry_run, args.force)

    print("\nDone.")


if __name__ == "__main__":
    main()
