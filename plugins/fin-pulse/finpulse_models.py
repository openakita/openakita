# ruff: noqa: N999
"""Static metadata constants for fin-pulse.

Pure data — no I/O, no runtime state, no host dependency — so the module
can be imported from the plugin entry, the pipeline, every fetcher, and
the unit-test harness without pulling in aiosqlite or httpx.

Sections:

* :data:`MODES` — the three canonical V1.0 pipeline modes
  (``daily_brief`` / ``hot_radar`` / ``ask_news``) plus the shared
  ``ingest`` staging mode, with display names and required params.
* :data:`ERROR_HINTS` — the nine standardised ``error_kind`` categories
  with bilingual operator hints, aligned to ``footage-gate`` /
  ``avatar-studio`` / ``subtitle-craft`` so the host task-detail panel
  renders one uniform badge.
* :data:`SOURCE_DEFS` — finance sources spanning three *kinds*
  (``newsnow`` / ``direct`` / ``rss``), each annotated with a
  ``content_type`` and an optional ``newsnow_id``.
  ``source_id`` is the primary key used by ``articles.source_id`` and
  the ``config['source.{id}.last_ok']`` health probe keys.
* :data:`FRESHNESS_DEFAULTS` — per-content-type maximum article age
  in hours; sources may override with an explicit ``max_age_hours``.
* :data:`SESSIONS` — ``morning`` / ``noon`` / ``evening`` labels for the
  ``daily_brief`` mode; the :data:`DEFAULT_CRONS` map ships the
  Mon-Fri defaults surfaced in the Settings → Schedules section.
"""

from __future__ import annotations

from typing import Any, Final

# ── Modes ────────────────────────────────────────────────────────────────


MODES: Final[dict[str, dict[str, object]]] = {
    # "ingest" is a staging helper — crawlers land articles into SQLite
    # without rendering a digest. Exposed so the UI can trigger a
    # source-only refresh without spinning up daily_brief.
    "ingest": {
        "display_zh": "抓取归一",
        "display_en": "Ingest",
        "catalog_id": "IN0",
        "default_params": {"sources": "*", "since_hours": 24},
    },
    "daily_brief": {
        "display_zh": "早午晚报",
        "display_en": "Daily Brief",
        "catalog_id": "DB1",
        "sessions": ("morning", "noon", "evening"),
        "default_params": {"session": "morning", "top_k": 20},
    },
    "hot_radar": {
        "display_zh": "热点雷达",
        "display_en": "Hot Radar",
        "catalog_id": "HR1",
        "default_params": {"min_score": 7.0, "cooldown_sec": 1800},
    },
    "ask_news": {
        "display_zh": "Agent 问询",
        "display_en": "Ask News",
        "catalog_id": "AN1",
        "default_params": {},
    },
}

MODE_IDS: Final[tuple[str, ...]] = tuple(MODES.keys())

SESSIONS: Final[tuple[str, ...]] = ("morning", "noon", "evening")

# Weekday cron defaults (Mon-Fri) for the three daily_brief sessions.
# Users can override in Settings → Schedules before calling
# ``POST /schedules``.
DEFAULT_CRONS: Final[dict[str, str]] = {
    "morning": "0 9 * * 1-5",
    "noon": "0 13 * * 1-5",
    "evening": "0 22 * * 1-5",
}


# ── Error categories ─────────────────────────────────────────────────────


