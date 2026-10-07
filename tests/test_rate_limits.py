"""
test_rate_limits.py — Rate-limit and retry tests.

Coverage:
  - 429 with Retry-After header → retries and succeeds
  - 429 without Retry-After → falls back to exponential backoff, then succeeds
  - 429 with retries exhausted → RateLimitError
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
import respx

from woocommerce_connector.client import RateLimitError, WooCommerceClient

BASE = "http://localhost:8080/wp-json/wc/v3"


@respx.mock
async def test_429_with_retry_after_then_success(settings, instant_backoff):
    """429 with Retry-After: 0 should retry immediately and succeed."""
    s = replace(settings, max_retries=1)
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                429,
                json={"message": "Too Many Requests"},
                headers={"Retry-After": "0"},
            )
        return httpx.Response(
            200,
            json=[],
            headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"},
        )

    respx.get(f"{BASE}/orders").mock(side_effect=side_effect)

    async with WooCommerceClient(s) as client:
        resp = await client.get("/orders")
        assert resp.status_code == 200
        assert call_count == 2


@respx.mock
async def test_429_without_retry_after_uses_backoff(settings, instant_backoff):
    """429 with no Retry-After falls back to _backoff (patched to 0); retries and succeeds."""
    s = replace(settings, max_retries=1)
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, json={"message": "Too Many Requests"})
        return httpx.Response(
            200,
            json=[],
            headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"},
        )

    respx.get(f"{BASE}/orders").mock(side_effect=side_effect)

    async with WooCommerceClient(s) as client:
        resp = await client.get("/orders")
        assert resp.status_code == 200
        assert call_count == 2


@respx.mock
async def test_429_retries_exhausted_raises_rate_limit_error(settings, instant_backoff):
    """RateLimitError raised when max_retries=0 and 429 returned."""
    s = replace(settings, max_retries=0)

    respx.get(f"{BASE}/orders").mock(
        return_value=httpx.Response(
            429,
            json={"message": "Too Many Requests"},
            headers={"Retry-After": "0"},
        )
    )

    async with WooCommerceClient(s) as client:
        with pytest.raises(RateLimitError):
            await client.get("/orders")


@respx.mock
async def test_429_retry_after_respected(settings, instant_backoff):
    """Retry-After header value is read (sleep is patched, but we verify the retry happens)."""
    s = replace(settings, max_retries=1)
    retry_after_seen: list[str] = []
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                429,
                json={"message": "Too Many Requests"},
                headers={"Retry-After": "5"},
            )
        return httpx.Response(
            200,
            json=[],
            headers={"X-WP-Total": "0", "X-WP-TotalPages": "0"},
        )

    respx.get(f"{BASE}/orders").mock(side_effect=side_effect)

    async with WooCommerceClient(s) as client:
        resp = await client.get("/orders")
        assert resp.status_code == 200
        assert call_count == 2
