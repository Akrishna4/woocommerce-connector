"""
test_readonly.py — Read-only guarantee tests.

Asserts that no non-GET HTTP method is ever issued — not even to the wire.

Coverage:
  - POST, PUT, PATCH, DELETE all raise RuntimeError before any network call
  - The error message explicitly mentions "read-only"
  - GET is allowed and passes through normally
  - A custom tracking transport confirms that the rejected methods never reach the wire
"""

from __future__ import annotations

import httpx
import pytest
import respx

from woocommerce_connector.client import WooCommerceClient

BASE = "http://localhost:8080/wp-json/wc/v3"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_non_get_method_raises_before_network(settings, method):
    """Any non-GET method must raise RuntimeError immediately — no network call."""
    async with WooCommerceClient(settings) as client:
        with pytest.raises(RuntimeError, match="read-only"):
            await client._request(method, "/orders")


@respx.mock
async def test_get_method_allowed(settings):
    """GET requests must pass through without error."""
    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            200,
            json=[],
            headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"},
        )
    )
    async with WooCommerceClient(settings) as client:
        resp = await client.get("/orders")
        assert resp.status_code == 200


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_no_non_get_sent_to_wire(settings, method):
    """Verify that rejected methods are blocked BEFORE the request hits the network layer."""
    sent_methods: list[str] = []

    class _TrackingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            sent_methods.append(request.method)
            return httpx.Response(200, json={})

    # Bypass the context manager and inject a tracking transport directly.
    async with httpx.AsyncClient(
        base_url=settings.base_url,
        auth=settings.auth,
        transport=_TrackingTransport(),
    ) as raw_client:
        wc = WooCommerceClient(settings)
        wc._client = raw_client  # inject

        with pytest.raises(RuntimeError, match="read-only"):
            await wc._request(method, "/orders", json={"status": "processing"})

    assert method not in sent_methods, (
        f"{method} was sent to the wire — read-only guarantee is violated"
    )