ERROR_HINTS: Final[dict[str, dict[str, list[str]]]] = {
    "network": {
        "zh": ["请检查网络连接", "若使用代理请确认 NewsNow/RSS 源可达"],
        "en": [
            "Check your network connection",
            "If behind a proxy, verify NewsNow / RSS feeds are reachable",
        ],
    },
    "timeout": {
        "zh": ["请在 Settings → Sources 调低并发", "或延长 fetcher 超时阈值后重试"],
        "en": [
            "Lower the concurrency in Settings → Sources",
            "Or extend the fetcher timeout and retry",
        ],
    },
    "auth": {
        "zh": ["请检查 LLM / webhook 的 API Key", "或在 Settings 重新填写"],
        "en": [
            "Check the LLM / webhook API key",
            "Re-enter the credential in Settings",
        ],
    },
    "quota": {
        "zh": ["LLM 或源站配额超限", "可切换宿主 LLM 端点或等待重置"],
        "en": [
            "LLM or upstream quota exceeded",
            "Switch host LLM endpoint or wait for quota reset",
        ],
    },
    "rate_limit": {
        "zh": ["抓取过于频繁被限流", "请在 Settings → Sources 拉长抓取间隔"],
        "en": [
            "Source or webhook rate-limited",
            "Extend the crawl interval in Settings → Sources",
        ],
    },
    "dependency": {
        "zh": ["缺少必要依赖（PyExecJS / Node / feedparser 等）", "请按 VALIDATION.md 安装"],
        "en": [
            "Missing runtime dependency (PyExecJS / Node / feedparser, etc.)",
            "Follow VALIDATION.md for installation",
        ],
    },
    "moderation": {
        "zh": ["LLM 内容审核拒绝", "可切换 provider 或调整 prompt 后重试"],
        "en": [
            "LLM content moderation rejected the request",
            "Switch provider or adjust the prompt and retry",
        ],
    },
    "not_found": {
        "zh": ["源站 404 或游标越界", "请在 Settings → Sources 点测试连接并重置游标"],
        "en": [
            "Upstream 404 or cursor out of range",
            "Click Test in Settings → Sources and reset the cursor",
        ],
    },
    "unknown": {
        "zh": ["请复制 task_id 反馈给维护者", "或截图 Tasks 详情页 metadata"],
        "en": [
            "Report the task_id to the maintainer",
            "Or screenshot the Tasks detail-page metadata JSON",
        ],
    },
}

ERROR_KINDS: Final[tuple[str, ...]] = tuple(ERROR_HINTS.keys())


# ── Content-type freshness defaults (hours) ──────────────────────────────

FRESHNESS_DEFAULTS: Final[dict[str, int]] = {
    "flash": 12,
    "news": 72,
    "hot_stock": 4,
    "policy": 168,
    "filing": 168,
    "data": 168,
    "custom": 72,
}

CONTENT_TYPES: Final[tuple[str, ...]] = tuple(FRESHNESS_DEFAULTS.keys())


# ── Data sources ─────────────────────────────────────────────────────────
#
# Each entry carries:
#   kind          – "newsnow" | "direct" | "rss"
#   content_type  – one of CONTENT_TYPES
#   newsnow_id    – upstream NewsNow channel id (only for kind=newsnow)
#   max_age_hours – per-source freshness override (0 = use FRESHNESS_DEFAULTS)
#
# TrendRadar keeps NewsNow platforms and RSS feeds editable in YAML. fin-pulse
# mirrors that catalog/fetch/filter/report split, but deliberately does not
# mirror TrendRadar's scheduler or notification dispatcher: OpenAkita's host
# scheduler and IM channels remain the delivery layer.


