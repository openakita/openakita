"""omni-post data layer — platforms, error kinds, pydantic models, hints.

Pure data + Pydantic models. No I/O, no Playwright import. Imported by the
task manager / pipeline / plugin layers and by tests with zero side effects.

Design notes
------------
- :data:`PLATFORMS` is the single source of truth for the 10 MVP platforms
  (Sprint 1 delivers 3, Sprint 2 adds 7).
- :class:`ErrorKind` enumerates the 13 error categories used by
  ``task.error_kind`` and the frontend ``ERROR_HINTS`` renderer. Nine of
  them are standard (network / timeout / rate_limit / auth / not_found /
  moderation / quota / dependency / unknown) and four are omni-post
  specific (cookie_expired / content_moderated /
  rate_limited_by_platform / platform_breaking_change).
- :class:`OmniPostError` is the ONLY exception type the pipeline raises
  on a terminal failure; it carries both the error_kind and the bilingual
  hint dictionary so the HTTP layer can echo it to the UI verbatim.
- All Pydantic models set ``model_config = ConfigDict(extra="forbid")``
  so typos in request bodies surface as HTTP 422 — they do NOT silently
  drop fields (Pixelle C6).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── ErrorKind taxonomy (13) ─────────────────────────────────────────────


class ErrorKind(StrEnum):
    """The 13 canonical error categories surfaced by omni-post.

    The first 9 mirror the shared OpenAkita convention (see
    ``avatar_studio_inline.vendor_client``), and the last 4 are
    publishing-specific and documented in ERROR_HINTS below.
    """

    NETWORK = "network"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    NOT_FOUND = "not_found"
    MODERATION = "moderation"
    QUOTA = "quota"
    DEPENDENCY = "dependency"
    UNKNOWN = "unknown"
    # omni-post specific
    COOKIE_EXPIRED = "cookie_expired"
    CONTENT_MODERATED = "content_moderated"
    RATE_LIMITED_BY_PLATFORM = "rate_limited_by_platform"
    PLATFORM_BREAKING_CHANGE = "platform_breaking_change"


ALL_ERROR_KINDS: tuple[str, ...] = tuple(k.value for k in ErrorKind)


class OmniPostError(Exception):
    """Terminal pipeline error with a typed ``kind`` and bilingual hint.

    Code raising this MUST pass an :class:`ErrorKind` and either a
    pre-formatted ``hint`` (see :data:`ERROR_HINTS`) or an ad-hoc
    bilingual dict.  The pipeline serializes ``hint`` into
    ``tasks.error_hint_i18n`` so the UI can render it without any
    frontend-side translation.
    """

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        hint: ErrorHint | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.hint: ErrorHint = (
            hint if hint is not None else ERROR_HINTS.get(kind.value, ERROR_HINTS["unknown"])
        )


# ─── Error hints (bilingual, actionable) ─────────────────────────────────


class ErrorHint(dict):
    """Typed dict-like ErrorHint with ``title_*`` and ``hints_*`` fields."""


# Keeping this as a plain dict (not TypedDict) keeps runtime cost to zero
# while still matching the JSON shape the UI expects.
ERROR_HINTS: dict[str, dict[str, Any]] = {
    "network": {
        "title_zh": "网络异常",
        "title_en": "Network error",
        "hints_zh": [
            "请检查网络连接",
            "若走代理请确认目标平台域名可达",
            "将自动指数退避重试",
        ],
        "hints_en": [
            "Check your network connection",
            "If a proxy is used, verify the target platform is reachable",
            "Auto-retry with exponential backoff",
        ],
    },
    "timeout": {
        "title_zh": "请求超时",
        "title_en": "Timeout",
        "hints_zh": [
            "平台响应超时，请稍后重试",
            "可在「设置」中调高每步超时",
        ],
        "hints_en": [
            "The platform took too long to respond; retry later",
            "Adjust per-step timeout in Settings",
        ],
    },
    "rate_limit": {
        "title_zh": "并发受限",
        "title_en": "Rate limited",
        "hints_zh": [
            "达到并发上限，请等待当前任务完成",
            "可在「设置」降低并发",
        ],
        "hints_en": [
            "Concurrency ceiling reached; wait and retry",
            "Lower concurrency in Settings",
        ],
    },
    "auth": {
        "title_zh": "鉴权失败",
        "title_en": "Auth failed",
        "hints_zh": [
            "插件自身鉴权失败（并非平台 Cookie 过期）",
            "请检查 OpenAkita identity 配置",
        ],
        "hints_en": [
            "Plugin-level auth failed (not a platform cookie issue)",
            "Check your OpenAkita identity configuration",
        ],
    },
    "not_found": {
        "title_zh": "资源不存在",
        "title_en": "Not found",
        "hints_zh": [
            "请求的任务或素材不存在，可能已被删除",
            "请刷新列表后重试",
        ],
        "hints_en": [
            "The requested task or asset is gone",
            "Refresh the list and retry",
        ],
    },
    "moderation": {
        "title_zh": "内容审核未通过",
        "title_en": "Content moderation",
        "hints_zh": [
            "文案/素材被 OpenAkita 本地合规检查拦截",
            "请修改后重试",
        ],
        "hints_en": [
            "Your caption or asset was blocked by local compliance rules",
            "Edit the content and retry",
        ],
    },
    "quota": {
        "title_zh": "额度不足",
        "title_en": "Quota exceeded",
        "hints_zh": [
            "本账号已达到日/周/月发布上限",
            "请切换账号或调整限额",
        ],
        "hints_en": [
            "This account hit its daily/weekly/monthly publish limit",
            "Switch accounts or adjust the limit",
        ],
    },
    "dependency": {
        "title_zh": "依赖缺失",
        "title_en": "Dependency missing",
        "hints_zh": [
            "ffmpeg / ffprobe 或 Playwright Chromium 未安装",
            "参考 SKILL.md 的系统级依赖清单",
        ],
        "hints_en": [
            "ffmpeg / ffprobe or Playwright Chromium is missing",
            "See SKILL.md for install instructions",
        ],
    },
    "unknown": {
        "title_zh": "未知错误",
        "title_en": "Unknown error",
        "hints_zh": [
            "请查看任务详情中的截图与日志",
            "如复现请反馈给维护者",
        ],
        "hints_en": [
            "Inspect the task's screenshot and logs",
            "Report to the maintainer if reproducible",
        ],
    },
    "cookie_expired": {
        "title_zh": "账号 Cookie 已过期",
        "title_en": "Account cookie expired",
        "hints_zh": [
            "请在「账号」页重新导入 Cookie",
            "或在浏览器内重新登录后触发账号刷新",
        ],
        "hints_en": [
            "Re-import the cookie on the Accounts tab",
            "Or re-login in the browser and refresh the account",
        ],
    },
    "content_moderated": {
        "title_zh": "平台内容审核未通过",
        "title_en": "Platform content moderation",
        "hints_zh": [
            "文案/素材被目标平台审核驳回",
            "常见原因：敏感词、外链、水印、版权",
            "请修改后重投",
        ],
        "hints_en": [
            "Caption or asset was rejected by the platform",
            "Common causes: sensitive words, outbound links, watermarks, copyright",
            "Edit and retry",
        ],
    },
    "rate_limited_by_platform": {
        "title_zh": "平台限流",
        "title_en": "Rate limited by platform",
        "hints_zh": [
            "目标平台返回 429 或风控，已自动延长冷却",
            "请降低该账号的并发/频次",
        ],
        "hints_en": [
            "Target platform returned 429 or risk control; cooldown extended",
            "Lower the per-account concurrency or frequency",
        ],
    },
    "platform_breaking_change": {
        "title_zh": "平台改版",
        "title_en": "Platform breaking change",
        "hints_zh": [
            "页面结构与本地 selectors 不匹配",
            "请等待维护者更新 selectors JSON 或临时改走 MultiPost 兼容引擎",
        ],
        "hints_en": [
            "Page layout no longer matches local selectors",
            "Wait for a selectors update or switch to the MultiPost compat engine",
        ],
    },
}


# ─── Platform catalog (10 MVP) ───────────────────────────────────────────


PlatformId = Literal[
    "douyin",
    "rednote",
    "bilibili",
    "wechat_channels",
    "kuaishou",
    "youtube",
    "tiktok",
    "zhihu",
    "weibo",
    "wechat_mp",
]


PostKind = Literal["video", "dynamic", "article", "podcast"]


class PlatformSpec(BaseModel):
    """One target platform's static metadata (JSON-friendly)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    display_name_zh: str
    display_name_en: str
    supported_kinds: tuple[PostKind, ...]
    engine_preferred: Literal["pw", "mp"] = "pw"
    url_domain_whitelist: tuple[str, ...] = ()
    notes: str = ""


