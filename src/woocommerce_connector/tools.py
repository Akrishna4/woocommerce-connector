"""
tools.py — The six read-only WooCommerce MCP tool functions.

All functions are async, accept a :class:`WooCommerceClient` and
:class:`Settings` instance, and return plain ``dict`` objects suitable
for JSON serialisation.

Input is validated before any HTTP call is made.  Output is normalised
through :mod:`models`.
"""

from __future__ import annotations

import re
from typing import Any

from .auth import Settings
from .client import WooCommerceClient
from .models import make_list_result, order_from_raw, product_from_raw

# ---------------------------------------------------------------------------
# Constants / validation helpers
# ---------------------------------------------------------------------------

_MAX_PER_PAGE = 30

_VALID_ORDER_STATUSES: frozenset[str] = frozenset({
    "pending", "processing", "on-hold", "completed",
    "cancelled", "refunded", "failed", "trash", "any",
})
_VALID_STOCK_STATUSES: frozenset[str] = frozenset({
    "instock", "outofstock", "onbackorder",
})

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?$")
_MAX_QUERY_LEN = 200
_UNSAFE_CHARS_RE = re.compile(r"[<>\"'%;()&+]")


def _clamp_per_page(per_page: int) -> int:
    """Clamp per_page to [1, _MAX_PER_PAGE]."""
    return max(1, min(per_page, _MAX_PER_PAGE))


def _validate_iso_date(value: str | None, name: str) -> None:
    """Raise ValueError if *value* is not a valid ISO 8601 date/datetime."""
    if value and not _ISO_DATE_RE.match(value):
        raise ValueError(
            f"'{name}' must be an ISO 8601 date: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS. "
            f"Got: {value!r}"
        )


def _normalize_iso_date(value: str | None) -> str | None:
    """Normalize a YYYY-MM-DD date-only string to YYYY-MM-DDT00:00:00."""
    if value and len(value) == 10:  # Matches exactly YYYY-MM-DD
        return f"{value}T00:00:00"
    return value


def _sanitize_query(query: str) -> str:
    """Validate and remove unsafe characters from a free-text search query.

    Raises:
        ValueError: If the query is empty or exceeds _MAX_QUERY_LEN.
    """
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty or whitespace-only.")
    if len(query) > _MAX_QUERY_LEN:
        raise ValueError(
            f"query exceeds maximum length of {_MAX_QUERY_LEN} characters "
            f"(got {len(query)})."
        )
    # Strip characters that could confuse server-side search parsers.
    return _UNSAFE_CHARS_RE.sub("", query)


def _pagination_from_response(response: Any) -> tuple[int, int]:
    """Parse X-WP-Total and X-WP-TotalPages from response headers."""
    total = int(response.headers.get("X-WP-Total", "0"))
    total_pages = int(response.headers.get("X-WP-TotalPages", "1"))
    return total, total_pages


# ---------------------------------------------------------------------------
# Order tools
# ---------------------------------------------------------------------------

async def list_orders(
    client: WooCommerceClient,
    settings: Settings,
    *,
    status: str | None = None,
    after: str | None = None,
    before: str | None = None,
    customer: int | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """List WooCommerce orders with optional filters.

    Args:
        client: Active :class:`WooCommerceClient` instance.
        settings: Connector settings (controls redaction, page cap, etc.).
        status: Filter by order status.  One of: pending, processing, on-hold,
                completed, cancelled, refunded, failed, trash, any.
        after: Return orders created after this date (YYYY-MM-DD or ISO 8601 date-time).
               Date-only values are treated as midnight at the start of that day in the store's timezone.
        before: Return orders created before this date (YYYY-MM-DD or ISO 8601 date-time).
                Date-only values are treated as midnight at the start of that day in the store's timezone.
        customer: Filter by WooCommerce customer ID.
        page: Page number (1-indexed).
        per_page: Results per page (max 30).

    Returns:
        A :func:`~models.make_list_result` dict with ``items``, ``total``,
        ``page``, ``total_pages``, ``next_page``, and ``truncated``.
    """
    if status is not None and status not in _VALID_ORDER_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Valid values: {sorted(_VALID_ORDER_STATUSES)}"
        )
    _validate_iso_date(after, "after")
    _validate_iso_date(before, "before")
    per_page = _clamp_per_page(per_page)

    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if status is not None:
        params["status"] = status
    if after is not None:
        params["after"] = _normalize_iso_date(after)
    if before is not None:
        params["before"] = _normalize_iso_date(before)
    if customer is not None:
        params["customer"] = customer

    response = await client.get("/orders", params=params)
    total, total_pages = _pagination_from_response(response)
    items = [order_from_raw(o, settings.redact_pii) for o in response.json()]
    return make_list_result(items, total, total_pages, page, settings.max_pages)


