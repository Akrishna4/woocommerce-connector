# Agent Capabilities — WooCommerce Connector

This document describes what an AI agent can and cannot do with the
`woocommerce-connector` MCP server, its known limitations, and the
recommended path to a production-grade deployment.

---

## What the Agent CAN Do

### Orders
| Action | Tool | Notes |
|---|---|---|
| List orders (all or filtered) | `list_orders` | Filter by status, date range, customer ID |
| Get a single order by ID | `get_order` | Full billing/shipping/line-item detail |
| Search orders by text | `search_orders` | See observed behavior below |
| Check order status | `get_order` or `list_orders(status=…)` | All WooCommerce statuses supported |
| Find stuck orders | `list_orders(status="on-hold")` | Combined with date filters |
| Find recent orders | `list_orders(after="YYYY-MM-DD")` | ISO 8601 date |

### Products / Inventory
| Action | Tool | Notes |
|---|---|---|
| List all products | `list_products` | With pagination |
| Filter by stock status | `list_products(stock_status=…)` | instock / outofstock / onbackorder |
| Filter by category | `list_products(category=ID)` | Category term ID (integer) |
| Check stock levels | `list_products` + `get_product` | `stock_quantity` and `stock_status` fields |
| Find out-of-stock items | `list_products(stock_status="outofstock")` | |
| Find low-stock items | `list_products(stock_status="instock")` + filter `stock_quantity` | No direct low-stock filter in API |
| Get full product detail | `get_product(product_id)` | Includes `manage_stock` flag |

---

## What the Agent CANNOT Do

- **Create, update, or cancel orders** — the connector issues GET requests only; any attempt raises `RuntimeError` before reaching the network.
- **Create or update products / edit inventory quantities** — same read-only enforcement.
- **Issue refunds** — no write operations.
- **Manage webhooks** — not implemented; real-time event delivery is out of scope.
- **Multi-store or multi-tenant credential switching** — one store URL and one key pair per server instance; there is no per-request credential isolation.
- **Authenticate as an end customer** — only merchant-level API keys are supported.
- **Access WooCommerce extensions** (subscriptions, memberships, etc.) — only core v3 endpoints.
- **Search products by keyword** — `list_products` does not expose a `search` parameter in the current tool set. Use `list_products` with `stock_status` or `category`, then filter client-side.

---

## search_orders — Observed Behavior (Empirical)

> Tested against the local demo store after seeding with `scripts/seed_fake_data.py`.
> Raw output captured by `scripts/smoke_test.py`.
> Counts shown are from 90 total orders (3 seed rounds of 30 during development).

| Query | Example | Results | Notes |
|---|---|---|---|
| Billing first name | `"Alice"` | 9/90 | ✅ Substring match on `billing.first_name` |
| Billing last name | `"Farnsworth"` | 9/90 | ✅ Substring match on `billing.last_name` |
| Full email | `"alice.farnsworth@example.com"` | 9/90 | ✅ Email is matched |
| Email domain fragment | `"example.com"` | 90/90 | ⚠️ Partial match — all 90 orders matched (all share the domain in demo data) |
| City name | `"Springfield"` | 27/90 | ✅ City **is** searchable (undocumented in WooCommerce v3 API docs; confirmed empirically) |
| Numeric order ID | `"128"` | 4/90 | ❌ Unreliable — digit appears in many unrelated order data fields |

**Key findings from real testing:**
- City name IS matched (27 orders for `"Springfield"` with 90 total). This behavior is not explicitly documented in the WooCommerce v3 REST API docs.
- Email domain fragments match all orders when customers share a domain. In production with diverse emails, this would be more selective.
- Numeric order ID search is **not reliable** — use `get_order(order_id=N)` for precise lookup.
- Name searches are selective: `"Alice"` returned exactly the orders placed by Alice Farnsworth.
- `customer_note` is **not** indexed by WooCommerce search — querying for text that appears only in order notes returns 0 results.
- Search is case-insensitive, simple substring matching — not ranked, not stemmed, no field scoping.
- For precise filtering, prefer `list_orders(status=…, after=…, before=…)`.

---

## Known Limitations

| Limitation | Details |
|---|---|
| **No real 429s on local store** | WooCommerce dev installs have no rate limiting; 429 retry logic is proven only through mocked tests (`test_rate_limits.py`). |
| **Basic search** | `search_orders` is simple substring match — no relevance ranking, no field scope. |
| **`customer_note` not searchable** | WooCommerce does not index `customer_note` in its search index. Queries that match only order notes return 0 results. |
| **Pagination cap** | Results are capped at `WC_MAX_PAGES` (default 10) per call; callers must page manually for larger data sets. `truncated=true` signals when more pages exist. |
| **PII caveats** | With `REDACT_PII=false`, all address fields including email and phone are returned. This setting must only be used in authorized internal contexts. |
| **HTML stripped, not escaped** | `customer_note`, `description_sanitized`, and `short_description_sanitized` are HTML-stripped and labeled via `_untrusted_fields`. The plain-text content of script block bodies will still appear in the output; agents reading this text cannot execute it, but downstream rendering contexts must still escape it. |
| **Category ID only** | `list_products(category=…)` accepts a WooCommerce term ID (integer), not a human-readable slug. |
| **No real-time freshness** | The connector pulls live data on each call; there is no caching or webhook subscription. Data is as fresh as the last API call. |

---

## Recommended Long-Term Approach (Production)

### Authentication
- Replace the `wp-config.php` patch with real TLS (certificate, HTTPS termination).
- Use a secrets manager (AWS Secrets Manager, Vault, GCP Secret Manager) for credentials instead of env var files.
- For multi-tenant deployments, implement per-request credential isolation (e.g. a per-tenant key store with an OAuth-style flow for key provisioning).

### Freshness
- Subscribe to WooCommerce webhooks (`order.updated`, `order.status_changed`, `product.updated`) to maintain a local read cache.
- This eliminates per-call latency and avoids rate-limit pressure on high-traffic stores.

### Caching
- A short-lived in-process or Redis cache (e.g. 30-second TTL) on list endpoints dramatically reduces load for high-frequency agent queries.

### Scalability
- The token-bucket limiter is in-process; for multi-process deployments, move rate-limit state to Redis.
- The `WC_MAX_PAGES` cap prevents runaway pagination; tune it based on expected data volumes.