PLATFORMS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        id="douyin",
        display_name_zh="抖音",
        display_name_en="Douyin",
        supported_kinds=("video", "dynamic"),
        url_domain_whitelist=("douyin.com", "iesdouyin.com"),
        notes="主力视频渠道，发布前建议填封面与话题",
    ),
    PlatformSpec(
        id="rednote",
        display_name_zh="小红书",
        display_name_en="RedNote",
        supported_kinds=("video", "dynamic"),
        url_domain_whitelist=("xiaohongshu.com",),
        notes="图文优先；话题与定位能显著提升曝光",
    ),
    PlatformSpec(
        id="bilibili",
        display_name_zh="B 站",
        display_name_en="Bilibili",
        supported_kinds=("video", "dynamic", "article"),
        url_domain_whitelist=("bilibili.com",),
        notes="支持动态、视频稿件、专栏文章",
    ),
    PlatformSpec(
        id="wechat_channels",
        display_name_zh="微信视频号",
        display_name_en="WeChat Channels",
        supported_kinds=("video", "dynamic"),
        url_domain_whitelist=("channels.weixin.qq.com",),
        notes="无界微前端，shadow-root 穿透必需",
    ),
    PlatformSpec(
        id="kuaishou",
        display_name_zh="快手",
        display_name_en="Kuaishou",
        supported_kinds=("video", "dynamic"),
        url_domain_whitelist=("kuaishou.com",),
    ),
    PlatformSpec(
        id="youtube",
        display_name_zh="YouTube",
        display_name_en="YouTube",
        supported_kinds=("video",),
        url_domain_whitelist=("youtube.com", "youtu.be"),
        notes="2FA 需手动完成",
    ),
    PlatformSpec(
        id="tiktok",
        display_name_zh="TikTok",
        display_name_en="TikTok",
        supported_kinds=("video",),
        url_domain_whitelist=("tiktok.com",),
        notes="区域敏感，推荐走代理",
    ),
    PlatformSpec(
        id="zhihu",
        display_name_zh="知乎",
        display_name_en="Zhihu",
        supported_kinds=("video", "dynamic", "article"),
        url_domain_whitelist=("zhihu.com",),
    ),
    PlatformSpec(
        id="weibo",
        display_name_zh="微博",
        display_name_en="Weibo",
        supported_kinds=("video", "dynamic"),
        url_domain_whitelist=("weibo.com", "weibo.cn"),
    ),
    PlatformSpec(
        id="wechat_mp",
        display_name_zh="微信公众号",
        display_name_en="WeChat Official Account",
        supported_kinds=("article",),
        url_domain_whitelist=("mp.weixin.qq.com",),
        notes="仅文章；外链会被自动屏蔽",
    ),
)


