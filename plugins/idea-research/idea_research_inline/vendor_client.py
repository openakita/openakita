"""Vendored, slim HTTPX-based vendor client base — Phase 0.

The full client (auth header injection, exponential retry, JSON helper,
streaming) is fleshed out in Phase 3. We define the error taxonomy here
so the rest of the codebase can already raise / catch the right types.
"""

from __future__ import annotations

from typing import Any


class VendorError(Exception):
    """Base error for all vendor-side failures."""

    error_kind: str = "unknown"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class VendorAuthError(VendorError):
    error_kind = "auth"


class VendorQuotaError(VendorError):
    error_kind = "quota"


class VendorRateLimitError(VendorError):
    error_kind = "rate_limit"


class VendorTimeoutError(VendorError):
    error_kind = "timeout"


class VendorNetworkError(VendorError):
    error_kind = "network"


class VendorFormatError(VendorError):
    error_kind = "format"


class VendorClient:
    """Skeleton vendor client.

    Phase 3 will add ``async def request_json``, ``async def stream``,
    retry policy, and per-vendor base-URL plumbing.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        default_timeout_s: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_timeout_s = default_timeout_s

    def update_api_key(self, key: str | None) -> None:
        self.api_key = key

    async def aclose(self) -> None:
        """Release underlying httpx client — implemented in Phase 3."""


__all__ = [
    "VendorAuthError",
    "VendorClient",
    "VendorError",
    "VendorFormatError",
    "VendorNetworkError",
    "VendorQuotaError",
    "VendorRateLimitError",
    "VendorTimeoutError",
]
