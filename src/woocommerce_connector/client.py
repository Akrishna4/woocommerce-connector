"""
client.py — Async HTTP client for WooCommerce REST API v3.

Features:
  - HTTP Basic authentication (only supported auth method)
  - Client-side token-bucket rate limiter (configurable via WC_RPM)
  - Automatic retry on 429 (honouring Retry-After), 5xx errors, and timeouts
  - Exponential backoff with jitter between retries
  - Read-only enforcement: any non-GET method raises RuntimeError immediately
  - Typed exceptions for all error conditions
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

from .auth import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------

class WooCommerceError(Exception):
    """Base exception for all WooCommerce connector errors."""


class AuthError(WooCommerceError):
    """Raised on HTTP 401 Unauthorized."""


class NotFoundError(WooCommerceError):
    """Raised on HTTP 404 Not Found."""


class RateLimitError(WooCommerceError):
    """Raised when rate-limit retries are exhausted."""


class UpstreamError(WooCommerceError):
    """Raised on 5xx errors or network/timeout failures after all retries."""


# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Async token-bucket rate limiter.

    Allows up to *rate_per_minute* requests per 60-second window.
    Callers await :meth:`acquire` before each request; it sleeps only when
    the bucket is empty.
    """

    def __init__(self, rate_per_minute: int) -> None:
        self._rate: float = rate_per_minute / 60.0      # tokens per second
        self._tokens: float = float(rate_per_minute)    # start full
        self._max: float = float(rate_per_minute)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until one token is available."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._max, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                self._tokens = 0.0
                await asyncio.sleep(wait)
            else:
                self._tokens -= 1.0


# ---------------------------------------------------------------------------
# WooCommerce HTTP client
# ---------------------------------------------------------------------------

class WooCommerceClient:
    """Async HTTP client for the WooCommerce REST API v3.

    Must be used as an async context manager::

        async with WooCommerceClient(settings) as client:
            response = await client.get("/orders", params={"page": 1})

    Only GET requests are issued; any attempt to use another HTTP method
    raises :class:`RuntimeError` before a network call is made.
    """

    _TIMEOUT = httpx.Timeout(10.0, connect=5.0)

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = _TokenBucket(settings.rpm)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "WooCommerceClient":
        self._client = httpx.AsyncClient(
            base_url=self._settings.base_url,
            auth=self._settings.auth,
            timeout=self._TIMEOUT,
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue a GET request with rate-limit and retry handling.

        Args:
            path: API path relative to the WooCommerce v3 base URL (e.g. ``/orders``).
            params: Optional query parameters.

        Returns:
            The successful :class:`httpx.Response`.

        Raises:
            AuthError: On HTTP 401.
            NotFoundError: On HTTP 404.
            RateLimitError: On 429 after all retries are exhausted.
            UpstreamError: On 5xx or network/timeout failures after retries.
        """
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Internal request dispatcher.

        Enforces read-only policy and retry logic.
        """
        if method.upper() != "GET":
            raise RuntimeError(
                f"Write operation blocked: '{method}' is not allowed. "
                "This connector is read-only and only issues GET requests."
            )

        assert self._client is not None, (
            "WooCommerceClient is not open — use it as 'async with WooCommerceClient(settings) as client:'"
        )

        max_retries = self._settings.max_retries

        for attempt in range(max_retries + 1):
            await self._bucket.acquire()

            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                logger.warning(
                    "Timeout on attempt %d/%d for %s %s: %s",
                    attempt + 1, max_retries + 1, method, path, exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise UpstreamError(f"Request timed out after {max_retries + 1} attempt(s).") from exc
            except httpx.NetworkError as exc:
                logger.warning(
                    "Network error on attempt %d/%d for %s %s: %s",
                    attempt + 1, max_retries + 1, method, path, exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise UpstreamError(f"Network error after {max_retries + 1} attempt(s): {exc}") from exc

            # --- HTTP-level error handling ---

            if response.status_code == 401:
                raise AuthError(
                    "Authentication failed (HTTP 401). "
                    "Check WC_CONSUMER_KEY and WC_CONSUMER_SECRET in your .env file."
                )

            if response.status_code == 404:
                raise NotFoundError(f"Resource not found (HTTP 404): {path}")

            if response.status_code == 429:
                retry_after_raw = response.headers.get("Retry-After")
                retry_after = float(retry_after_raw) if retry_after_raw else None
                delay = retry_after if retry_after is not None else _backoff(attempt)
                logger.warning(
                    "Rate limited (429) on attempt %d/%d — retrying in %.1fs (Retry-After=%s).",
                    attempt + 1, max_retries + 1, delay, retry_after_raw,
                )
                if attempt < max_retries:
                    await asyncio.sleep(delay)
                    continue
                raise RateLimitError(
                    f"Rate limit exceeded after {max_retries + 1} attempt(s). "
                    f"Last Retry-After header: {retry_after_raw!r}."
                )

            if response.status_code >= 500:
                logger.warning(
                    "Upstream error %d on attempt %d/%d for %s %s.",
                    response.status_code, attempt + 1, max_retries + 1, method, path,
                )
                if attempt < max_retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise UpstreamError(
                    f"Upstream returned HTTP {response.status_code} after "
                    f"{max_retries + 1} attempt(s)."
                )

            response.raise_for_status()
            return response

        # Unreachable; satisfies type checkers.
        raise UpstreamError("Exhausted retries with no successful response.")  # pragma: no cover


def _backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff with jitter.

    Returns delay in seconds, capped at *cap*.
    """
    delay = min(cap, base * (2 ** attempt))
    jitter = random.uniform(0.0, delay * 0.25)
    return delay + jitter