PLATFORMS_BY_ID: dict[str, PlatformSpec] = {p.id: p for p in PLATFORMS}


# ─── Task / Asset / Account Pydantic models ──────────────────────────────


class AssetKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    COVER = "cover"


class UploadStatus(StrEnum):
    UPLOADING = "uploading"
    READY = "ready"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EngineName(StrEnum):
    PW = "pw"
    MP = "mp"


class HealthStatus(StrEnum):
    OK = "ok"
    COOKIE_EXPIRED = "cookie_expired"
    UNKNOWN = "unknown"


class PublishPayload(BaseModel):
    """Per-task payload. Each platform adapter is responsible for mapping
    the generic fields below to its own form fields.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., max_length=200)
    description: str = Field(default="", max_length=3000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    topic: str | None = None
    cover_asset_id: str | None = None
    location: str | None = None
    subtitles_path: str | None = None
    # Per-platform override (optional) — when present, the adapter must
    # prefer this over the top-level fields.
    per_platform_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PublishRequest(BaseModel):
    """HTTP body for POST /publish."""

    model_config = ConfigDict(extra="forbid")

    asset_id: str
    payload: PublishPayload
    platforms: list[str]
    account_ids: list[str]
    client_trace_id: str = Field(..., min_length=4, max_length=80)
    auto_submit: bool = True
    engine: Literal["auto", "pw", "mp"] = "auto"
    scheduled_at: str | None = None  # ISO-8601 UTC


class ScheduleRequest(PublishRequest):
    """Same as PublishRequest but ``scheduled_at`` is required."""

    model_config = ConfigDict(extra="forbid")

    scheduled_at: str  # type: ignore[assignment]


class AccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str
    nickname: str
    cookie_raw: str  # Netscape string or JSON blob; encrypted on insert
    tags: list[str] = Field(default_factory=list)
    daily_limit: int = Field(default=5, ge=0, le=1000)
    weekly_limit: int = Field(default=30, ge=0, le=10000)
    monthly_limit: int = Field(default=100, ge=0, le=100000)


class SettingsUpdateRequest(BaseModel):
    """Partial update to omni-post's config. Omitted fields are preserved."""

    model_config = ConfigDict(extra="forbid")

    engine: Literal["auto", "pw", "mp"] | None = None
    concurrency_per_platform: int | None = Field(default=None, ge=1, le=16)
    concurrency_global: int | None = Field(default=None, ge=1, le=64)
    cooldown_seconds_per_account: int | None = Field(default=None, ge=0, le=3600)
    auto_submit_fail_threshold: int | None = Field(default=None, ge=1, le=10)
    retry_max_attempts: int | None = Field(default=None, ge=0, le=10)
    retry_backoff_base: float | None = Field(default=None, ge=1.0, le=10.0)
    health_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    selector_probe_cron: str | None = None
    im_alert_channels: list[str] | None = None
    im_alert_dedup_seconds: int | None = Field(default=None, ge=0, le=86400)
    schedule_jitter_seconds: int | None = Field(default=None, ge=0, le=7200)
    proxy_url: str | None = None
    playwright_headless: bool | None = None
    upload_chunk_bytes: int | None = Field(default=None, ge=65536, le=50 * 1024 * 1024)
    max_asset_size_bytes: int | None = Field(default=None, ge=0)
    mp_extension_min_version: str | None = None
    mp_trusted_domain: str | None = None


