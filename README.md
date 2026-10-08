# WooCommerce Connector

A read-only [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that lets an AI agent read orders and inventory from a WooCommerce store.

**Package name:** `woocommerce-connector` | **Python package:** `woocommerce_connector`

---

## Documentation and what the agent can do

- [`docs/AGENT_CAPABILITIES.md`](docs/AGENT_CAPABILITIES.md): full description of what the agent can and cannot do
- [`docs/DESIGN_NOTE.md`](docs/DESIGN_NOTE.md): how a merchant ops team would use the tools, and why they are shaped this way
- [`mcp_tools.json`](mcp_tools.json): the MCP tool specification (input schemas, output shapes, error cases)

**In short**

- **Can:** read, list, and search orders and products, and check stock levels.
- **Cannot:** create, update, refund, or cancel orders, or edit inventory. The connector only ever issues GET requests.
- **Key limitations:**
  - Order search does not cover order notes; use `get_order` for ID lookups.
  - The 429 and 5xx retry paths are proven by mocked tests only, because a local WooCommerce store does not rate-limit.
  - The local HTTPS patch is for development only and must never be used in production.
  - Tested against a local store only, not a live TLS-hosted store.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker + Docker Compose | For the local WordPress/WooCommerce stack |
| Python 3.11+ | Connector runtime |
| WooCommerce store running | See step 1 below |
| Two sets of API keys | Read-only (for connector) + Read/Write (for seeding) |

---

## Setup in Under 10 Minutes

### Step 0 — Get the code

```bash
git clone https://github.com/Akrishna4/woocommerce-connector.git
cd woocommerce-connector
```

### Step 1 — Start the local store

```bash
docker compose up -d
```

Wait ~30 seconds, then open http://localhost:8080 and complete the WordPress install wizard (language, admin user, site title).

After WordPress is installed:
1. Install the **WooCommerce** plugin (Plugins → Add New → search "WooCommerce" → Install → Activate).
2. Run the WooCommerce setup wizard or skip it.
3. Go to **WooCommerce → Settings → Advanced → Page setup** and confirm the pages are created.
4. Go to **Settings → Permalinks → Post name** (or any setting other than "Plain") and click **Save Changes**. This activates the REST API.

### Step 2 — Apply the local HTTPS patch

WooCommerce requires HTTPS for Basic auth. This one-line patch makes WordPress treat plain-HTTP requests as HTTPS for **local development only**.

```bash
docker compose exec wordpress sed -i "1a \$_SERVER['HTTPS'] = 'on';" /var/www/html/wp-config.php
```

> **⚠️ Production warning:** This patch disables a transport security check. It must **never** be applied in production. In production, use real HTTPS and keep `wp-config.php` unchanged.

Verify it worked:

```bash
docker compose exec wordpress head -3 /var/www/html/wp-config.php
# Expected second line: $_SERVER['HTTPS'] = 'on';
```

### Step 3 — Create API keys

1. Log into WordPress admin at http://localhost:8080/wp-admin
2. Go to **WooCommerce → Settings → Advanced → REST API** → **Add key**
3. Create a **Read-only** key (for the connector): copy the Consumer Key and Secret.
4. Create a second **Read/Write** key (for seeding demo data): copy separately.

### Step 4 — Install the connector

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### Step 5 — Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in:
#   STORE_URL=http://localhost:8080
#   WC_CONSUMER_KEY=ck_...   (read-only key)
#   WC_CONSUMER_SECRET=cs_... (read-only secret)
#   WC_SEED_KEY=ck_...        (read/write key — for seeding only)
#   WC_SEED_SECRET=cs_...     (read/write secret)
```

Load the env vars:

```bash
set -a; source .env; set +a
```

### Step 6 — Seed demo data

```bash
python scripts/seed_fake_data.py
```

Creates 20 fictional products (skipped if SKU already exists) and 30 fictional orders (skipped if the `[seed-demo]` marker is detected in the `customer_note`). The script uses a paginated scan capped at 10 pages / 1000 orders to find existing orders. Mix of statuses: 9× processing, 10× completed, 4× on-hold, 3× cancelled, 2× refunded, 1× pending, 1× failed. All names are clearly fictional; all emails use `example.com`.

Run with `--dry-run` first to preview what will be created without making any API calls:

```bash
python scripts/seed_fake_data.py --dry-run
```

Use `--force` to re-seed even if data is already detected.
Use `--reset` to delete ONLY seeded products (SKU prefix `SEED-`) and orders (with the `[seed-demo]` marker). Supports `--dry-run` and will ask for confirmation unless `--yes` is passed.

> **Note:** These options are for setup and testing only, and operate completely outside the MCP connector.

### Step 7 — Run the smoke test

```bash
python scripts/smoke_test.py
```

Calls all six tools against the live store, prints results (including `search_orders` empirical behavior), and exits non-zero on failure.

### Step 8 — Run the test suite

```bash
pytest
```

All tests use mocked HTTP via `respx` — no live store required.

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `STORE_URL` | ✅ | — | WooCommerce store URL |
| `WC_CONSUMER_KEY` | ✅ | — | Read-only consumer key |
| `WC_CONSUMER_SECRET` | ✅ | — | Read-only consumer secret |
| `WC_SEED_KEY` | Seeding only | — | Read/write key for `seed_fake_data.py` |
| `WC_SEED_SECRET` | Seeding only | — | Read/write secret for `seed_fake_data.py` |
| `REDACT_PII` | ❌ | `true` | Set to `false` to disable PII redaction |
| `WC_RPM` | ❌ | `60` | Client-side token-bucket rate limit (req/min) |
| `WC_MAX_PAGES` | ❌ | `10` | Max pages per paginated list call |
| `WC_MAX_RETRIES` | ❌ | `3` | Retry cap for 429/5xx/timeouts |
| `WC_ALLOW_INSECURE` | ❌ | `false` | Allow plain HTTP to non-local hosts (dev only) |

---

## Registering the Server in an MCP Client

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "woocommerce": {
      "command": "/path/to/your/.venv/bin/wc-mcp-server",
      "env": {
        "STORE_URL": "http://localhost:8080",
        "WC_CONSUMER_KEY": "ck_...",
        "WC_CONSUMER_SECRET": "cs_..."
      }
    }
  }
}
```

### Generic MCP client (stdio transport)

Start the server:

```bash
wc-mcp-server
# or:
python -m woocommerce_connector.server
```

The server reads from stdin and writes to stdout using the MCP stdio protocol.

### Streamable HTTP transport (optional)

> **Note:** Supported by MCP SDK 2.3.0. Which transport Agent Studio or other
> clients require has **not** been verified. Use stdio first.

```bash
wc-mcp-server --http               # binds to 127.0.0.1:8001/mcp
```

> **⚠️ Security warning:** The HTTP transport has **no authentication of its own**, and the tools can read order data. Keep it bound to `127.0.0.1`, or place it behind an authenticated reverse proxy. Do not expose it directly to a network.

**Supported transports summary:**

| Transport | Status | Default |
|---|---|---|
| stdio | ✅ Verified | yes |
| Streamable HTTP (`/mcp`) | ⚠️ Implemented, not client-verified | no |
| SSE | ❌ Not implemented | — |

---

## Available Tools

| Tool | Description |
|---|---|
| `list_orders` | List orders with optional status/date/customer filters |
| `get_order` | Retrieve a single order by ID |
| `search_orders` | Search orders by free text (see search behavior below) |
| `list_products` | List products with optional stock_status/category filters |
| `get_product` | Retrieve a single product by ID, including stock info |
| `search_products` | Search products by name (partial) or SKU (exact) |

See [`mcp_tools.json`](mcp_tools.json) for full input schemas and output shapes.

---

## search_orders — Observed Search Behavior

> **Note:** Tested empirically by running `python scripts/smoke_test.py` against the live seeded store.

Observed results against the seeded demo store (30 total orders):

| Query | Example | Results | Notes |
|---|---|---|---|
| Billing first name | `"Alice"` | 3/30 | ✅ Substring match on `billing.first_name` — returns only Alice's orders |
| Billing last name | `"Farnsworth"` | 3/30 | ✅ Substring match on `billing.last_name` |
| Full email | `"alice.farnsworth@example.com"` | 3/30 | ✅ Email is matched — same orders as first/last name for this demo |
| Email domain fragment | `"example.com"` | 30/30 | ⚠️ Matches ALL orders — every billing email ends in `@example.com`. In production with diverse email domains, this would be more selective. |
| City name | `"Springfield"` | 9/30 | ✅ City IS searchable (not documented in WooCommerce v3 API docs; confirmed empirically). |
| Numeric order ID | `"180"` | 1/30 | ✅ Returns ONLY the matching order (`_exact_id_match` injected) |

**Key findings from real testing:**
- City name **is** matched by WooCommerce search (undocumented; confirmed live).
- Email matches work even when emails are redacted in the output. An agent can probe for an email address to verify its existence by searching for it.
- Email domain fragment (`example.com`) matches every order when all customers share the domain — in production with real, diverse emails this is a useful filter.
- If a purely numeric query matches an existing order ID (e.g. `"180"`), the connector's exact-ID shortcut returns **only** that order (marked `_exact_id_match: true`) and hides other matches like phone or postcode fragments. For browsing, use `list_orders` filters instead of `search_orders`.
- If a purely numeric query does NOT match an existing order ID (e.g. `"17"`), WooCommerce performs a normal substring search (which might match addresses or phones).
- Name searches are selective: first-name or last-name queries return only that customer's orders.
- `customer_note` is **not** indexed by WooCommerce search — searching for text that only appears in order notes will return 0 results.
- Search is case-insensitive substring matching — not ranked, not stemmed, no field scoping.
- Use `list_orders(status=…, after=…, before=…)` for precise filtering.

---

## search_products — Observed Search Behavior

Tested against the seeded store (20 products). `query` is a partial, case-insensitive product-name search. `sku` is an exact-match filter using WooCommerce's `sku` parameter.

| Parameter | Example | Results | Notes |
|---|---|---|---|
| `query` (name) | `query="Desk"` | 2/20 | ✅ Matches "Apex Standing Desk" and "Luminos Desk Lamp" |
| `sku` (exact) | `sku="SEED-DSK-016"` | 1/20 | ✅ Exact SKU match |
| `query` (exact SKU) | `query="SEED-DSK-016"` | 1/20 | ✅ Exact SKU match via shortcut (`_exact_sku_match` injected); `total` is 1 |
| `query` (partial SKU) | `query="SEED-DSK"` | 0/20 | ❌ Partial SKU is NOT matched by `query` or `sku` |
| `query` (nonsense) | `query="XyZzY123"` | 0/20 | ✅ Correctly returns 0 results |

**Key findings:** Use `query` for partial name searches. Use `sku` for exact SKU lookups. Combine with `stock_status` for precise queries.

---

## Example Agent Prompts with Tool-Call Traces

The traces below are illustrative. Order IDs, names, and totals are examples in the shape of real responses, not a recording of one specific run.

### Prompt 1: "Which orders from the last 7 days are still processing?"

```
Agent → list_orders(status="processing", after="<ISO date 7 days ago>")

Response: {
  "items": [
    {"id": 157, "status": "processing",
     "billing": {"first_name": "Alice", "city": "Springfield", ...}},
    {"id": 156, "status": "processing",
     "billing": {"first_name": "Bob", "city": "Cypress Creek", ...}},
    {"id": 155, "status": "processing",
     "billing": {"first_name": "Carol", "city": "Brockway", ...}}
  ],
  "total": 3, "page": 1, "next_page": null
}

Agent: "There are 3 processing orders from the last 7 days: #157 (Springfield),
#156 (Cypress Creek), and #155 (Brockway). The oldest is #155."
```

### Prompt 2: "Which products are low on stock or out of stock?"

```
Agent → list_products(stock_status="outofstock")
Response: {"items": [
  {"name": "Pulsar Gaming Chair", "sku": "SEED-CHR-019", "stock_quantity": 0},
  {"name": "Stratos 4K Webcam", "sku": "SEED-4KW-020", "stock_quantity": 0}
], "total": 2}

Agent → list_products(stock_status="instock", per_page=30)
Response: {"items": [...18 products, 3 with stock_quantity ≤ 5...]}

Agent: "2 products are out of stock: Pulsar Gaming Chair and Stratos 4K Webcam.
3 products are running low (≤5 units): Apex Standing Desk (3),
Comet Portable Monitor (1), Quasar Wireless Charger (2)."
```

### Prompt 3: "What is the status of order 180?"

```
Agent → get_order(order_id=180)

Response: {
  "id": 180, "status": "failed", "total": "138.99",
  "billing": {"first_name": "Homer", "city": "Springfield", "country": "US",
              "last_name": "[redacted]", "email": "[redacted]"},
  "line_items": [
    {"name": "Prism Webcam 1080p", "quantity": 1},
    {"name": "Vortex USB-C Hub", "quantity": 1}
  ],
  "customer_note": "[seed-demo] order #29",
  "_untrusted_fields": ["customer_note"]
}

Agent: "Order #180 is currently failed. It contains a Prism Webcam 1080p and a
Vortex USB-C Hub totaling $138.99, shipping to Springfield, US."
```

---

## Running Utility Scripts

```bash
# Seed demo data (idempotent)
python scripts/seed_fake_data.py

# Smoke test all tools against live store
python scripts/smoke_test.py

# Verify MCP protocol end-to-end via stdio
python scripts/mcp_client_check.py

# Scan tracked files for secrets
python scripts/secret_scan.py
```

A schema drift test (`tests/test_schema_drift.py`) is also included to ensure `mcp_tools.json` stays exactly in sync with the live server.

---

## Development Notes

- **Auth:** HTTP Basic only. No OAuth query-param fallback is implemented (weaker; not needed with the wp-config.php patch).
- **Extra request on product search:** a page-1 `search_products` call with `query` also makes one exact-SKU request (`sku=<query>`), so it uses two requests from the rate-limit budget instead of one.
- **Rate limiting:** Client-side token bucket at 60 req/min by default. The local dev store has no real rate limits; 429 handling is proven via mocked tests (`tests/test_rate_limits.py`).
- **PII:** Redacted fields: `last_name`, `email`, `phone`, `address_1`, `address_2`, `postcode`. City, state, and country are always returned.
- **Untrusted text:** `customer_note`, `description_sanitized`, and `short_description_sanitized` are HTML-stripped and truncated to 500 chars. The `_untrusted_fields` key in each response labels these fields.
