# Design Note — WooCommerce MCP Connector

## How a Merchant Operations Team Would Use These Tools

A merchant ops team uses this connector to answer two categories of question:

**Order status lookups** — The most frequent ops task is checking where a specific order is
and why it has not moved. An agent calling `get_order(order_id=N)` instantly retrieves
status, total, line items, shipping destination (city, state, country), and the customer's
sanitized note — everything a support rep needs to answer "where is order 123?" without
logging into the WooCommerce admin panel. `list_orders(status="on-hold")` combined with a
date filter surfaces stuck orders at scale; an agent can then summarise: "You have 4 orders
on-hold, the oldest placed 3 days ago, all shipping to Illinois."

**Inventory monitoring** — `list_products(stock_status="outofstock")` surfaces items that
need immediate restocking. `list_products(stock_status="instock")` with client-side
filtering on `stock_quantity ≤ N` identifies low-stock items before they go out of stock.
A daily agent prompt like "which products have fewer than 5 units?" can create an automated
reorder alert workflow without any custom dashboard.

---

## Why the Tool Set Is Shaped This Way

**Five tools, not fifteen.** WooCommerce exposes dozens of endpoints, but merchant ops
teams need only two entities — orders and products — to handle the day-to-day questions
that drive agent interactions. Adding webhooks, refunds, coupons, customers, or taxes as
tools would increase the attack surface and make the schema harder for an agent to reason
about without corresponding business value.

**Trimmed payloads, not raw responses.** A raw WooCommerce order response can exceed 5 KB
and contain dozens of fields irrelevant to operational queries (coupon codes, fee lines,
tax details, meta data arrays). By normalising to `OrderSummary` and `ProductSummary`,
the connector reduces context-window pressure, makes agent reasoning simpler, and avoids
accidentally surfacing internal fields. The `_untrusted_fields` label on `customer_note`
and product descriptions is an explicit reminder to the agent — and to any downstream
renderer — not to treat this content as trusted application output.

**PII redaction on by default.** Billing email, phone, last name, and full address are
redacted unless `REDACT_PII=false` is explicitly set. This means agents can answer
location-based questions ("which on-hold orders ship to Illinois?") using city, state, and
country — which are always returned — without being handed a PII-rich payload that the
agent has no legitimate need for. This design makes it safe to expose the connector to a
broader class of agents and users without a separate access-control layer. Note that
the WooCommerce API still allows searching by email; an agent can probe for an email address
to verify its existence even when the output is redacted.

**Security consideration — untrusted free text.** `customer_note` and product description
fields are authored by external parties (customers and product managers). They may contain
HTML, CSS, or JavaScript payloads intended to exploit browsers or LLM-based rendering
pipelines. The connector strips HTML tags and truncates these fields to 500 characters.
The `_untrusted_fields` key in every response explicitly labels which fields received this
treatment. Downstream systems rendering these values must independently apply output
escaping — the connector's stripping is a defence-in-depth measure, not a guarantee of
safety in all rendering contexts.

**Pagination cap.** The `WC_MAX_PAGES` cap (default 10) prevents a single agent query
from issuing hundreds of API calls against the upstream store. The `truncated=true` flag
signals when more data exists but was not returned, so agents can prompt the user or
request a narrower filter rather than silently missing records.
