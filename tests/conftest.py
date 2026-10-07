"""
conftest.py — Shared fixtures for the WooCommerce connector test suite.
"""

from __future__ import annotations

import asyncio

import pytest

from woocommerce_connector.auth import Settings


# ---------------------------------------------------------------------------
# Settings fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings() -> Settings:
    """Test settings pointing at localhost with insecure mode enabled.

    RPM is set very high so the token bucket never blocks during tests.
    """
    return Settings(
        store_url="http://localhost:8080",
        consumer_key="ck_test_key_00000000000000000000000000000000",
        consumer_secret="cs_test_secret_0000000000000000000000000000",
        allow_insecure=True,
        rpm=6000,       # high — never blocks in tests
        max_pages=10,
        max_retries=3,
        redact_pii=True,
    )


@pytest.fixture
def settings_no_redact(settings: Settings) -> Settings:
    """Settings with PII redaction disabled."""
    from dataclasses import replace
    return replace(settings, redact_pii=False)


@pytest.fixture
def settings_low_retries(settings: Settings) -> Settings:
    """Settings with max_retries=1 for fast retry tests."""
    from dataclasses import replace
    return replace(settings, max_retries=1)


@pytest.fixture
def settings_no_retries(settings: Settings) -> Settings:
    """Settings with max_retries=0 for exhaustion tests."""
    from dataclasses import replace
    return replace(settings, max_retries=0)


# ---------------------------------------------------------------------------
# Backoff / sleep patching
# ---------------------------------------------------------------------------

@pytest.fixture
def instant_backoff(monkeypatch):
    """Replace _backoff with zero delay and no-op asyncio.sleep in client module.

    Use this fixture in tests that exercise retry paths to avoid actual sleeping.
    """
    import woocommerce_connector.client as cm

    monkeypatch.setattr(cm, "_backoff", lambda *a, **kw: 0.0)

    async def _noop_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr(cm.asyncio, "sleep", _noop_sleep)


# ---------------------------------------------------------------------------
# Raw WooCommerce response fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def raw_order() -> dict:
    """Minimal realistic raw order as returned by WooCommerce REST API v3."""
    return {
        "id": 42,
        "status": "processing",
        "date_created": "2024-01-15T10:30:00",
        "date_modified": "2024-01-15T10:35:00",
        "total": "99.99",
        "currency": "USD",
        "billing": {
            "first_name": "Alice",
            "last_name": "Smith",
            "email": "alice@example.com",
            "phone": "555-1234",
            "address_1": "123 Main St",
            "address_2": "Apt 4",
            "city": "Springfield",
            "state": "IL",
            "postcode": "62701",
            "country": "US",
        },
        "shipping": {
            "first_name": "Alice",
            "last_name": "Smith",
            "address_1": "123 Main St",
            "address_2": "Apt 4",
            "city": "Springfield",
            "state": "IL",
            "postcode": "62701",
            "country": "US",
        },
        "line_items": [
            {
                "id": 1,
                "product_id": 10,
                "name": "Fancy Widget",
                "quantity": 2,
                "total": "49.98",
            }
        ],
        "customer_note": "Please leave at the door.",
        "payment_method_title": "Credit Card",
    }


@pytest.fixture
def raw_product() -> dict:
    """Minimal realistic raw product as returned by WooCommerce REST API v3."""
    return {
        "id": 10,
        "name": "Fancy Widget",
        "status": "publish",
        "sku": "WIDGET-001",
        "price": "24.99",
        "regular_price": "29.99",
        "sale_price": "24.99",
        "stock_status": "instock",
        "stock_quantity": 50,
        "manage_stock": True,
        "categories": [{"id": 1, "name": "Widgets", "slug": "widgets"}],
        "description": "<p>A <strong>fancy</strong> widget for all occasions.</p>",
        "short_description": "<p>Fancy widget short desc.</p>",
    }
