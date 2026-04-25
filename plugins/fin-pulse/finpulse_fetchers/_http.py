"""Shared HTTP utilities for fin-pulse fetchers.

Centralises the ``httpx.AsyncClient`` factory so every fetcher inherits
the same timeout, UA string, and friendly rate-limit defaults. The
factory is intentionally tiny so unit tests can monkey-patch it with a
stubbed ``respx`` router.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

try:
    import httpx  # type: ignore
except ImportError as exc:  # pragma: no cover — httpx ships with the host
    raise RuntimeError("fin-pulse requires httpx (host dependency)") from exc


# Real-browser UA string. The previous ``Mozilla/5.0 (OpenAkita fin-pulse/1.0; ...)``
# banner was flagged as a bot by Cloudflare on every edge it protects. NewsNow's
# public aggregator is less noisy with a mainstream Chrome UA and the standard
# Accept-Language headers used by browser traffic.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def make_client(
    *,
    timeout: float = 15.0,
    extra_headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
    user_agent: str | None = None,
) -> httpx.AsyncClient:
    """Build a short-lived ``httpx.AsyncClient``.

    Fetchers own their client lifetime (``async with`` form) so we never
    leak sockets across tasks.

    ``user_agent`` overrides the shared Chrome UA — SEC EDGAR, for
    example, explicitly requires ``Name email@example.com`` per its
    public-access guidance and silently 403s on generic browser UAs.
    """
    headers = {
        "User-Agent": user_agent or DEFAULT_UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    if extra_headers:
        headers.update(extra_headers)
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=follow_redirects,
    )


async def jittered_sleep(base_ms: int = 100, spread_ms: int = 100) -> None:
    """Await a jittered delay — used by fetchers that burst multiple
    requests (NewsNow, eastmoney) to stay friendly with upstream.
    """
    ms = base_ms + random.randint(0, max(0, spread_ms))
    await asyncio.sleep(ms / 1000.0)


async def fetch_text(client: httpx.AsyncClient, url: str, **kw: Any) -> str:
    """GET ``url`` and return the body decoded as text (UTF-8 fallback)."""
    resp = await client.get(url, **kw)
    resp.raise_for_status()
    try:
        return resp.text
    except UnicodeDecodeError:
        return resp.content.decode("utf-8", errors="replace")


async def fetch_json(client: httpx.AsyncClient, url: str, **kw: Any) -> Any:
    """GET ``url`` and return the JSON body. Raises for non-2xx."""
    resp = await client.get(url, **kw)
    resp.raise_for_status()
    return resp.json()


__all__ = [
    "DEFAULT_UA",
    "fetch_json",
    "fetch_text",
    "jittered_sleep",
    "make_client",
]