# ─── Default config (merged on first load) ───────────────────────────────


DEFAULT_SETTINGS: dict[str, Any] = {
    "engine": "auto",
    "concurrency_per_platform": 2,
    "concurrency_global": 6,
    "cooldown_seconds_per_account": 120,
    "auto_submit_fail_threshold": 3,
    "retry_max_attempts": 3,
    "retry_backoff_base": 2.0,
    "health_threshold": 0.8,
    "selector_probe_cron": "0 3 * * *",
    "im_alert_channels": ["wecom"],
    "im_alert_dedup_seconds": 3600,
    "schedule_jitter_seconds": 900,
    "proxy_url": None,
    "playwright_headless": True,
    "upload_chunk_bytes": 5 * 1024 * 1024,
    "max_asset_size_bytes": 2 * 1024 * 1024 * 1024,
    "mp_extension_min_version": "1.3.8",
    "mp_trusted_domain": "localhost",
}


def build_catalog() -> dict[str, Any]:
    """Return a frontend-ready catalog (platforms + post kinds + engines)."""

    return {
        "platforms": [
            {
                "id": p.id,
                "display_name_zh": p.display_name_zh,
                "display_name_en": p.display_name_en,
                "supported_kinds": list(p.supported_kinds),
                "engine_preferred": p.engine_preferred,
                "notes": p.notes,
            }
            for p in PLATFORMS
        ],
        "post_kinds": ["video", "dynamic", "article", "podcast"],
        "engines": ["auto", "pw", "mp"],
        "asset_kinds": [k.value for k in AssetKind],
        "task_statuses": [s.value for s in TaskStatus],
        "error_kinds": list(ALL_ERROR_KINDS),
    }


__all__ = [
    "ALL_ERROR_KINDS",
    "DEFAULT_SETTINGS",
    "ERROR_HINTS",
    "AccountCreateRequest",
    "AssetKind",
    "EngineName",
    "ErrorHint",
    "ErrorKind",
    "HealthStatus",
    "OmniPostError",
    "PLATFORMS",
    "PLATFORMS_BY_ID",
    "PlatformId",
    "PlatformSpec",
    "PostKind",
    "PublishPayload",
    "PublishRequest",
    "ScheduleRequest",
    "SettingsUpdateRequest",
    "TaskStatus",
    "UploadStatus",
    "build_catalog",
]
