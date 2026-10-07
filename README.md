# WooCommerce Connector

A read-only [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that lets an AI agent read orders and inventory from a WooCommerce store.

**Package name:** `woocommerce-connector` | **Python package:** `woocommerce_connector`

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

Creates 20 fictional products (idempotent — skipped if already present) and 30 fictional orders (skipped if ≥ 25 seeded orders already exist). Mix of statuses: 9× processing, 10× completed, 4× on-hold, 3× cancelled, 2× refunded, 1× pending, 1× failed. All names are clearly fictional; all emails use `example.com`.

Run with `--dry-run` first to preview what will be created without making any API calls:

```bash
python scripts/seed_fake_data.py --dry-run
```

Use `--force` to re-seed even if data is already detected.

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

> **Note:** Supported by MCP SDK 2.3.0.  Which transport Agent Studio or other
> clients require has **not** been verified.  Use stdio first.

```bash
wc-mcp-server --http               # binds to 127.0.0.1:8001/mcp
wc-mcp-server --http --host 0.0.0.0 --port 9000
```

**Supported transports summary:**
| Transport | Status | Default |
|---|---|---|
| stdio | ✅ Verified | yes |
| Streamable HTTP (`/mcp`) | ✅ Implemented, not client-verified | no |
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
| `search_products` | Search products by name, SKU, or keyword (see below) |

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
| Numeric order ID | `"180"` | 1/30 | ❌ Unreliable — digit sequence matches many unrelated fields. Use `get_order(order_id=N)` for precise lookup. |

**Key findings from real testing:**
- City name **is** matched by WooCommerce search (undocumented; confirmed live).
- Email matches work even when emails are redacted in the output. An agent can probe for an email address to verify its existence by searching for it.
- Email domain fragment (`example.com`) matches every order when all customers share the domain — in production with real, diverse emails this is a useful filter.
- Numeric order ID search is **not reliable** — the digit string appears in too many fields. Always use `get_order(order_id=N)` for precise lookup by ID.
- Name searches are selective: first-name or last-name queries return only that customer's orders.
- `customer_note` is **not** indexed by WooCommerce search — searching for text that only appears in order notes will return 0 results.
- Search is case-insensitive substring matching — not ranked, not stemmed, no field scoping.
- Use `list_orders(status=…, after=…, before=…)` for precise filtering.

---

## search_products — Observed Search Behavior

Search queries the WooCommerce product `search` parameter. Tested against the seeded store (20 products).

| Query | Example | Results | Notes |
|---|---|---|---|
| Product name fragment | `"Desk"` | 2/20 | ✅ Matches "Apex Standing Desk" and "Luminos Desk Lamp" |
| SKU prefix | `"SEED-DSK"` | 1/20 | ✅ SKU is matched exactly |
| With stock filter | `query="Desk", stock_status="instock"` | 2/20 | ✅ Filters applied correctly |

**Key findings:** Product name and SKU are both indexed. Combine with `stock_status` for precise queries.

---

## Example Agent Prompts with Tool-Call Traces

### Prompt 1: "Which orders from the last 7 days are still processing?"

```
Agent → list_orders(status="processing", after="2024-01-08")

Response: {
  "items": [
    {"id": 101, "status": "processing", "total": "79.99",
     "billing": {"first_name": "Alice", "city": "Springfield", ...},
     "line_items": [{"name": "Zephyr Wireless Keyboard", "quantity": 1}]},
    ...
  ],
  "total": 5, "page": 1, "next_page": null
}

Agent: "There are 5 processing orders from the last 7 days. The oldest is order #98 
placed on Jan 9 for a Luminos Desk Lamp, shipping to Ogdenville. Orders #101-#103 
are all from Springfield customers."
```

### Prompt 2: "Which products are low on stock or out of stock?"

```
Agent → list_products(stock_status="outofstock")
Response: {"items": [{"name": "Pulsar Gaming Chair", "stock_quantity": 0}, ...], "total": 2}

Agent → list_products(stock_status="instock", per_page=30)
Response: {"items": [...20 products, 3 with stock_quantity ≤ 5...]}

Agent: "2 products are out of stock: Pulsar Gaming Chair and Stratos 4K Webcam.
3 products are running low (≤5 units): Apex Standing Desk (3), 
Comet Portable Monitor (2), Quasar Wireless Charger (4)."
```

### Prompt 3: "What is the status of order 123?"

```
Agent → get_order(order_id=123)

Response: {
  "id": 123, "status": "on-hold", "total": "149.00",
  "billing": {"first_name": "Bob", "city": "Shelbyville", "country": "US",
              "last_name": "[redacted]", "email": "[redacted]"},
  "line_items": [{"name": "Aether Noise-Cancel Headset", "quantity": 1}],
  "customer_note": "[seed-demo] order #06"
}

Agent: "Order #123 is currently on-hold. It contains 1× Aether Noise-Cancel Headset 
totaling $149.00, shipping to Shelbyville, US. The order may need manual review 
before it can proceed."
```

---

## Running Utility Scripts

```bash
# Seed demo data (idempotent)
python scripts/seed_fake_data.py

# Smoke test all tools against live store
python scripts/smoke_test.py

# Scan tracked files for secrets
python scripts/secret_scan.py
```

---

## Development Notes

- **Auth:** HTTP Basic only. No OAuth query-param fallback is implemented (weaker; not needed with the wp-config.php patch).
- **Rate limiting:** Client-side token bucket at 60 req/min by default. The local dev store has no real rate limits; 429 handling is proven via mocked tests (`tests/test_rate_limits.py`).
- **PII:** Redacted fields: `last_name`, `email`, `phone`, `address_1`, `address_2`, `postcode`. City, state, and country are always returned.
- **Untrusted text:** `customer_note`, `description_sanitized`, and `short_description_sanitized` are HTML-stripped and truncated to 500 chars. The `_untrusted_fields` key in each response labels these fields.
