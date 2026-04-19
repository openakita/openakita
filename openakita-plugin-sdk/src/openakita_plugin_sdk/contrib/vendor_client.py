"""BaseVendorClient — minimal HTTP client base with retry & cancel hooks.

Replaces the pattern in seedance-video / tongyi-image where each plugin
hand-rolled `requests` / `httpx` boilerplate plus its own retry logic.

Design rules (audit3 hardening):

- **All calls have a hard timeout** (default 60s, configurable per call).
- **Retry only safe failures**: 429 / 5xx / network exceptions.  4xx
  (except 429) and content-moderation responses are *not* retried — they
  surface to ``ErrorCoach`` immediately.
- **cancel_task()** is mandatory in the contract: subclasses must implement
  it (or raise ``NotImplementedError`` if the vendor truly cannot cancel
  — but document it).
- **httpx is imported lazily** so importing the SDK does not pay the cost.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_NEVER_RETRY_STATUSES = frozenset({400, 401, 403, 404, 422})


class VendorError(Exception):
    """Raised when a vendor call fails after retries.

    Attributes:
        status: HTTP status code (or ``None`` for transport errors).
        body: Decoded body (str / dict / None).
        retryable: Whether *this specific* failure could be safely retried.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: Any = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.retryable = retryable


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

    All other helpers (``request``, ``get_json``, ``post_json``) are
    inherited and handle retries.
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
    ) -> None:
        if base_url is not None:
            self.base_url = base_url
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))
        self.retry_max_backoff = max(0.0, float(retry_max_backoff))
        self.retry_statuses = retry_statuses

    # ── overridable ──────────────────────────────────────────────────────

    def auth_headers(self) -> dict[str, str]:
        """Subclass override — return e.g. ``{"Authorization": "Bearer ..."}``."""
        return {}

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002 — interface
        """Cancel a remote task.

        Subclasses **must** override.  If the vendor genuinely does not
        support cancellation, raise ``NotImplementedError`` and document it.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.cancel_task() not implemented — "
            "either call the vendor's cancel endpoint or raise NotImplementedError "
            "explicitly so the host can disable the cancel button.",
        )

    # ── core HTTP helpers ────────────────────────────────────────────────

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
        """Make a single HTTP request with retry.  Returns parsed JSON or text.

        Raises :class:`VendorError` on terminal failure.
        """
        try:
            import httpx
        except ImportError as e:
            raise RuntimeError(
                "httpx is required for BaseVendorClient — `pip install httpx`",
            ) from e

        url = path if path.startswith(("http://", "https://")) else f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
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
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    last_error = VendorError(
                        f"Network error: {e}",
                        status=None,
                        body=None,
                        retryable=True,
                    )
                    if attempt < retries:
                        await asyncio.sleep(self._backoff(attempt))
                        continue
                    raise last_error from e

                if resp.status_code < 400:
                    return self._parse(resp)

                body = self._safe_body(resp)
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
                    )
                    await asyncio.sleep(self._backoff(attempt))
                    continue

                raise VendorError(
                    f"HTTP {resp.status_code}: {self._short(body)}",
                    status=resp.status_code,
                    body=body,
                    retryable=False,
                )

        # Defensive: should never reach here
        if last_error:
            raise last_error
        raise VendorError("Unknown vendor failure", retryable=False)

    async def get_json(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post_json(self, path: str, json_body: Any, **kw: Any) -> Any:
        return await self.request("POST", path, json_body=json_body, **kw)

    # ── helpers ──────────────────────────────────────────────────────────

    def _backoff(self, attempt: int) -> float:
        base = min(self.retry_backoff * (2 ** attempt), self.retry_max_backoff)
        # full jitter
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
        return s[:240] + ("…" if len(s) > 240 else "")
