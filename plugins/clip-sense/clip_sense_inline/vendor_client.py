"""BaseVendorClient — minimal HTTP client base with retry & cancel hooks.

Vendored from ``openakita_plugin_sdk.contrib.vendor_client`` (SDK 0.6.0) into
clip-sense in 0.7.0; see ``clip_sense_inline/__init__.py``.

Design rules (audit3 hardening, preserved verbatim):

- All calls have a hard timeout (default 60s, configurable per call).
- Retry only safe failures: 429 / 5xx / network exceptions.  4xx
  (except 429) and content-moderation responses are not retried — they
  surface to ErrorCoach immediately.
- Body-level moderation detection: even a 5xx response whose body matches
  a moderation regex is not retried.
- ``cancel_task()`` is mandatory in the contract.
- ``httpx`` is imported lazily so importing this module is cheap.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_NEVER_RETRY_STATUSES = frozenset({400, 401, 403, 404, 422})

_DEFAULT_MODERATION_PATTERN = re.compile(
    r"(content[\s_-]?polic|moderation|sensitive[\s_-]?content|"
    r"data[\s_-]?inspection[\s_-]?failed|inappropriat|"
    r"\u654f\u611f|\u98ce\u63a7|\u5185\u5bb9\u5b89\u5168|\u8fdd\u89c4|\u5ba1\u6838\u4e0d\u901a\u8fc7)",
    re.IGNORECASE,
)


ERROR_KIND_NETWORK = "network"
ERROR_KIND_TIMEOUT = "timeout"
ERROR_KIND_RATE_LIMIT = "rate_limit"
ERROR_KIND_AUTH = "auth"
ERROR_KIND_NOT_FOUND = "not_found"
ERROR_KIND_MODERATION = "moderation"
ERROR_KIND_CLIENT = "client"
ERROR_KIND_SERVER = "server"
ERROR_KIND_UNKNOWN = "unknown"


def _classify(status: int | None, body: Any, *, is_timeout: bool = False) -> str:
    if is_timeout:
        return ERROR_KIND_TIMEOUT
    if status is None:
        return ERROR_KIND_NETWORK
    if status == 429:
        return ERROR_KIND_RATE_LIMIT
    if status in (401, 403):
        return ERROR_KIND_AUTH
    if status == 404:
        return ERROR_KIND_NOT_FOUND
    if 400 <= status < 500:
        return ERROR_KIND_CLIENT
    if 500 <= status < 600:
        return ERROR_KIND_SERVER
    return ERROR_KIND_UNKNOWN


class VendorError(Exception):
    """Raised when a vendor call fails after retries."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: Any = None,
        retryable: bool = False,
        kind: str = ERROR_KIND_UNKNOWN,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.retryable = retryable
        self.kind = kind


@dataclass
class _CallSpec:
    method: str
    url: str
    headers: dict[str, str]
    json_body: Any
    params: dict[str, Any] | None
    timeout: float


