"""博查 Bocha provider — 国内推荐.

API: ``POST https://api.bochaai.com/v1/web-search``
Docs: https://api.bochaai.com/

Auto-detect priority: 10 (highest — first try in mainland China context).
"""

from __future__ import annotations

import logging
from typing import Any

from ....config import settings
from ..base import (
    AuthFailedError,
    MissingCredentialError,
    NetworkUnreachableError,
    RateLimitedError,
    SearchResult,
)
from ..registry import register

logger = logging.getLogger(__name__)


class BochaProvider:
    id = "bocha"
    label = "博查 Bocha"
    requires_credential = True
    auto_detect_order = 10
    signup_url = "https://api.bochaai.com"
    docs_url = "https://api.bochaai.com/docs"

    _ENDPOINT = "https://api.bochaai.com/v1/web-search"

    def is_available(self) -> bool:
        return bool((settings.bocha_api_key or "").strip())

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        region: str = "wt-wt",  # bocha 不需要 region；保持签名一致即可
        safesearch: str = "moderate",
        timeout_seconds: float = 0.0,
    ) -> list[SearchResult]:
        api_key = (settings.bocha_api_key or "").strip()
        if not api_key:
            raise MissingCredentialError(
                "BOCHA_API_KEY not configured", provider_id=self.id
            )

        payload = {
            "query": query,
            "freshness": "noLimit",
            "summary": True,
            "count": min(max(1, max_results), 50),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else 30.0

        return await _post_and_parse(
            url=self._ENDPOINT,
            headers=headers,
            json_body=payload,
            timeout=timeout,
            provider_id=self.id,
            extractor=_extract_bocha_results,
        )

    async def news_search(self, *args: Any, **kwargs: Any) -> list[SearchResult] | None:
        # 博查目前没有独立的新闻接口；返回 None 让 runtime 跳到下一家
        return None


async def _post_and_parse(
    *,
    url: str,
    headers: dict,
    json_body: dict,
    timeout: float,
    provider_id: str,
    extractor,
) -> list[SearchResult]:
    """Shared POST → JSON → results helper. Maps HTTP errors to ProviderError subclasses."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=headers, json=json_body)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise NetworkUnreachableError(
            f"{provider_id} transport failure: {type(exc).__name__}: {exc}",
            provider_id=provider_id,
        ) from exc
    except httpx.HTTPError as exc:
        raise NetworkUnreachableError(
            f"{provider_id} HTTP error: {type(exc).__name__}: {exc}",
            provider_id=provider_id,
        ) from exc

    if resp.status_code in (401, 403):
        raise AuthFailedError(
            f"{provider_id} rejected credential (HTTP {resp.status_code})",
            provider_id=provider_id,
        )
    if resp.status_code == 429:
        raise RateLimitedError(
            f"{provider_id} rate-limited (HTTP 429)",
            provider_id=provider_id,
        )
    if resp.status_code >= 500:
        raise NetworkUnreachableError(
            f"{provider_id} upstream error (HTTP {resp.status_code})",
            provider_id=provider_id,
        )
    if resp.status_code >= 400:
        # 400 通常是参数错或内容审核
        text_preview = resp.text[:200]
        raise NetworkUnreachableError(
            f"{provider_id} request rejected (HTTP {resp.status_code}): {text_preview}",
            provider_id=provider_id,
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise NetworkUnreachableError(
            f"{provider_id} returned non-JSON response", provider_id=provider_id,
        ) from exc

    return extractor(data)


def _extract_bocha_results(data: dict) -> list[SearchResult]:
    """博查 response shape: ``{"data": {"webPages": {"value": [...]}, "code": 200}}``."""
    code = data.get("code")
    if code is not None and int(code) != 200:
        msg = data.get("msg") or data.get("message") or f"code={code}"
        raise NetworkUnreachableError(f"bocha api error: {msg}", provider_id="bocha")

    payload = data.get("data") or {}
    web_pages = (payload.get("webPages") or {}).get("value") or []
    out: list[SearchResult] = []
    for item in web_pages:
        out.append(
            SearchResult(
                title=str(item.get("name") or item.get("title") or "无标题"),
                url=str(item.get("url") or ""),
                snippet=str(item.get("snippet") or item.get("summary") or ""),
                source=str(item.get("siteName") or ""),
                date=str(item.get("datePublished") or ""),
            )
        )
    return out


register(BochaProvider())
