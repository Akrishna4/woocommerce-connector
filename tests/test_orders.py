"""
test_orders.py — Tests for order tools.

Coverage:
  - list_orders: success path, empty results, pagination headers, truncation,
                 invalid status, 401 auth error
  - get_order: success, 404
  - search_orders: success path, empty-query and over-length-query validation
  - 5xx retry: retry then success, all retries exhausted
"""

from __future__ import annotations

import httpx
import pytest
import respx
from dataclasses import replace

from woocommerce_connector.auth import Settings
from woocommerce_connector.client import (
    AuthError,
    NotFoundError,
    UpstreamError,
    WooCommerceClient,
)
from woocommerce_connector import tools
from woocommerce_connector.tools import _sanitize_query

BASE = "http://localhost:8080/wp-json/wc/v3"


@pytest.fixture
async def client(settings):
    async with WooCommerceClient(settings) as c:
        yield c


# ---------------------------------------------------------------------------
# list_orders — success and filters
# ---------------------------------------------------------------------------

@respx.mock
async def test_list_orders_success(client, settings, raw_order):
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[raw_order],
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    )
    result = await tools.list_orders(client, settings)
    assert result["total"] == 1
    assert result["items"][0]["id"] == 42
    assert result["next_page"] is None
    assert result["truncated"] is False


@respx.mock
async def test_list_orders_empty(client, settings):
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[],
            headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"},
        )
    )
    result = await tools.list_orders(client, settings)
    assert result["items"] == []
    assert result["total"] == 0
    assert result["next_page"] is None


@respx.mock
async def test_list_orders_pagination_next_page(client, settings, raw_order):
    """next_page is set when current page < total_pages (within max_pages cap)."""
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[raw_order],
            headers={"X-WP-Total": "100", "X-WP-TotalPages": "5"},
        )
    )
    result = await tools.list_orders(client, settings, page=1)
    assert result["total_pages"] == 5
    assert result["next_page"] == 2
    assert result["truncated"] is False  # 5 < max_pages=10


@respx.mock
async def test_list_orders_truncated_when_exceeds_max_pages(settings, raw_order):
    """truncated=True when total_pages exceeds max_pages."""
    low_cap = replace(settings, max_pages=2)
    async with WooCommerceClient(low_cap) as c:
        respx.get(f"{BASE}/orders").mock(
            return_value=httpx.Response(
                200,
                json=[raw_order],
                headers={"X-WP-Total": "100", "X-WP-TotalPages": "5"},
            )
        )
        result = await tools.list_orders(c, low_cap, page=1)
        assert result["truncated"] is True
        assert result["next_page"] == 2  # still within cap


@respx.mock
async def test_list_orders_no_next_page_on_last_page(client, settings, raw_order):
    """next_page is None on the last page."""
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[raw_order],
            headers={"X-WP-Total": "2", "X-WP-TotalPages": "2"},
        )
    )
    result = await tools.list_orders(client, settings, page=2)
    assert result["next_page"] is None


@respx.mock
async def test_list_orders_auth_error(client, settings):
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    with pytest.raises(AuthError):
        await tools.list_orders(client, settings)


async def test_list_orders_invalid_status_raises(client, settings):
    with pytest.raises(ValueError, match="Invalid status"):
        await tools.list_orders(client, settings, status="deleted")


async def test_list_orders_invalid_after_date_raises(client, settings):
    with pytest.raises(ValueError, match="ISO 8601"):
        await tools.list_orders(client, settings, after="not-a-date")


# ---------------------------------------------------------------------------
# get_order
# ---------------------------------------------------------------------------

@respx.mock
async def test_get_order_success(client, settings, raw_order):
    respx.get(f"{BASE}/orders/42").mock(
        return_value=httpx.Response(200, json=raw_order)
    )
    result = await tools.get_order(client, settings, 42)
    assert result["id"] == 42
    assert result["status"] == "processing"
    assert result["total"] == "99.99"


@respx.mock
async def test_get_order_not_found(client, settings):
    respx.get(f"{BASE}/orders/9999").mock(
        return_value=httpx.Response(404, json={"message": "Not found"})
    )
    with pytest.raises(NotFoundError):
        await tools.get_order(client, settings, 9999)


# ---------------------------------------------------------------------------
# search_orders
# ---------------------------------------------------------------------------

@respx.mock
async def test_search_orders_success(client, settings, raw_order):
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[raw_order],
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    )
    result = await tools.search_orders(client, settings, "Alice")
    assert result["items"][0]["id"] == 42


def test_search_orders_empty_query_raises():
    with pytest.raises(ValueError, match="empty"):
        _sanitize_query("")
    with pytest.raises(ValueError, match="empty"):
        _sanitize_query("   ")


def test_search_orders_long_query_raises():
    with pytest.raises(ValueError, match="exceeds maximum length"):
        _sanitize_query("a" * 201)