async def get_order(
    client: WooCommerceClient,
    settings: Settings,
    order_id: int,
) -> dict[str, Any]:
    """Retrieve a single WooCommerce order by ID.

    Args:
        client: Active :class:`WooCommerceClient` instance.
        settings: Connector settings.
        order_id: The WooCommerce order ID.

    Returns:
        A normalised :func:`~models.order_from_raw` dict.
    """
    response = await client.get(f"/orders/{order_id}")
    return order_from_raw(response.json(), settings.redact_pii)


async def search_orders(
    client: WooCommerceClient,
    settings: Settings,
    query: str,
    page: int = 1,
) -> dict[str, Any]:
    """Search orders using WooCommerce's built-in ``search`` parameter.

    The query is validated (non-empty, max 200 chars) and potentially
    unsafe characters are stripped before the request is sent.

    **Exact-ID shortcut:** When *query* consists entirely of digits and *page*
    is 1, the tool also fetches ``GET /orders/{id}`` via an extra GET request.
    If that order exists it is placed **first** in the result with an additional
    field ``"_exact_id_match": true``; any copy of that order already returned
    by the text search is removed to avoid duplication.

    **Total field:** ``total`` always reflects the WooCommerce search-API count
    (unchanged).  The injected exact-match item does not inflate ``total``.

    .. note::
        The observed search behaviour (which fields are matched, whether
        partial matches are supported, etc.) is documented in README.md and
        AGENT_CAPABILITIES.md after empirical testing against the seeded store.

    Args:
        client: Active :class:`WooCommerceClient` instance.
        settings: Connector settings.
        query: Search string.
        page: Page number (1-indexed).

    Returns:
        A :func:`~models.make_list_result` dict.
    """
    from .client import NotFoundError as _NotFoundError

    safe_query = _sanitize_query(query)
    params: dict[str, Any] = {
        "search": safe_query,
        "page": page,
        "per_page": _MAX_PER_PAGE,
    }
    response = await client.get("/orders", params=params)
    total, total_pages = _pagination_from_response(response)
    items = [order_from_raw(o, settings.redact_pii) for o in response.json()]

    # Exact-ID shortcut: only on page 1 and only for pure-digit queries.
    if page == 1 and safe_query.isdigit():
        try:
            exact_resp = await client.get(f"/orders/{int(safe_query)}")
            exact = order_from_raw(exact_resp.json(), settings.redact_pii)
            exact["_exact_id_match"] = True
            # ONLY that order is returned.
            items = [exact]
            total = 1
            total_pages = 1
        except _NotFoundError:
            pass  # 404 → no exact match, keep search results unchanged

    return make_list_result(items, total, total_pages, page, settings.max_pages)


# ---------------------------------------------------------------------------
# Product tools
# ---------------------------------------------------------------------------