SOURCE_DEFS: Final[dict[str, dict[str, object]]] = {
    # ── NewsNow: Chinese finance core ────────────────────────────────
    "wallstreetcn": {
        "display_zh": "华尔街见闻",
        "display_en": "WallStreet CN",
        "kind": "newsnow",
        "content_type": "flash",
        "newsnow_id": "wallstreetcn",
        "default_enabled": True,
        "homepage": "https://wallstreetcn.com/",
    },
    "wallstreetcn-news": {
        "display_zh": "华尔街见闻·新闻",
        "display_en": "WallStreet CN News",
        "kind": "newsnow",
        "content_type": "news",
        "newsnow_id": "wallstreetcn-news",
        "default_enabled": False,
        "homepage": "https://wallstreetcn.com/",
    },
    "cls": {
        "display_zh": "财联社电报",
        "display_en": "CLS Telegraph",
        "kind": "newsnow",
        "content_type": "flash",
        "newsnow_id": "cls",
        "default_enabled": True,
        "homepage": "https://www.cls.cn/telegraph",
    },
    "cls-depth": {
        "display_zh": "财联社·深度",
        "display_en": "CLS In-depth",
        "kind": "newsnow",
        "content_type": "news",
        "newsnow_id": "cls-depth",
        "default_enabled": False,
        "homepage": "https://www.cls.cn/",
    },
    "jin10": {
        "display_zh": "金十数据",
        "display_en": "Jin10",
        "kind": "newsnow",
        "content_type": "flash",
        "newsnow_id": "jin10",
        "default_enabled": True,
        "homepage": "https://www.jin10.com/",
    },
    "gelonghui": {
        "display_zh": "格隆汇",
        "display_en": "Gelonghui",
        "kind": "newsnow",
        "content_type": "news",
        "newsnow_id": "gelonghui",
        "default_enabled": True,
        "homepage": "https://www.gelonghui.com/",
    },
    "xueqiu-hotstock": {
        "display_zh": "雪球·热门股票",
        "display_en": "Xueqiu Hot Stocks",
        "kind": "newsnow",
        "content_type": "hot_stock",
        "newsnow_id": "xueqiu-hotstock",
        "default_enabled": True,
        "homepage": "https://xueqiu.com/",
    },
    "fastbull": {
        "display_zh": "快牛快讯",
        "display_en": "Fastbull Express",
        "kind": "newsnow",
        "content_type": "flash",
        "newsnow_id": "fastbull",
        "default_enabled": False,
        "homepage": "https://www.fastbull.com/",
    },
    "mktnews": {
        "display_zh": "市场新闻",
        "display_en": "MktNews",
        "kind": "newsnow",
        "content_type": "flash",
        "newsnow_id": "mktnews",
        "default_enabled": False,
        "homepage": "https://mktnews.net/",
    },
    # ── Direct fetchers ──────────────────────────────────────────────
    "eastmoney": {
        "display_zh": "东方财富",
        "display_en": "EastMoney",
        "kind": "direct",
        "content_type": "news",
        "default_enabled": True,
        "homepage": "https://www.eastmoney.com/",
    },
    "pbc_omo": {
        "display_zh": "央行公开市场",
        "display_en": "PBC OMO",
        "kind": "direct",
        "content_type": "policy",
        "default_enabled": True,
        "homepage": "http://www.pbc.gov.cn/",
    },
    "yicai": {
        "display_zh": "第一财经",
        "display_en": "Yicai",
        "kind": "direct",
        "content_type": "news",
        "default_enabled": True,
        "homepage": "https://www.yicai.com/",
    },
    "nbd": {
        "display_zh": "每日经济新闻",
        "display_en": "National Business Daily",
        "kind": "direct",
        "content_type": "news",
        "default_enabled": True,
        "homepage": "https://www.nbd.com.cn/",
    },
    "stcn": {
        "display_zh": "证券时报",
        "display_en": "Securities Times",
        "kind": "direct",
        "content_type": "news",
        "default_enabled": True,
        "homepage": "https://www.stcn.com/",
    },
    # ── RSS / institutional ──────────────────────────────────────────
    "nbs": {
        "display_zh": "国家统计局",
        "display_en": "NBS of China",
        "kind": "rss",
        "content_type": "data",
        "default_enabled": True,
        "homepage": "https://www.stats.gov.cn/",
    },
    "fed_fomc": {
        "display_zh": "美联储 FOMC",
        "display_en": "Fed FOMC",
        "kind": "rss",
        "content_type": "policy",
        "default_enabled": True,
        "homepage": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    },
    "sec_edgar": {
        "display_zh": "美国 SEC EDGAR",
        "display_en": "SEC EDGAR",
        "kind": "rss",
        "content_type": "filing",
        "default_enabled": True,
        "homepage": "https://www.sec.gov/cgi-bin/browse-edgar",
    },
    "rss_generic": {
        "display_zh": "自定义 RSS",
        "display_en": "Custom RSS",
        "kind": "rss",
        "content_type": "custom",
        "default_enabled": True,
        "homepage": "",
    },
}

SOURCE_IDS: Final[tuple[str, ...]] = tuple(SOURCE_DEFS.keys())


def get_source_group(source_id: str) -> str:
    """Return the UI/source-management group for *source_id*."""
    defn = SOURCE_DEFS.get(source_id, {})
    kind = str(defn.get("kind") or "")
    if kind == "newsnow":
        return "newsnow"
    if source_id == "rss_generic":
        return "custom_rss"
    if kind == "rss":
        return "builtin_rss"
    if kind == "direct":
        return "direct"
    return "other"