def test_search_orders_sanitizes_unsafe_chars():
    result = _sanitize_query("hello<script>alert(1)</script>world")
    assert "<" not in result
    assert ">" not in result
    assert "(" not in result
    assert "world" in result


# ---------------------------------------------------------------------------
# 5xx retry: retry then success
# ---------------------------------------------------------------------------

@respx.mock
async def test_5xx_retry_then_success(settings, raw_order, instant_backoff):
    """First request returns 500; second succeeds."""
    s = replace(settings, max_retries=1)
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(500, json={"message": "Server error"})
        return httpx.Response(
            200,
            json=[raw_order],
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )

    respx.get(f"{BASE}/orders").mock(side_effect=side_effect)

    async with WooCommerceClient(s) as c:
        result = await tools.list_orders(c, s)
        assert result["total"] == 1
        assert call_count == 2


@respx.mock
async def test_5xx_all_retries_exhausted_raises(settings, instant_backoff):
    """All retries return 500 → UpstreamError."""
    s = replace(settings, max_retries=0)

    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(500, json={"message": "Server error"})
    )
    async with WooCommerceClient(s) as c:
        with pytest.raises(UpstreamError):
            await tools.list_orders(c, s)


# ---------------------------------------------------------------------------
# search_orders — exact-ID shortcut
# ---------------------------------------------------------------------------

@respx.mock
async def test_search_orders_digit_query_exact_id_found(client, settings, raw_order):
    """Digit-only query: exact order is fetched and placed first with marker."""
    search_order = {**raw_order, "id": 99}  # different id returned by search
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[search_order],
            headers={"X-WP-Total": "30", "X-WP-TotalPages": "1"},
        )
    )
    # Exact-ID GET returns the order with id=180
    exact_order = {**raw_order, "id": 180}
    respx.get(f"{BASE}/orders/180").mock(
        return_value=httpx.Response(200, json=exact_order)
    )

    result = await tools.search_orders(client, settings, "180")

    # First item must be the exact match with marker
    assert result["items"][0]["id"] == 180
    assert result["items"][0]["_exact_id_match"] is True
    # ONLY that item is returned, hiding the others
    assert len(result["items"]) == 1
    assert result["total"] == 1


@respx.mock
async def test_search_orders_digit_query_exact_id_not_found(client, settings, raw_order):
    """Digit-only query: 404 on exact GET is silently ignored."""
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[raw_order],
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    )
    respx.get(f"{BASE}/orders/9999").mock(
        return_value=httpx.Response(404, json={"message": "Not found"})
    )

    result = await tools.search_orders(client, settings, "9999")

    # No marker, original search results returned
    assert result["items"][0]["id"] == 42
    assert "_exact_id_match" not in result["items"][0]
    assert result["total"] == 1


@respx.mock
async def test_search_orders_non_digit_query_no_extra_request(client, settings, raw_order):
    """Non-digit query: no extra GET /orders/{id} call is made."""
    search_route = respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[raw_order],
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    )

    result = await tools.search_orders(client, settings, "Alice")

    assert result["items"][0]["id"] == 42
    # Only one request made (the search), no order-by-id request
    assert len(respx.calls) == 1
    assert respx.calls[0].request.url.path.endswith("/orders")


@respx.mock
async def test_search_orders_digit_deduplicates_exact_match(client, settings, raw_order):
    """If the exact-ID order is also in the search results, it's not duplicated."""
    # Search returns order 42 (same as the raw_order)
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[raw_order],  # id=42
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    )
    # Exact GET also returns id=42
    respx.get(f"{BASE}/orders/42").mock(
        return_value=httpx.Response(200, json=raw_order)
    )

    result = await tools.search_orders(client, settings, "42")

    # Only one item — no duplicate
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == 42
    assert result["items"][0]["_exact_id_match"] is True


@respx.mock
async def test_search_orders_digit_page2_no_extra_request(client, settings, raw_order):
    """Exact-ID shortcut only fires on page 1."""
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[raw_order],
            headers={"X-WP-Total": "30", "X-WP-TotalPages": "1"},
        )
    )

    result = await tools.search_orders(client, settings, "180", page=2)

    # No exact-id marker, and only one HTTP request made
    for item in result["items"]:
        assert "_exact_id_match" not in item
    assert len(respx.calls) == 1


@respx.mock
async def test_search_orders_all_requests_are_get(client, settings, raw_order):
    """Both search and exact-ID requests use GET only."""
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200, json=[raw_order], headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"}
        )
    )
    respx.get(f"{BASE}/orders/180").mock(
        return_value=httpx.Response(200, json={**raw_order, "id": 180})
    )

    await tools.search_orders(client, settings, "180")

    for call in respx.calls:
        assert call.request.method == "GET"