async def list_products(
    client: WooCommerceClient,
    settings: Settings,
    *,
    stock_status: str | None = None,
    category: int | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """List WooCommerce products with optional filters.

    Each product includes ``stock_quantity`` and ``stock_status``.
    Product descriptions are HTML-stripped and truncated.

    Args:
        client: Active :class:`WooCommerceClient` instance.
        settings: Connector settings.
        stock_status: Filter by stock status (instock / outofstock / onbackorder).
        category: Filter by category **term ID** (integer, not slug).
                  IDs can be found in WooCommerce admin > Products > Categories.
        page: Page number (1-indexed).
        per_page: Results per page (max 30).

    Returns:
        A :func:`~models.make_list_result` dict.
    """
    if stock_status is not None and stock_status not in _VALID_STOCK_STATUSES:
        raise ValueError(
            f"Invalid stock_status {stock_status!r}. "
            f"Valid values: {sorted(_VALID_STOCK_STATUSES)}"
        )
    per_page = _clamp_per_page(per_page)

    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if stock_status is not None:
        params["stock_status"] = stock_status
    if category is not None:
        params["category"] = category

    response = await client.get("/products", params=params)
    total, total_pages = _pagination_from_response(response)
    items = [product_from_raw(p) for p in response.json()]
    return make_list_result(items, total, total_pages, page, settings.max_pages)


async def get_product(
    client: WooCommerceClient,
    settings: Settings,
    product_id: int,
) -> dict[str, Any]:
    """Retrieve a single WooCommerce product by ID.

    Includes ``manage_stock``, ``stock_quantity``, and ``stock_status``.
    Descriptions are HTML-stripped and truncated.

    Args:
        client: Active :class:`WooCommerceClient` instance.
        settings: Connector settings.
        product_id: The WooCommerce product ID.

    Returns:
        A normalised :func:`~models.product_from_raw` dict.
    """
    response = await client.get(f"/products/{product_id}")
    return product_from_raw(response.json())


async def search_products(
    client: WooCommerceClient,
    settings: Settings,
    *,
    query: str | None = None,
    sku: str | None = None,
    stock_status: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """Search products by name (partial text) or SKU (exact match).

    At least one of ``query`` or ``sku`` must be provided.

    **Exact-SKU shortcut:** When *query* is given and *sku* is **not** given,
    the tool also performs an extra ``sku=<query>`` lookup on page 1 via GET.
    If the exact-SKU lookup returns a product, it is placed **first** in the
    results with ``"_exact_sku_match": true``; the same product is removed from
    the name-search list if it appears there.  Partial-SKU matching via
    ``query`` is still **not** supported — ``query`` only matches product names.

    **Total field:** ``total`` reflects the WooCommerce name-search count
    (unchanged).  The injected SKU match does not inflate ``total``.

    Args:
        client: Active :class:`WooCommerceClient` instance.
        settings: Connector settings.
        query: Optional partial text search on product name. Validated and sanitized.
        sku: Optional exact SKU lookup (uses WooCommerce ``sku`` filter).
        stock_status: Optional filter ("instock", "outofstock", "onbackorder").
        page: Page number (1-indexed).
        per_page: Results per page (max 30).

    Returns:
        A :func:`~models.make_list_result` dict.
    """
    if not query and not sku:
        raise ValueError("Must provide at least 'query' or 'sku'.")

    per_page = _clamp_per_page(per_page)

    params: dict[str, Any] = {
        "page": page,
        "per_page": per_page,
    }
    if query:
        params["search"] = _sanitize_query(query)
    if sku:
        params["sku"] = sku
    if stock_status is not None:
        params["stock_status"] = stock_status

    response = await client.get("/products", params=params)
    total, total_pages = _pagination_from_response(response)
    items = [product_from_raw(p) for p in response.json()]

    # Exact-SKU shortcut: only when query is given, sku is not, on page 1.
    if query and not sku and page == 1:
        safe_q = _sanitize_query(query)
        sku_params: dict[str, Any] = {"sku": safe_q, "per_page": 1}
        if stock_status is not None:
            sku_params["stock_status"] = stock_status
        sku_response = await client.get("/products", params=sku_params)
        sku_hits = [product_from_raw(p) for p in sku_response.json()]
        if sku_hits:
            exact = sku_hits[0]
            exact["_exact_sku_match"] = True
            already_present = any(p["id"] == exact["id"] for p in items)
            # Remove duplicate from name-search results (same id).
            items = [p for p in items if p["id"] != exact["id"]]
            # Prepend the exact match.
            items = [exact] + items
            if not already_present:
                total += 1
                if total_pages == 0:
                    total_pages = 1

    return make_list_result(items, total, total_pages, page, settings.max_pages)
