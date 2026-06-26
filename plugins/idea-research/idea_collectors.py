"""Collector registry, Normalizer and Ranker (§6 + §6.5).

Glues Engine A (``idea_engine_api``) and Engine B
(``idea_engine_crawler``) together behind a single
``CollectorRegistry`` facade. The registry resolves the right collector
for a (platform, engine_pref) pair, calls into ``Normalizer`` /
``Ranker`` and returns deduped + ranked ``TrendItem``s ready for the
``trend_items`` table.

Design constraints honoured:

* No cross-plugin imports, no ``contrib`` imports.
* Engine B is opt-in: missing cookies / unacked risk → graceful
  ``VendorError`` with the right ``error_kind``.
* MDRM read is optional and *never blocking*: if the adapter raises
  for any reason the ranker continues without the boost.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from idea_engine_api import (
    BiliCollector,
    RssHubCollector,
    UrlPasteCollector,
    YouTubeCollector,
    _platform_from_url,
    filter_items_by_keywords,
)
from idea_engine_crawler import (
    BiliLoggedCrawler,
    CookiesVault,
    CrawlerBase,
    DouyinCrawler,
    KsCrawler,
    PlaywrightDriver,
    WeiboCrawler,
    XhsCrawler,
)
from idea_models import (
    RANKER_WEIGHTS,
    ResolvedSource,
    TrendItem,
    score_trend_item,
)
from idea_research_inline.vendor_client import VendorAuthError, VendorError, VendorNetworkError

API_SAFE_PLATFORMS: tuple[str, ...] = ("bilibili", "youtube")
RSS_PLATFORMS: tuple[str, ...] = ("douyin", "xhs", "weibo", "bilibili", "youtube")
_PLATFORM_COLLECTOR_TIMEOUT_S: dict[str, float] = {"youtube": 120.0}
CRAWLER_PLATFORMS: tuple[str, ...] = (
    "douyin",
    "xhs",
    "ks",
    "bilibili",
    "weibo",
)


# --------------------------------------------------------------------------- #
# Protocols                                                                    #
# --------------------------------------------------------------------------- #


class _ItemCollector(Protocol):
    name: str
    platform: str

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str,
        limit: int,
    ) -> list[TrendItem]: ...


class _SingleCollector(Protocol):
    async def fetch_single(self, url: str, *, with_comments: bool = False) -> TrendItem | None: ...


# --------------------------------------------------------------------------- #
# Normalizer                                                                   #
# --------------------------------------------------------------------------- #


class Normalizer:
    """Dedupes by ``(platform, external_id)`` and post-fills hook guesses."""

    HOOK_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("数据冲击", ("第一", "破纪录", "万", "亿", "倍", "100%")),
        ("反差", ("没想到", "居然", "竟然", "颠覆", "逆袭")),
        ("悬念", ("揭秘", "真相", "原来", "内幕")),
        ("疑问", ("为什么", "怎么", "如何", "?", "？")),
        ("情绪", ("感动", "心疼", "震惊", "破防")),
        ("利益承诺", ("免费", "教程", "干货", "秘籍", "技巧")),
        ("痛点", ("再也不", "终于", "解决", "搞定")),
    )

    def dedupe(self, items: list[TrendItem]) -> list[TrendItem]:
        seen: set[tuple[str, str]] = set()
        out: list[TrendItem] = []
        for it in items:
            key = (it.platform, it.external_id)
            if key in seen or not it.external_id:
                continue
            seen.add(key)
            out.append(it)
        return out

    def guess_hook(self, item: TrendItem) -> str | None:
        text = (item.title or "") + " " + (item.description or "")
        for hook, tokens in self.HOOK_TYPE_RULES:
            if any(tok in text for tok in tokens):
                return hook
        return None

    def annotate(self, items: list[TrendItem]) -> list[TrendItem]:
        for it in items:
            if it.hook_type_guess is None:
                it.hook_type_guess = self.guess_hook(it)
        return items


# --------------------------------------------------------------------------- #
# Ranker                                                                       #
# --------------------------------------------------------------------------- #


class Ranker:
    """Wraps ``score_trend_item`` and pulls MDRM hits in bulk."""

    def __init__(
        self,
        *,
        weights: dict[str, Any] | None = None,
        mdrm_search: Callable[..., Awaitable[list[Any]]] | None = None,
        mdrm_search_limit: int = 5,
        mdrm_search_timeout_s: float = 2.0,
    ) -> None:
        self._weights = weights or RANKER_WEIGHTS
        self._mdrm_search = mdrm_search
        self._mdrm_search_limit = max(1, int(mdrm_search_limit))
        self._mdrm_search_timeout_s = float(mdrm_search_timeout_s)

    async def annotate_mdrm(
        self, items: list[TrendItem], *, enabled: bool = True
    ) -> list[TrendItem]:
        if not enabled or self._mdrm_search is None:
            return items
        for it in items:
            try:
                hits = await asyncio.wait_for(
                    self._mdrm_search(
                        it.title or "",
                        limit=self._mdrm_search_limit,
                        min_similarity=0.5,
                    ),
                    timeout=self._mdrm_search_timeout_s,
                )
            except (TimeoutError, Exception):
                continue
            ids: list[str] = []
            for entry in hits or []:
                if isinstance(entry, tuple) and len(entry) == 2:
                    rec, _score = entry
                    rec_id = getattr(rec, "id", None) or (
                        rec.get("id") if isinstance(rec, dict) else None
                    )
                elif isinstance(entry, dict):
                    rec_id = entry.get("id") or entry.get("hook_id")
                else:
                    rec_id = getattr(entry, "id", None)
                if rec_id:
                    ids.append(str(rec_id))
            if ids:
                it.mdrm_hits = ids
        return items

    def score(self, items: list[TrendItem], keywords: list[str]) -> list[TrendItem]:
        for it in items:
            it.score = score_trend_item(it, keywords, weights=self._weights)
        items.sort(key=lambda x: x.score, reverse=True)
        return items


# --------------------------------------------------------------------------- #
# Registry                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class CollectorChoice:
    engine: str  # 'a' | 'b'
    name: str


def _collector_timeout(platform: str, default_timeout_s: float) -> float:
    return _PLATFORM_COLLECTOR_TIMEOUT_S.get(platform, default_timeout_s)


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    with contextlib.suppress(asyncio.CancelledError, Exception):
        task.result()


class CollectorRegistry:
    """Resolves and orchestrates Engine A / Engine B collectors."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        vault: CookiesVault | None = None,
        playwright_driver: PlaywrightDriver | None = None,
        ranker: Ranker | None = None,
        normalizer: Normalizer | None = None,
        api_keys: dict[str, str] | None = None,
        rsshub_base: str = "https://rsshub.app",
        risk_acknowledged: bool = False,
        engine_b_enabled: bool = False,
    ) -> None:
        self._client = http_client
        self._vault = vault
        self._driver = playwright_driver
        self._ranker = ranker or Ranker()
        self._normalizer = normalizer or Normalizer()
        self._api_keys = api_keys or {}
        self._rsshub_base = rsshub_base
        self._risk_acknowledged = bool(risk_acknowledged)
        self._engine_b_enabled = bool(engine_b_enabled)
        self._collector_cache: dict[str, _ItemCollector] = {}

    def update_api_key(self, provider: str, api_key: str | None) -> None:
        key = (api_key or "").strip()
        if key:
            self._api_keys[provider] = key
        else:
            self._api_keys.pop(provider, None)
        self._collector_cache.pop(f"a:{provider}", None)

    def update_rsshub_base(self, rsshub_base: str | None) -> None:
        base = (rsshub_base or "").strip() or "https://rsshub.app"
        if base == self._rsshub_base:
            return
        self._rsshub_base = base
        for key in list(self._collector_cache):
            if key.startswith("a:"):
                self._collector_cache.pop(key, None)

    def update_engine_b_options(
        self,
        *,
        engine_b_enabled: bool | None = None,
        risk_acknowledged: bool | None = None,
    ) -> None:
        changed = False
        if engine_b_enabled is not None:
            enabled = bool(engine_b_enabled)
            changed = changed or enabled != self._engine_b_enabled
            self._engine_b_enabled = enabled
        if risk_acknowledged is not None:
            acknowledged = bool(risk_acknowledged)
            changed = changed or acknowledged != self._risk_acknowledged
            self._risk_acknowledged = acknowledged
        if changed:
            for key in list(self._collector_cache):
                if key.startswith("b:"):
                    self._collector_cache.pop(key, None)

    async def cookie_header(self, platform: str) -> str | None:
        if self._vault is None:
            return None
        entry = await self._vault.load(platform)
        if entry is None or not entry.cookies:
            return None
        return "; ".join(f"{k}={v}" for k, v in entry.cookies.items())

    # ---- factories ---------------------------------------------------------

    def _engine_a_for(self, platform: str) -> _ItemCollector:
        cache_key = f"a:{platform}"
        if cache_key in self._collector_cache:
            return self._collector_cache[cache_key]
        if platform == "bilibili":
            inst = BiliCollector(client=self._client, rsshub_base=self._rsshub_base)
        elif platform == "youtube":
            inst = YouTubeCollector(
                client=self._client,
                api_key=self._api_keys.get("youtube"),
                rsshub_base=self._rsshub_base,
            )
        else:
            inst = RssHubCollector(client=self._client, rsshub_base=self._rsshub_base)
        self._collector_cache[cache_key] = inst
        return inst

    def _engine_b_for(self, platform: str) -> CrawlerBase:
        if not self._engine_b_enabled:
            err = VendorError("Engine B 已关闭；请到 Settings → 数据源 启用浏览器爬虫")
            err.error_kind = "auth"
            raise err
        if self._driver is None or self._vault is None:
            err = VendorError(
                "Engine B 未注入 PlaywrightDriver / CookiesVault；请检查 plugin on_load 装配"
            )
            err.error_kind = "dependency"
            raise err
        cls_map: dict[str, type[CrawlerBase]] = {
            "douyin": DouyinCrawler,
            "xhs": XhsCrawler,
            "ks": KsCrawler,
            "bilibili": BiliLoggedCrawler,
            "weibo": WeiboCrawler,
        }
        if platform not in cls_map:
            err = VendorError(f"Engine B 不支持平台 {platform!r}")
            err.error_kind = "format"
            raise err
        cache_key = f"b:{platform}"
        if cache_key not in self._collector_cache:
            self._collector_cache[cache_key] = cls_map[platform](
                driver=self._driver,
                vault=self._vault,
                http_client=self._client,
                risk_acknowledged=self._risk_acknowledged,
            )
        return self._collector_cache[cache_key]  # type: ignore[return-value]

    def resolve_collector(
        self,
        platform: str,
        *,
        engine_pref: str = "auto",
    ) -> CollectorChoice:
        """Decide which engine + collector should serve a platform."""

        engine_pref = (engine_pref or "auto").lower()
        if engine_pref == "b":
            inst = self._engine_b_for(platform)
            return CollectorChoice(engine="b", name=inst.name)
        if engine_pref == "a" or platform in API_SAFE_PLATFORMS:
            inst_a = self._engine_a_for(platform)
            return CollectorChoice(engine="a", name=inst_a.name)
        if engine_pref == "auto":
            if (
                self._engine_b_enabled
                and platform in CRAWLER_PLATFORMS
                and self._driver is not None
                and self._vault is not None
            ):
                inst_b = self._engine_b_for(platform)
                return CollectorChoice(engine="b", name=inst_b.name)
            inst_a = self._engine_a_for(platform)
            return CollectorChoice(engine="a", name=inst_a.name)
        err = VendorError(f"unknown engine preference {engine_pref!r}")
        err.error_kind = "format"
        raise err

    # ---- orchestration -----------------------------------------------------

    async def fetch_for_radar(
        self,
        platforms: list[str],
        keywords: list[str],
        *,
        time_window: str = "24h",
        limit: int = 20,
        engine_pref: str = "auto",
        mdrm_weighting: bool = True,
        per_collector_timeout_s: float = 25.0,
    ) -> dict[str, Any]:
        """Pull from all platforms in parallel; never raise."""

        tasks: list[tuple[str, CollectorChoice, float, float, asyncio.Task[Any]]] = []
        choices: list[CollectorChoice] = []
        errors: list[dict[str, Any]] = []
        try:
            for platform in platforms:
                try:
                    choice = self.resolve_collector(platform, engine_pref=engine_pref)
                except VendorError as exc:
                    errors.append(
                        {
                            "platform": platform,
                            "error_kind": exc.error_kind,
                            "message": str(exc),
                            "status_code": getattr(exc, "status_code", None),
                            "payload": getattr(exc, "payload", None),
                        }
                    )
                    continue
                choices.append(choice)
                collector = (
                    self._engine_b_for(platform)
                    if choice.engine == "b"
                    else self._engine_a_for(platform)
                )
                effective_timeout = _collector_timeout(platform, per_collector_timeout_s)
                tasks.append(
                    (
                        platform,
                        choice,
                        time.monotonic(),
                        effective_timeout,
                        asyncio.create_task(
                            self._fetch_one(
                                collector,
                                platform=platform,
                                keywords=keywords,
                                time_window=time_window,
                                limit=limit,
                                timeout_s=effective_timeout,
                            )
                        ),
                    )
                )
            gathered: list[TrendItem] = []
            for platform, choice, started_at, effective_timeout, task in tasks:
                remaining = max(0.0, effective_timeout - (time.monotonic() - started_at))
                done, _pending = await asyncio.wait({task}, timeout=remaining)
                if not done:
                    task.cancel()
                    task.add_done_callback(_consume_task_exception)
                    errors.append(
                        {
                            "platform": platform,
                            "error_kind": "timeout",
                            "message": (
                                f"{platform} collector timed out after "
                                f"{effective_timeout:.0f}s"
                            ),
                            "payload": {
                                "timeout_s": effective_timeout,
                                "collector": choice.name,
                                "engine": choice.engine,
                            },
                        }
                    )
                    continue
                try:
                    items = task.result()
                except VendorError as exc:
                    errors.append(
                        {
                            "platform": platform,
                            "error_kind": exc.error_kind,
                            "message": str(exc),
                            "status_code": getattr(exc, "status_code", None),
                            "payload": getattr(exc, "payload", None),
                        }
                    )
                    continue
                except Exception as exc:
                    errors.append(
                        {
                            "platform": platform,
                            "error_kind": "unknown",
                            "message": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue
                gathered.extend(items)
            gathered = self._normalizer.dedupe(gathered)
            gathered = filter_items_by_keywords(gathered, keywords)
            gathered = self._normalizer.annotate(gathered)
            gathered = await self._ranker.annotate_mdrm(gathered, enabled=mdrm_weighting)
            gathered = self._ranker.score(gathered, keywords)
            gathered = gathered[:limit]
            return {
                "items": gathered,
                "choices": [c.__dict__ for c in choices],
                "errors": errors,
                "fetched_at": int(time.time()),
            }
        finally:
            for _platform, _choice, _started_at, _effective_timeout, task in tasks:
                if not task.done():
                    task.cancel()
                    task.add_done_callback(_consume_task_exception)

    async def _fetch_one(
        self,
        collector: _ItemCollector,
        *,
        platform: str,
        keywords: list[str],
        time_window: str,
        limit: int,
        timeout_s: float,
    ) -> list[TrendItem]:
        if isinstance(collector, RssHubCollector):
            return await asyncio.wait_for(
                collector.fetch_trending(
                    keywords,
                    time_window,
                    limit,
                    platform=platform,
                ),
                timeout=timeout_s,
            )
        return await asyncio.wait_for(
            collector.fetch_trending(keywords, time_window, limit),
            timeout=timeout_s,
        )

    async def fetch_single_url(
        self,
        url: str,
        *,
        with_comments: bool = False,
        prefer: str = "auto",
    ) -> TrendItem | None:
        """Resolve a single URL into a TrendItem (best-effort)."""

        resolved = await self.fetch_single_source(
            url,
            with_comments=with_comments,
            prefer=prefer,
        )
        return resolved.item if resolved is not None else None

    async def fetch_single_source(
        self,
        url: str,
        *,
        with_comments: bool = False,
        prefer: str = "auto",
    ) -> ResolvedSource | None:
        """Resolve a single URL into a TrendItem plus optional comments."""

        platform = _platform_from_url(url) or "other"
        if (
            platform == "ks"
            and prefer != "ytdlp"
            and self._engine_b_enabled
            and self._driver is not None
            and self._vault is not None
        ):
            collector = self._engine_b_for("ks")
            fetch_detail = getattr(collector, "fetch_detail", None)
            if callable(fetch_detail):
                try:
                    item = await fetch_detail(url)
                    comments: list[dict[str, Any]] = []
                    if with_comments:
                        fetch_comments = getattr(collector, "fetch_comments", None)
                        if callable(fetch_comments):
                            with contextlib.suppress(VendorError):
                                comments = await fetch_comments(item, limit=40)
                    return ResolvedSource(item=item, comments=comments)
                except VendorError:
                    with contextlib.suppress(VendorError):
                        item = await UrlPasteCollector().fetch_single(
                            url,
                            with_comments=with_comments,
                        )
                        if item is not None:
                            return ResolvedSource(item=item)
                    raise
        if prefer == "ytdlp" or platform not in API_SAFE_PLATFORMS:
            item = None
            with contextlib.suppress(VendorError):
                item = await UrlPasteCollector().fetch_single(url, with_comments=with_comments)
            if item is not None:
                return ResolvedSource(item=item)
            if prefer == "ytdlp":
                # Never fall through to Data API when caller explicitly prefers yt-dlp.
                return None
            return None
        if platform in API_SAFE_PLATFORMS:
            collector = self._engine_a_for(platform)
            assert hasattr(collector, "fetch_single")
            try:
                if hasattr(collector, "fetch_single_source"):
                    resolved = await collector.fetch_single_source(  # type: ignore[union-attr]
                        url,
                        with_comments=with_comments,
                    )
                    if resolved is not None:
                        return resolved
                item = await collector.fetch_single(url, with_comments=with_comments)  # type: ignore[union-attr]
                if item is not None:
                    return ResolvedSource(item=item)
                return None
            except (VendorAuthError, VendorNetworkError) as exc:
                should_ytdlp_fallback = platform == "youtube" and isinstance(
                    exc, VendorAuthError
                )
                should_network_fallback = (
                    platform == "youtube"
                    or (
                        platform == "bilibili"
                        and getattr(exc, "status_code", None) == 412
                    )
                )
                if should_ytdlp_fallback or (
                    isinstance(exc, VendorNetworkError) and should_network_fallback
                ):
                    with contextlib.suppress(VendorError):
                        item = await UrlPasteCollector().fetch_single(
                            url,
                            with_comments=with_comments,
                        )
                        if item is not None:
                            return ResolvedSource(item=item)
                        return None
                raise
        return None

    # ---- maintenance -------------------------------------------------------

    async def aclose(self) -> None:
        if self._driver is not None:
            await self._driver.aclose()


__all__ = [
    "API_SAFE_PLATFORMS",
    "CollectorChoice",
    "CollectorRegistry",
    "CRAWLER_PLATFORMS",
    "Normalizer",
    "Ranker",
    "RSS_PLATFORMS",
]
