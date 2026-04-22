"""Clip-sense editing mode definitions, presets, pricing, and error hints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ClipMode:
    """Definition of a single editing mode."""

    id: str
    label_zh: str
    label_en: str
    icon: str
    description_zh: str
    description_en: str
    requires_api_key: bool = True
    requires_ffmpeg: bool = True
    skip_steps: frozenset[str] = field(default_factory=frozenset)


MODES: list[ClipMode] = [
    ClipMode(
        id="highlight_extract",
        label_zh="高光提取",
        label_en="Highlight Extract",
        icon="sparkles",
        description_zh="AI 识别精彩片段，自动剪出高光合集",
        description_en="AI identifies exciting moments and auto-clips highlights",
        requires_api_key=True,
        requires_ffmpeg=True,
    ),
    ClipMode(
        id="silence_clean",
        label_zh="静音精剪",
        label_en="Silence Clean",
        icon="volume-x",
        description_zh="自动检测并移除静音/空白段落，紧凑剪辑",
        description_en="Detect and remove silent/blank segments for tighter cuts",
        requires_api_key=False,
        requires_ffmpeg=True,
        skip_steps=frozenset({"transcribe", "analyze"}),
    ),
    ClipMode(
        id="topic_split",
        label_zh="段落拆条",
        label_en="Topic Split",
        icon="scissors",
        description_zh="AI 按主题/段落自动拆分长视频为多个短视频",
        description_en="AI splits long video into topic-based short clips",
        requires_api_key=True,
        requires_ffmpeg=True,
    ),
    ClipMode(
        id="talking_polish",
        label_zh="口播精编",
        label_en="Talking Polish",
        icon="mic",
        description_zh="AI 去除口误、废话、重复，精炼口播内容",
        description_en="AI removes filler words, stutters, and repetitions",
        requires_api_key=True,
        requires_ffmpeg=True,
    ),
]

MODES_BY_ID: dict[str, ClipMode] = {m.id: m for m in MODES}


def get_mode(mode_id: str) -> ClipMode | None:
    return MODES_BY_ID.get(mode_id)


def mode_to_dict(m: ClipMode) -> dict[str, Any]:
    return {
        "id": m.id,
        "label_zh": m.label_zh,
        "label_en": m.label_en,
        "icon": m.icon,
        "description_zh": m.description_zh,
        "description_en": m.description_en,
        "requires_api_key": m.requires_api_key,
        "requires_ffmpeg": m.requires_ffmpeg,
        "skip_steps": sorted(m.skip_steps),
    }


# ---------------------------------------------------------------------------
# Silence presets (conservative / standard / aggressive)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SilencePreset:
    id: str
    label_zh: str
    label_en: str
    threshold_db: float
    min_silence_sec: float
    padding_sec: float


SILENCE_PRESETS: list[SilencePreset] = [
    SilencePreset(
        id="conservative",
        label_zh="保守",
        label_en="Conservative",
        threshold_db=-50.0,
        min_silence_sec=1.0,
        padding_sec=0.2,
    ),
    SilencePreset(
        id="standard",
        label_zh="标准",
        label_en="Standard",
        threshold_db=-40.0,
        min_silence_sec=0.5,
        padding_sec=0.1,
    ),
    SilencePreset(
        id="aggressive",
        label_zh="激进",
        label_en="Aggressive",
        threshold_db=-35.0,
        min_silence_sec=0.3,
        padding_sec=0.05,
    ),
]

SILENCE_PRESETS_BY_ID: dict[str, SilencePreset] = {p.id: p for p in SILENCE_PRESETS}


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceEntry:
    """Per-unit price for a single API."""

    api: str
    unit: str
    price_cny: float


PRICE_TABLE: list[PriceEntry] = [
    PriceEntry(api="paraformer-v2", unit="秒", price_cny=0.0008),
    PriceEntry(api="qwen-plus", unit="千 input token", price_cny=0.004),
]


@dataclass
class CostPreview:
    """Estimated cost breakdown for a task."""

    total_cny: float
    items: list[dict[str, Any]]


def estimate_cost(mode_id: str, duration_sec: float) -> CostPreview:
    """Estimate API cost for a given mode and video duration.

    Returns CostPreview with total_cny and per-item breakdown.
    """
    mode = MODES_BY_ID.get(mode_id)
    if mode is None:
        return CostPreview(total_cny=0.0, items=[])

    items: list[dict[str, Any]] = []

    if "transcribe" not in mode.skip_steps:
        asr_cost = duration_sec * 0.0008
        items.append({
            "api": "paraformer-v2",
            "description": "语音转写",
            "quantity": f"{duration_sec:.0f}秒",
            "cost_cny": round(asr_cost, 4),
        })

    if "analyze" not in mode.skip_steps:
        tokens_est = duration_sec * 3.5
        qwen_cost = (tokens_est / 1000.0) * 0.004
        items.append({
            "api": "qwen-plus",
            "description": "AI 分析",
            "quantity": f"~{tokens_est:.0f} tokens",
            "cost_cny": round(qwen_cost, 4),
        })

    total = sum(it["cost_cny"] for it in items)
    return CostPreview(total_cny=round(total, 4), items=items)


# ---------------------------------------------------------------------------
# Error hints (9 categories, matching avatar-studio convention)
# ---------------------------------------------------------------------------

ERROR_HINTS: dict[str, dict[str, Any]] = {
    "network": {
        "label_zh": "网络错误",
        "label_en": "Network Error",
        "color": "orange",
        "hints_zh": ["检查网络连接", "检查代理设置", "稍后重试"],
        "hints_en": ["Check network connection", "Check proxy settings", "Retry later"],
    },
    "timeout": {
        "label_zh": "超时",
        "label_en": "Timeout",
        "color": "orange",
        "hints_zh": ["任务可能仍在处理中，请刷新查看", "长视频处理较慢，请耐心等待"],
        "hints_en": ["Task may still be processing, try refreshing", "Long videos take longer"],
    },
    "auth": {
        "label_zh": "认证错误",
        "label_en": "Auth Error",
        "color": "red",
        "hints_zh": ["到 Settings 重新配置 DashScope API Key", "确认 Key 有效且有余额"],
        "hints_en": ["Reconfigure DashScope API Key in Settings", "Verify key is valid"],
    },
    "quota": {
        "label_zh": "额度不足",
        "label_en": "Quota Exceeded",
        "color": "red",
        "hints_zh": ["阿里云百炼控制台充值", "检查账户余额"],
        "hints_en": ["Top up at Alibaba Cloud console", "Check account balance"],
    },
    "moderation": {
        "label_zh": "内容审核",
        "label_en": "Content Moderation",
        "color": "red",
        "hints_zh": ["视频内容未通过审核", "更换视频素材后重试"],
        "hints_en": ["Video content failed moderation", "Try with different source material"],
    },
    "dependency": {
        "label_zh": "依赖缺失",
        "label_en": "Missing Dependency",
        "color": "yellow",
        "hints_zh": ["到 Settings 查看 FFmpeg 安装引导", "需要 ffmpeg >= 4.0"],
        "hints_en": ["Check FFmpeg install guide in Settings", "Requires ffmpeg >= 4.0"],
    },
    "format": {
        "label_zh": "格式错误",
        "label_en": "Format Error",
        "color": "yellow",
        "hints_zh": ["确认是有效的 mp4/mkv/mov 文件", "视频文件可能已损坏"],
        "hints_en": ["Verify file is valid mp4/mkv/mov", "File may be corrupted"],
    },
    "duration": {
        "label_zh": "时长超限",
        "label_en": "Duration Exceeded",
        "color": "yellow",
        "hints_zh": ["视频时长超过 120 分钟上限", "请先手动截取后再上传"],
        "hints_en": ["Video exceeds 120-minute limit", "Trim video before uploading"],
    },
    "unknown": {
        "label_zh": "未知错误",
        "label_en": "Unknown Error",
        "color": "gray",
        "hints_zh": ["请将 task_id 反馈给开发者", "查看日志获取更多信息"],
        "hints_en": ["Report task_id to developer", "Check logs for details"],
    },
}


def get_error_hints(kind: str) -> dict[str, Any]:
    """Return error hint dict for the given error_kind, with unknown fallback."""
    return ERROR_HINTS.get(kind, ERROR_HINTS["unknown"])


MAX_VIDEO_DURATION_SEC: float = 7200.0
MAX_TRANSCRIPT_CHARS: int = 20000