class BaseVendorClient:
    """Thin async HTTP client with sensible retry + cancel contract.

    Subclasses provide:

    - ``base_url``           — vendor base URL (string or property)
    - ``auth_headers()``     — return Authorization etc.
    - ``cancel_task(task_id)`` — call vendor's cancel endpoint (or raise
      ``NotImplementedError`` if unsupported).
    """

    base_url: str = ""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_backoff: float = 0.8,
        retry_max_backoff: float = 8.0,
        retry_statuses: frozenset[int] = _DEFAULT_RETRY_STATUSES,
        moderation_pattern: re.Pattern[str] | None = _DEFAULT_MODERATION_PATTERN,
    ) -> None:
        if base_url is not None:
            self.base_url = base_url
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))
        self.retry_max_backoff = max(0.0, float(retry_max_backoff))
        self.retry_statuses = retry_statuses
        self.moderation_pattern = moderation_pattern

    def auth_headers(self) -> dict[str, str]:
        """Subclass override — return e.g. ``{"Authorization": "Bearer ..."}``."""
        return {}

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        """Cancel a remote task. Subclasses must override."""
        raise NotImplementedError(
            f"{type(self).__name__}.cancel_task() not implemented — "
            "either call the vendor's cancel endpoint or raise NotImplementedError "
            "explicitly so the host can disable the cancel button.",
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> Any:
        """Make a single HTTP request with retry. Returns parsed JSON or text.

        Raises :class:`VendorError` on terminal failure.
        """
        try:
            import httpx
        except ImportError as e:
            raise RuntimeError(
                "httpx is required for BaseVendorClient — `pip install httpx`",
            ) from e

        url = (
            path
            if path.startswith(("http://", "https://"))
            else f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        )
        headers = {**self.auth_headers(), **(extra_headers or {})}
        spec = _CallSpec(
            method=method.upper(),
            url=url,
            headers=headers,
            json_body=json_body,
            params=params,
            timeout=float(timeout) if timeout is not None else self.timeout,
        )

        retries = self.max_retries if max_retries is None else max(0, int(max_retries))
        last_error: VendorError | None = None

        async with httpx.AsyncClient(timeout=spec.timeout) as client:
            for attempt in range(retries + 1):
                try:
                    resp = await client.request(
                        spec.method,
                        spec.url,
                        json=spec.json_body,
                        params=spec.params,
                        headers=spec.headers,
                    )
                except httpx.TimeoutException as e:
                    last_error = VendorError(
                        f"Timeout after {spec.timeout:.1f}s: {e}",
                        status=None,
                        body=None,
                        retryable=True,
                        kind=ERROR_KIND_TIMEOUT,
                    )
                    if attempt < retries:
                        await asyncio.sleep(self._backoff(attempt))
                        continue
                    raise last_error from e
                except httpx.NetworkError as e:
                    last_error = VendorError(
                        f"Network error: {e}",
                        status=None,
                        body=None,
                        retryable=True,
                        kind=ERROR_KIND_NETWORK,
                    )
                    if attempt < retries:
                        await asyncio.sleep(self._backoff(attempt))
                        continue
                    raise last_error from e

                if resp.status_code < 400:
                    body_ok = self._safe_body(resp)
                    if self._is_moderation(body_ok):
                        raise VendorError(
                            f"Content moderation rejected the request "
                            f"(HTTP {resp.status_code}): {self._short(body_ok)}",
                            status=resp.status_code,
                            body=body_ok,
                            retryable=False,
                            kind=ERROR_KIND_MODERATION,
                        )
                    return self._parse(resp)

                body = self._safe_body(resp)
                if self._is_moderation(body):
                    raise VendorError(
                        f"Content moderation rejected the request "
                        f"(HTTP {resp.status_code}): {self._short(body)}",
                        status=resp.status_code,
                        body=body,
                        retryable=False,
                        kind=ERROR_KIND_MODERATION,
                    )
                if (
                    resp.status_code in self.retry_statuses
                    and resp.status_code not in _NEVER_RETRY_STATUSES
                    and attempt < retries
                ):
                    last_error = VendorError(
                        f"Retryable HTTP {resp.status_code}",
                        status=resp.status_code,
                        body=body,
                        retryable=True,
                        kind=_classify(resp.status_code, body),
                    )
                    await asyncio.sleep(self._backoff(attempt))
                    continue

                raise VendorError(
                    f"HTTP {resp.status_code}: {self._short(body)}",
                    status=resp.status_code,
                    body=body,
                    retryable=False,
                    kind=_classify(resp.status_code, body),
                )

        if last_error:
            raise last_error
        raise VendorError("Unknown vendor failure", retryable=False, kind=ERROR_KIND_UNKNOWN)

    async def get_json(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post_json(self, path: str, json_body: Any, **kw: Any) -> Any:
        return await self.request("POST", path, json_body=json_body, **kw)

    def _backoff(self, attempt: int) -> float:
        base = min(self.retry_backoff * (2 ** attempt), self.retry_max_backoff)
        return random.uniform(0.0, base)

    @staticmethod
    def _parse(resp: Any) -> Any:
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            try:
                return resp.json()
            except Exception:  # noqa: BLE001
                pass
        return resp.text

    @staticmethod
    def _safe_body(resp: Any) -> Any:
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            try:
                return resp.text
            except Exception:  # noqa: BLE001
                return None

    @staticmethod
    def _short(body: Any) -> str:
        if body is None:
            return ""
        s = body if isinstance(body, str) else str(body)
        return s[:240] + ("\u2026" if len(s) > 240 else "")

    def _is_moderation(self, body: Any) -> bool:
        """Return True iff body matches the moderation pattern."""
        if self.moderation_pattern is None or body is None:
            return False
        if isinstance(body, str):
            text = body
        else:
            try:
                text = str(body)
            except Exception:  # noqa: BLE001
                return False
        if not text:
            return False
        return bool(self.moderation_pattern.search(text))
