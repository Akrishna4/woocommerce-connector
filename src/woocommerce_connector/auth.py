"""
auth.py — Credential loading and transport-safety validation.

Loads STORE_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET from environment variables.
Fails fast with a clear error if any required variable is missing.
Secrets are never logged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1"})


@dataclass
class Settings:
    """All runtime configuration for the WooCommerce connector.

    Constructed by :func:`load_settings`; do not instantiate directly in
    production code — use the loader so validation is always applied.
    """

    store_url: str
    consumer_key: str
    consumer_secret: str
    allow_insecure: bool = False
    rpm: int = 60           # token-bucket rate limit (requests per minute)
    max_pages: int = 10     # maximum pages per paginated list call
    max_retries: int = 3    # retry cap for 429 / 5xx / timeouts
    redact_pii: bool = True

    @property
    def auth(self) -> tuple[str, str]:
        """HTTP Basic auth tuple (consumer_key, consumer_secret)."""
        return (self.consumer_key, self.consumer_secret)

    @property
    def base_url(self) -> str:
        """Base URL for all WooCommerce v3 API calls."""
        return self.store_url.rstrip("/") + "/wp-json/wc/v3"


def _validate_transport(store_url: str, allow_insecure: bool) -> None:
    """Enforce transport safety: plain-HTTP non-local URLs are blocked unless explicitly allowed.

    Raises:
        ValueError: If STORE_URL is plain HTTP on a non-local host and
                    WC_ALLOW_INSECURE is not set.
    """
    parsed = urlparse(store_url)
    if parsed.scheme != "http":
        return  # HTTPS or other scheme — fine

    host = (parsed.hostname or "").lower()
    is_local = host in _LOCAL_HOSTS

    if is_local:
        # Local plain HTTP is allowed (wp-config.php patch makes Basic auth work)
        logger.warning(
            "STORE_URL is plain HTTP on localhost. "
            "Basic auth works here due to the wp-config.php HTTPS patch "
            "(local dev only — never use in production)."
        )
    elif allow_insecure:
        logger.warning(
            "WC_ALLOW_INSECURE=true: sending Basic auth credentials over plain HTTP "
            "to a non-local host (%s). NEVER use this configuration in production.",
            parsed.hostname,
        )
    else:
        raise ValueError(
            f"STORE_URL '{store_url}' uses plain HTTP and the host is not localhost "
            "or 127.0.0.1. Sending Basic auth over plain HTTP exposes your API credentials. "
            "Use HTTPS, or set WC_ALLOW_INSECURE=true (dev only — never in production)."
        )


def load_settings() -> Settings:
    """Load and validate all connector configuration from environment variables.

    Required variables: STORE_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET.
    Optional variables and their defaults are documented in .env.example.

    Raises:
        RuntimeError: If any required variable is missing.
        ValueError: If STORE_URL uses plain HTTP on a non-local host and
                    WC_ALLOW_INSECURE is not true.
    """
    missing: list[str] = []

    store_url = os.environ.get("STORE_URL", "").strip()
    consumer_key = os.environ.get("WC_CONSUMER_KEY", "").strip()
    consumer_secret = os.environ.get("WC_CONSUMER_SECRET", "").strip()

    if not store_url:
        missing.append("STORE_URL")
    if not consumer_key:
        missing.append("WC_CONSUMER_KEY")
    if not consumer_secret:
        missing.append("WC_CONSUMER_SECRET")

    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Copy .env.example to .env and fill in your WooCommerce credentials."
        )

    allow_insecure = os.environ.get("WC_ALLOW_INSECURE", "").lower() == "true"
    _validate_transport(store_url, allow_insecure)

    def _int_env(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError:
                logger.warning(
                    "Environment variable %s must be an integer; using default %d.",
                    name,
                    default,
                )
        return default

    return Settings(
        store_url=store_url,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        allow_insecure=allow_insecure,
        rpm=_int_env("WC_RPM", 60),
        max_pages=_int_env("WC_MAX_PAGES", 10),
        max_retries=_int_env("WC_MAX_RETRIES", 3),
        redact_pii=os.environ.get("REDACT_PII", "true").lower() != "false",
    )
