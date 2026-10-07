"""
models.py — Pydantic models and normalization helpers for WooCommerce API responses.

All public functions return plain ``dict`` objects (not Pydantic models) so they
serialize cleanly to JSON without extra conversion steps.

PII redaction
-------------
The :data:`PII_REDACT_FIELDS` constant lists the billing/shipping fields that are
replaced with ``"[redacted]"`` when ``redact_pii=True`` (the default).

City, state, and country are intentionally kept so agents can answer questions
like "which orders are stuck and where do they ship?".

Untrusted-text handling
-----------------------
``customer_note``, product ``description``, and ``short_description`` are
third-party text that may contain HTML or injection payloads.  Every such field
is HTML-stripped and truncated to :data:`MAX_UNTRUSTED_LEN` characters before
being included in any response.  Each response dict also contains an
``"_untrusted_fields"`` key listing which fields received this treatment so
callers know not to render them without further escaping.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------

#: Billing/shipping sub-fields redacted by default.
#: Kept visible: first_name, city, state, country (needed for location questions).
PII_REDACT_FIELDS: frozenset[str] = frozenset({
    "email",
    "phone",
    "address_1",
    "address_2",
    "postcode",
    "last_name",
})

_REDACTED = "[redacted]"


def _redact_address(addr: dict[str, Any], redact: bool) -> dict[str, Any]:
    """Return a copy of *addr* with PII fields replaced, or the original if redact=False."""
    if not redact:
        return dict(addr)
    return {k: (_REDACTED if k in PII_REDACT_FIELDS else v) for k, v in addr.items()}


# ---------------------------------------------------------------------------
# Untrusted-text sanitization
# ---------------------------------------------------------------------------

#: Characters to preserve after stripping HTML entities.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTITY_RE = re.compile(r"&[a-zA-Z#][a-zA-Z0-9]{0,6};")
_WHITESPACE_RE = re.compile(r"\s+")

#: Maximum length for any third-party text field after sanitization.
MAX_UNTRUSTED_LEN: int = 500


def _strip_html(text: str | None) -> str | None:
    """Remove HTML tags and collapse whitespace."""
    if text is None:
        return None
    cleaned = _HTML_TAG_RE.sub(" ", text)
    cleaned = _HTML_ENTITY_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def sanitize_untrusted(text: str | None) -> str | None:
    """Strip HTML and truncate to MAX_UNTRUSTED_LEN.

    Returns ``None`` if *text* is ``None`` or empty after stripping.
    """
    if text is None:
        return None
    cleaned = _strip_html(text)
    if not cleaned:
        return None
    if len(cleaned) > MAX_UNTRUSTED_LEN:
        return cleaned[:MAX_UNTRUSTED_LEN] + "\u2026"  # ellipsis
    return cleaned


# ---------------------------------------------------------------------------
# Internal Pydantic models (used only for field validation / documentation)
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
    id: int
    product_id: int
    name: str
    quantity: int
    total: str


class OrderSummary(BaseModel):
    id: int
    status: str
    date_created: str
    date_modified: str
    total: str
    currency: str
    billing: dict[str, Any]
    shipping: dict[str, Any]
    line_items: list[LineItem]
    customer_note: str | None
    payment_method_title: str | None


class ProductSummary(BaseModel):
    id: int
    name: str
    status: str
    sku: str
    price: str
    regular_price: str
    sale_price: str
    stock_status: str
    stock_quantity: int | None
    manage_stock: bool
    categories: list[str]
    description_sanitized: str | None
    short_description_sanitized: str | None


class ListResult(BaseModel):
    items: list[Any]
    total: int
    page: int
    total_pages: int
    next_page: int | None
    truncated: bool


# ---------------------------------------------------------------------------
# Raw → normalized converters
# ---------------------------------------------------------------------------

def order_from_raw(raw: dict[str, Any], redact_pii: bool = True) -> dict[str, Any]:
    """Convert a raw WooCommerce order dict to a trimmed, normalized response dict.

    PII redaction is applied to billing and shipping address fields according
    to :data:`PII_REDACT_FIELDS`.  ``customer_note`` is HTML-stripped and
    truncated.
    """
    billing = _redact_address(raw.get("billing", {}), redact_pii)
    shipping = _redact_address(raw.get("shipping", {}), redact_pii)

    line_items = [
        LineItem(
            id=item["id"],
            product_id=item.get("product_id", 0),
            name=item.get("name", ""),
            quantity=item.get("quantity", 0),
            total=item.get("total", "0"),
        ).model_dump()
        for item in raw.get("line_items", [])
    ]

    summary = OrderSummary(
        id=raw["id"],
        status=raw.get("status", ""),
        date_created=raw.get("date_created", ""),
        date_modified=raw.get("date_modified", ""),
        total=raw.get("total", "0"),
        currency=raw.get("currency", ""),
        billing=billing,
        shipping=shipping,
        line_items=line_items,
        customer_note=sanitize_untrusted(raw.get("customer_note")),
        payment_method_title=raw.get("payment_method_title"),
    )

    result = summary.model_dump()
    # Label third-party text fields so callers know not to trust them blindly.
    result["_untrusted_fields"] = ["customer_note"]
    return result


def product_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw WooCommerce product dict to a trimmed, normalized response dict.

    Product descriptions are HTML-stripped and truncated.
    """
    categories = [cat.get("name", "") for cat in raw.get("categories", [])]

    summary = ProductSummary(
        id=raw["id"],
        name=raw.get("name", ""),
        status=raw.get("status", "publish"),
        sku=raw.get("sku", ""),
        price=raw.get("price", "0"),
        regular_price=raw.get("regular_price", "0"),
        sale_price=raw.get("sale_price", ""),
        stock_status=raw.get("stock_status", "instock"),
        stock_quantity=raw.get("stock_quantity"),
        manage_stock=raw.get("manage_stock", False),
        categories=categories,
        description_sanitized=sanitize_untrusted(raw.get("description")),
        short_description_sanitized=sanitize_untrusted(raw.get("short_description")),
    )

    result = summary.model_dump()
    result["_untrusted_fields"] = ["description_sanitized", "short_description_sanitized"]
    return result


def make_list_result(
    items: list[dict[str, Any]],
    total: int,
    total_pages: int,
    page: int,
    max_pages: int,
) -> dict[str, Any]:
    """Build a paginated list response with :data:`next_page` and :data:`truncated` flags.

    Args:
        items: Normalized item dicts for the current page.
        total: Total matching records (from X-WP-Total header).
        total_pages: Total pages (from X-WP-TotalPages header).
        page: Current page number (1-indexed).
        max_pages: Connector-side page cap; when total_pages exceeds this,
                   ``truncated`` is set to True.
    """
    effective_max = min(total_pages, max_pages)
    next_page: int | None = page + 1 if page < effective_max else None
    truncated = total_pages > max_pages

    return ListResult(
        items=items,
        total=total,
        page=page,
        total_pages=total_pages,
        next_page=next_page,
        truncated=truncated,
    ).model_dump()