def get_source_fetcher_id(source_id: str) -> str:
    """Return the executable fetcher id used by SOURCE_REGISTRY."""
    defn = SOURCE_DEFS.get(source_id, {})
    if defn.get("kind") == "newsnow":
        return "newsnow"
    return source_id


def get_source_probe_target(source_id: str) -> str:
    """Return the source id that should be used for health probing."""
    return get_source_fetcher_id(source_id)


def get_source_capabilities(source_id: str) -> tuple[str, ...]:
    """Return compact capabilities for UI badges and grouped source chips."""
    defn = SOURCE_DEFS.get(source_id, {})
    kind = str(defn.get("kind") or "")
    content_type = str(defn.get("content_type") or "news")
    caps: list[str] = []
    if kind == "newsnow":
        caps.append("hotlist")
    elif kind == "rss":
        caps.append("rss")
    elif kind == "direct":
        caps.append("direct")
    if content_type == "custom":
        caps.append("custom_rss")
    elif content_type:
        caps.append(content_type)
    # Preserve order while removing duplicates.
    return tuple(dict.fromkeys(caps))


def iter_sources_for_ui() -> list[dict[str, Any]]:
    """Return SOURCE_DEFS with derived UI/fetch contract fields.

    The fetcher registry executes at a different granularity than the
    display catalog: all NewsNow channels share the single ``newsnow``
    fetcher. Exposing that mapping here keeps frontend probing/ingest
    logic from guessing.
    """
    items: list[dict[str, Any]] = []
    for order, (sid, meta) in enumerate(SOURCE_DEFS.items()):
        group = get_source_group(sid)
        fetcher_id = get_source_fetcher_id(sid)
        items.append(
            {
                "id": sid,
                "display_zh": str(meta.get("display_zh") or sid),
                "display_en": str(meta.get("display_en") or sid),
                "kind": str(meta.get("kind") or ""),
                "group": group,
                "content_type": str(meta.get("content_type") or "news"),
                "capabilities": list(get_source_capabilities(sid)),
                "newsnow_id": str(meta.get("newsnow_id") or ""),
                "fetcher_id": fetcher_id,
                "probe_target": get_source_probe_target(sid),
                "can_probe_individual": group != "newsnow",
                "default_enabled": bool(meta.get("default_enabled")),
                "homepage": str(meta.get("homepage") or ""),
                "ui_order": order,
            }
        )
    return items


def get_max_age_hours(source_id: str) -> int:
    """Return the freshness ceiling for *source_id* in hours.

    Per-source ``max_age_hours`` wins; falls back to the content-type
    default from :data:`FRESHNESS_DEFAULTS`.
    """
    defn = SOURCE_DEFS.get(source_id, {})
    explicit = defn.get("max_age_hours")
    if isinstance(explicit, int) and explicit > 0:
        return explicit
    ct = defn.get("content_type", "news")
    return FRESHNESS_DEFAULTS.get(ct, 72)  # type: ignore[arg-type]


# ── Scoring scale (Horizon-style 0-10) ───────────────────────────────────
#
# Surfaced in the ai prompt (Phase 3) and the /articles default filter.

SCORE_THRESHOLDS: Final[dict[str, float]] = {
    "critical": 9.0,  # central-bank rate decisions / regulatory surprises
    "important": 7.0,  # major macro data, prime earnings
    "routine": 5.0,  # sector reports, ordinary announcements
    "low": 3.0,  # general tech / entertainment
    "noise": 0.0,  # ads, fluff
}


__all__ = [
    "CONTENT_TYPES",
    "DEFAULT_CRONS",
    "ERROR_HINTS",
    "ERROR_KINDS",
    "FRESHNESS_DEFAULTS",
    "MODE_IDS",
    "MODES",
    "SCORE_THRESHOLDS",
    "SESSIONS",
    "SOURCE_DEFS",
    "SOURCE_IDS",
    "get_source_capabilities",
    "get_source_fetcher_id",
    "get_source_group",
    "get_source_probe_target",
    "get_max_age_hours",
    "iter_sources_for_ui",
]
