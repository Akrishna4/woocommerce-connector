"""
test_products.py — Tests for product tools.

Coverage:
  - list_products: success, empty, by stock_status, invalid stock_status
  - get_product: success, 404
  - HTML stripping in description fields
  - stock fields are present and correct
"""

from __future__ import annotations

import httpx
import pytest
import respx

from woocommerce_connector.client import NotFoundError, WooCommerceClient
from woocommerce_connector import tools

BASE = "http://localhost:8080/wp-json/wc/v3"


@pytest.fixture
async def client(settings):
    async with WooCommerceClient(settings) as c:
        yield c


@respx.mock
async def test_list_products_success(client, settings, raw_product):
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(
            200,
            json=[raw_product],
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    )
    result = await tools.list_products(client, settings)
    assert result["total"] == 1
    item = result["items"][0]
    assert item["id"] == 10
    assert item["stock_status"] == "instock"
    assert item["stock_quantity"] == 50
    assert item["manage_stock"] is True
    assert result["next_page"] is None
    assert result["truncated"] is False


@respx.mock
async def test_list_products_empty(client, settings):
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(
            200,
            json=[],
            headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"},
        )
    )
    result = await tools.list_products(client, settings)
    assert result["items"] == []
    assert result["total"] == 0


@respx.mock
async def test_list_products_filter_by_stock_status(client, settings, raw_product):
    oos_product = {**raw_product, "stock_status": "outofstock", "stock_quantity": 0}
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(
            200,
            json=[oos_product],
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    )
    result = await tools.list_products(client, settings, stock_status="outofstock")
    assert result["items"][0]["stock_status"] == "outofstock"
    assert result["items"][0]["stock_quantity"] == 0


async def test_list_products_invalid_stock_status_raises(client, settings):
    with pytest.raises(ValueError, match="Invalid stock_status"):
        await tools.list_products(client, settings, stock_status="not_a_status")


@respx.mock
async def test_list_products_pagination(client, settings, raw_product):
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(
            200,
            json=[raw_product],
            headers={"X-WP-Total": "50", "X-WP-TotalPages": "3"},
        )
    )
    result = await tools.list_products(client, settings, page=1)
    assert result["total_pages"] == 3
    assert result["next_page"] == 2


@respx.mock
async def test_get_product_success(client, settings, raw_product):
    respx.get(f"{BASE}/products/10").mock(
        return_value=httpx.Response(200, json=raw_product)
    )
    result = await tools.get_product(client, settings, 10)
    assert result["id"] == 10
    assert result["sku"] == "WIDGET-001"
    assert result["manage_stock"] is True
    assert result["categories"] == ["Widgets"]


@respx.mock
async def test_get_product_not_found(client, settings):
    respx.get(f"{BASE}/products/9999").mock(
        return_value=httpx.Response(404, json={"message": "Not found"})
    )
    with pytest.raises(NotFoundError):
        await tools.get_product(client, settings, 9999)


@respx.mock
async def test_product_description_html_stripped(client, settings, raw_product):
    """description_sanitized must not contain HTML tags."""
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(
            200,
            json=[raw_product],
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    )
    result = await tools.list_products(client, settings)
    desc = result["items"][0]["description_sanitized"]
    assert desc is not None
    assert "<" not in desc
    assert ">" not in desc
    assert "fancy" in desc.lower()


@respx.mock
async def test_product_untrusted_fields_labeled(client, settings, raw_product):
    """Product response must include _untrusted_fields key."""
    respx.get(f"{BASE}/products/10").mock(
        return_value=httpx.Response(200, json=raw_product)
    )
    result = await tools.get_product(client, settings, 10)
    assert "_untrusted_fields" in result
    assert "description_sanitized" in result["_untrusted_fields"]
    assert "short_description_sanitized" in result["_untrusted_fields"]


# ---------------------------------------------------------------------------
# Search products
# ---------------------------------------------------------------------------

@respx.mock
async def test_search_products_success(client, settings, raw_product):
    """search_products returns sanitized results and hits the right endpoint."""
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(
            200,
            json=[raw_product, raw_product],
            headers={"X-WP-Total": "2", "X-WP-TotalPages": "1"},
        )
    )
    result = await tools.search_products(client, settings, query="desk", stock_status="instock", page=1, per_page=10)
    
    assert result["total"] == 2
    assert len(result["items"]) == 2
    
    # Check request params
    req = respx.calls.last.request
    assert req.url.params["search"] == "desk"
    assert req.url.params["stock_status"] == "instock"
    assert req.url.params["page"] == "1"
    assert req.url.params["per_page"] == "10"
    assert "sku" not in req.url.params


@respx.mock
async def test_search_products_sku_success(client, settings, raw_product):
    """search_products hits the sku parameter."""
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(
            200,
            json=[raw_product],
            headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"},
        )
    )
    result = await tools.search_products(client, settings, sku="SEED-DSK-016", stock_status="instock")
    
    assert result["total"] == 1
    
    # Check request params
    req = respx.calls.last.request
    assert req.url.params["sku"] == "SEED-DSK-016"
    assert req.url.params["stock_status"] == "instock"
    assert "search" not in req.url.params


async def test_search_products_missing_args(client, settings):
    """search_products raises ValueError if neither query nor sku is provided."""
    with pytest.raises(ValueError, match="Must provide at least"):
        await tools.search_products(client, settings)


@respx.mock
async def test_search_products_empty(client, settings):
    """search_products handles 0 results gracefully."""
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(
            200, json=[], headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"}
        )
    )
    result = await tools.search_products(client, settings, query="nonexistent")
    assert result["total"] == 0
    assert result["items"] == []


@respx.mock
async def test_search_products_pagination(client, settings, raw_product):
    """search_products exposes next_page appropriately."""
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(
            200, json=[raw_product], headers={"X-WP-Total": "3", "X-WP-TotalPages": "3"}
        )
    )
    result = await tools.search_products(client, settings, query="desk", page=2)
    assert result["total_pages"] == 3
    assert result["next_page"] == 3


@respx.mock
async def test_search_products_401(client, settings):
    """search_products raises AuthError on 401."""
    respx.get(f"{BASE}/products").mock(
        return_value=httpx.Response(401, json={"message": "Invalid key"})
    )
    from woocommerce_connector.client import AuthError
    import pytest
    with pytest.raises(AuthError):
        await tools.search_products(client, settings, query="desk")


@respx.mock
async def test_search_products_429_retry(client, settings, raw_product):
    """search_products retries on 429."""
    route = respx.get(f"{BASE}/products")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json=[raw_product], headers={"X-WP-Total": "1", "X-WP-TotalPages": "1"}),
    ]
    result = await tools.search_products(client, settings, query="desk")
    assert result["total"] == 1
    assert route.call_count == 2
