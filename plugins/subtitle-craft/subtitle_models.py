"""Subtitle-craft mode definitions, pricing, style presets, and error hints.

Aligned with ``docs/subtitle-craft-plan.md`` §3 (4 modes), §4 (pricing), §5
(9-key ``ERROR_HINTS`` taxonomy 1:1 with clip-sense), and Phase 1 DoD.

Red-line guards baked in:

- ``ERROR_HINTS`` keys are exactly the 9 strings used by clip-sense /
  avatar-studio: ``network / timeout / auth / quota / moderation / dependency
  / format / duration / unknown``. **No ``rate_limit`` key** — 429 maps to
  ``quota`` (vendor classification → ERROR_HINTS write-time mapping happens
  in pipeline error handlers).
- Mode ids are the canonical orchestration names (``auto_subtitle``,
  ``translate``, ``repair``, ``burn``) — also used in ``plugin.json``
  ``provides.tools`` prefix matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Mode definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubtitleMode:
    """Definition of a single subtitle-craft mode."""

    id: str
    label_zh: str
    label_en: str
    icon: str
    description_zh: str
    description_en: str
    requires_api_key: bool = True
    requires_ffmpeg: bool = True
    requires_playwright: bool = False
    skip_steps: frozenset[str] = field(default_factory=frozenset)


MODES: list[SubtitleMode] = [
    SubtitleMode(
        id="auto_subtitle",
        label_zh="自动字幕",
        label_en="Auto Subtitle",
        icon="captions",
        description_zh="Paraformer-v2 词级转写 → SRT/VTT；可选启用说话人角色识别",
        description_en="Paraformer-v2 word-level ASR → SRT/VTT; optional speaker identification",
        requires_api_key=True,
        requires_ffmpeg=True,
    ),
    SubtitleMode(
        id="translate",
        label_zh="字幕翻译",
        label_en="Translate",
        icon="languages",
        description_zh="Qwen-MT 多语翻译，保留原 cue 时间轴",
        description_en="Qwen-MT multilingual translation, preserving original cue timing",
        requires_api_key=True,
        requires_ffmpeg=False,
        # step 4 (asr_or_load) loads the user-supplied SRT for non-auto modes;
        # only audio prep (step 3) and burn (step 7) are skipped.
        skip_steps=frozenset({"prepare_assets"}),
    ),
    SubtitleMode(
        id="repair",
        label_zh="字幕修复",
        label_en="Repair",
        icon="wrench",
        description_zh="时间轴修复 / 短 cue 扩展 / 重叠裁剪 / 智能换行（无 API 调用）",
        description_en="Timeline repair, short-cue extension, overlap trim, smart line wrap (no API)",
        requires_api_key=False,
        requires_ffmpeg=False,
        # step 4 still runs to load the user-supplied SRT into ctx.cues.
        skip_steps=frozenset({"prepare_assets"}),
    ),
    SubtitleMode(
        id="burn",
        label_zh="字幕烧制",
        label_en="Burn",
        icon="film",
        description_zh="ffmpeg ASS 滤镜 / 可选 Playwright HTML 透明 PNG overlay",
        description_en="ffmpeg ASS subtitles filter / optional Playwright HTML transparent PNG overlay",
        requires_api_key=False,
        requires_ffmpeg=True,
        requires_playwright=False,
        # step 4 loads the SRT to burn; step 5 (translate/repair) is the no-op.
        skip_steps=frozenset({"translate_or_repair"}),
    ),
]

MODES_BY_ID: dict[str, SubtitleMode] = {m.id: m for m in MODES}


def get_mode(mode_id: str) -> SubtitleMode | None:
    return MODES_BY_ID.get(mode_id)


def mode_to_dict(m: SubtitleMode) -> dict[str, Any]:
    return {
        "id": m.id,
        "label_zh": m.label_zh,
        "label_en": m.label_en,
        "icon": m.icon,
        "description_zh": m.description_zh,
        "description_en": m.description_en,
        "requires_api_key": m.requires_api_key,
        "requires_ffmpeg": m.requires_ffmpeg,
        "requires_playwright": m.requires_playwright,
        "skip_steps": sorted(m.skip_steps),
    }


# ---------------------------------------------------------------------------
# Subtitle style presets (used by ``burn`` mode A path: ffmpeg force_style)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubtitleStyle:
    """ASS ``force_style`` preset for ffmpeg subtitles filter."""

    id: str
    label_zh: str
    label_en: str
    font_name: str
    font_size: int
    primary_colour: str  # ASS BGR &Hbbggrr&
    outline_colour: str
    back_colour: str
    bold: int  # 0 / 1
    outline: float
    shadow: float
    margin_v: int
    alignment: int  # ASS alignment (1-9, 2=bottom-center)
    description_zh: str = ""

    def to_force_style(self) -> str:
        """Render to ``ffmpeg subtitles=...:force_style='...'`` argument."""
        return (
            f"FontName={self.font_name},FontSize={self.font_size},"
            f"PrimaryColour={self.primary_colour},"
            f"OutlineColour={self.outline_colour},"
            f"BackColour={self.back_colour},"
            f"Bold={self.bold},Outline={self.outline:g},"
            f"Shadow={self.shadow:g},MarginV={self.margin_v},"
            f"Alignment={self.alignment}"
        )


SUBTITLE_STYLES: list[SubtitleStyle] = [
    SubtitleStyle(
        id="default",
        label_zh="默认",
        label_en="Default",
        font_name="Microsoft YaHei",
        font_size=24,
        primary_colour="&H00FFFFFF",
        outline_colour="&H00000000",
        back_colour="&H80000000",
        bold=0,
        outline=2.0,
        shadow=1.0,
        margin_v=30,
        alignment=2,
        description_zh="白底黑边，适合大多数场景",
    ),
    SubtitleStyle(
        id="bold",
        label_zh="粗体放大",
        label_en="Bold Large",
        font_name="Microsoft YaHei",
        font_size=32,
        primary_colour="&H00FFFFFF",
        outline_colour="&H00000000",
        back_colour="&H80000000",
        bold=1,
        outline=3.0,
        shadow=1.5,
        margin_v=40,
        alignment=2,
        description_zh="粗体放大，适合移动端竖屏",
    ),
    SubtitleStyle(
        id="yellow",
        label_zh="经典黄字",
        label_en="Classic Yellow",
        font_name="Microsoft YaHei",
        font_size=26,
        primary_colour="&H0000FFFF",
        outline_colour="&H00000000",
        back_colour="&H80000000",
        bold=1,
        outline=2.5,
        shadow=1.0,
        margin_v=30,
        alignment=2,
        description_zh="经典电影院黄色描黑边",
    ),
    SubtitleStyle(
        id="minimal",
        label_zh="极简白",
        label_en="Minimal",
        font_name="Microsoft YaHei",
        font_size=22,
        primary_colour="&H00FFFFFF",
        outline_colour="&H00000000",
        back_colour="&H00000000",
        bold=0,
        outline=1.0,
        shadow=0.0,
        margin_v=24,
        alignment=2,
        description_zh="极简白色，无背景遮罩",
    ),
    SubtitleStyle(
        id="bilingual",
        label_zh="双语并排",
        label_en="Bilingual",
        font_name="Microsoft YaHei",
        font_size=22,
        primary_colour="&H00FFFFFF",
        outline_colour="&H00000000",
        back_colour="&H80000000",
        bold=0,
        outline=2.0,
        shadow=1.0,
        margin_v=36,
        alignment=2,
        description_zh="双行排版（原文 + 译文），margin 上调",
    ),
]

SUBTITLE_STYLES_BY_ID: dict[str, SubtitleStyle] = {s.id: s for s in SUBTITLE_STYLES}


# ---------------------------------------------------------------------------
# Translation models (Qwen-MT family)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TranslationModel:
    """One Qwen-MT model variant available for the ``translate`` mode."""

    id: str
    label_zh: str
    label_en: str
    price_cny_per_k_token: float
    description_zh: str = ""


TRANSLATION_MODELS: list[TranslationModel] = [
    TranslationModel(
        id="qwen-mt-flash",
        label_zh="Qwen-MT Flash（推荐）",
        label_en="Qwen-MT Flash (recommended)",
        price_cny_per_k_token=0.0006,
        description_zh="速度最快，性价比最高，适合大多数字幕翻译场景",
    ),
    TranslationModel(
        id="qwen-mt-plus",
        label_zh="Qwen-MT Plus（专业）",
        label_en="Qwen-MT Plus (premium)",
        price_cny_per_k_token=0.005,
        description_zh="质量最高，适合专业内容、术语密集字幕",
    ),
    TranslationModel(
        id="qwen-mt-lite",
        label_zh="Qwen-MT Lite（极速）",
        label_en="Qwen-MT Lite (fast)",
        price_cny_per_k_token=0.0003,
        description_zh="最便宜，适合快速预览、长视频草稿翻译",
    ),
]

TRANSLATION_MODELS_BY_ID: dict[str, TranslationModel] = {m.id: m for m in TRANSLATION_MODELS}


# ---------------------------------------------------------------------------
# Language code → Qwen-MT-required English name (P1-5)
# Qwen-MT requires English language *names* like "Chinese" / "English",
# not ISO 639 codes.
# ---------------------------------------------------------------------------

LANGUAGE_NAMES: dict[str, str] = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
    "pt": "Portuguese",
    "it": "Italian",
    "ar": "Arabic",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
}


def language_name(code: str) -> str:
    """Map an ISO-639 code (or English name) to the Qwen-MT English name.

    Unknown codes are returned title-cased verbatim so the user gets a
    meaningful error from Qwen-MT instead of a silent skip.
    """
    if not code:
        return ""
    lc = code.strip().lower()
    if lc in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[lc]
    if code in LANGUAGE_NAMES.values():
        return code
    return code.title()


# ---------------------------------------------------------------------------
# Pricing & cost estimation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceEntry:
    """Per-unit price for a single API."""

    api: str
    unit: str
    price_cny: float


PRICE_TABLE: list[PriceEntry] = [
    PriceEntry(api="paraformer-v2", unit="秒", price_cny=0.0008),
    PriceEntry(api="qwen-mt-flash", unit="千 input token", price_cny=0.0006),
    PriceEntry(api="qwen-mt-plus", unit="千 input token", price_cny=0.005),
    PriceEntry(api="qwen-mt-lite", unit="千 input token", price_cny=0.0003),
    PriceEntry(api="qwen-vl-max", unit="千 input token", price_cny=0.02),
]


@dataclass
class CostPreview:
    """Estimated cost breakdown for a task."""

    total_cny: float
    items: list[dict[str, Any]]


# Token estimation coefficient: ~0.7 token per char for mixed CJK + Latin text.
_TOKEN_PER_CHAR: float = 0.7

# Per-speaker character-identification cost estimate (Qwen-VL-max, ~250 input
# tokens per speaker after sample text trimming).
_CHAR_ID_COST_PER_SPEAKER: float = 0.005

# Burn-mode duration limit (12 hours per Paraformer-v2 single-file cap).
MAX_AUDIO_DURATION_SEC: float = 12 * 60 * 60.0

# Hard SRT line-length cap (CJK chars).
MAX_LINE_CHARS: int = 42


def estimate_cost(
    mode_id: str,
    *,
    duration_sec: float = 0.0,
    char_count: int = 0,
    translation_model: str = "qwen-mt-flash",
    character_identify: bool = False,
    speaker_count: int = 0,
) -> CostPreview:
    """Estimate API cost for a task.

    Parameters mirror ``docs/subtitle-craft-plan.md`` §4 formulas:

    - ``auto_subtitle``: ``duration_sec * 0.0008`` plus optional
      ``speaker_count * 0.005`` when ``character_identify=True``.
    - ``translate``: ``(char_count * 0.7) / 1000 * price_per_k_token``.
    - ``repair`` / ``burn``: 0.

    Unknown ``mode_id`` returns an empty preview rather than raising so the UI
    can render gracefully while the user picks a mode.
    """
    mode = MODES_BY_ID.get(mode_id)
    if mode is None:
        return CostPreview(total_cny=0.0, items=[])

    items: list[dict[str, Any]] = []

    if mode.id == "auto_subtitle":
        asr = max(0.0, duration_sec) * 0.0008
        items.append(
            {
                "api": "paraformer-v2",
                "description": "语音转写（词级时间戳）",
                "quantity": f"{duration_sec:.0f}秒",
                "cost_cny": round(asr, 4),
            }
        )
        if character_identify and speaker_count > 0:
            cid = speaker_count * _CHAR_ID_COST_PER_SPEAKER
            items.append(
                {
                    "api": "qwen-vl-max",
                    "description": "角色识别（说话人 → 角色名）",
                    "quantity": f"{speaker_count}人",
                    "cost_cny": round(cid, 4),
                }
            )

    elif mode.id == "translate":
        model = TRANSLATION_MODELS_BY_ID.get(translation_model)
        rate = model.price_cny_per_k_token if model else 0.0006
        tokens_est = max(0, char_count) * _TOKEN_PER_CHAR
        cost = (tokens_est / 1000.0) * rate
        items.append(
            {
                "api": translation_model,
                "description": "字幕翻译",
                "quantity": f"~{tokens_est:.0f} tokens（{char_count}字）",
                "cost_cny": round(cost, 4),
            }
        )

    # repair / burn: no API cost; still emit a 0-cost line for UI symmetry.
    elif mode.id in {"repair", "burn"}:
        items.append(
            {
                "api": "local",
                "description": "本地处理（ffmpeg / 算法）",
                "quantity": "—",
                "cost_cny": 0.0,
            }
        )

    total = sum(it["cost_cny"] for it in items)
    return CostPreview(total_cny=round(total, 4), items=items)


# ---------------------------------------------------------------------------
# Error hints (9 categories, **1:1 with clip-sense ERROR_HINTS keys**)
# Per ``docs/subtitle-craft-plan.md`` §5 (post-patch P-2): no ``rate_limit``
# key; 429 / concurrent-limit hits are mapped to ``quota`` (occasionally
# ``auth``) at write time inside the pipeline.
# ---------------------------------------------------------------------------

ERROR_HINTS: dict[str, dict[str, Any]] = {
    "network": {
        "label_zh": "网络错误",
        "label_en": "Network Error",
        "color": "orange",
        "hints_zh": [
            "请检查网络连接",
            "若使用代理请确认 dashscope.aliyuncs.com 可达",
            "稍后自动重试 3 次",
        ],
        "hints_en": [
            "Check network",
            "Verify proxy reaches dashscope.aliyuncs.com",
            "Will auto-retry 3 times",
        ],
    },
    "timeout": {
        "label_zh": "超时",
        "label_en": "Timeout",
        "color": "orange",
        "hints_zh": [
            "任务可能仍在 DashScope 队列，刷新查看",
            "可在 Settings 调高超时阈值",
        ],
        "hints_en": [
            "Task may still be queued; refresh",
            "Increase timeout in Settings",
        ],
    },
    "auth": {
        "label_zh": "认证错误",
        "label_en": "Auth Error",
        "color": "red",
        "hints_zh": [
            "Settings → API Key 重新填写",
            "确认 Key 与 base_url 地域匹配",
        ],
        "hints_en": [
            "Re-enter API Key",
            "Verify region matches",
        ],
    },
    "quota": {
        "label_zh": "额度不足或并发受限",
        "label_en": "Quota / Rate Limit",
        "color": "red",
        "hints_zh": [
            "阿里云百炼控制台充值或升级账户配额",
            "DashScope 录音文件转写并发上限较低，等待当前任务完成",
            "或在 Settings 切换 API Key",
        ],
        "hints_en": [
            "Top up at Bailian console or upgrade quota",
            "Concurrent limit reached; wait for the current task",
            "or switch API Key",
        ],
    },
    "moderation": {
        "label_zh": "内容审核未过",
        "label_en": "Content Moderation",
        "color": "red",
        "hints_zh": [
            "音频/字幕内容被识别为敏感",
            "可在 Settings 关闭敏感词过滤后重试",
        ],
        "hints_en": [
            "Content flagged as sensitive",
            "Disable filter in Settings to retry",
        ],
    },
    "dependency": {
        "label_zh": "依赖缺失",
        "label_en": "Missing Dependency",
        "color": "yellow",
        "hints_zh": [
            "前往 Settings 查看 ffmpeg 安装引导",
            "HTML 烧制需要 Playwright + 中文字体；已自动降级到 ASS 路径",
        ],
        "hints_en": [
            "See ffmpeg install guide",
            "HTML burn needs Playwright + CJK fonts; fell back to ASS",
        ],
    },
    "format": {
        "label_zh": "格式错误",
        "label_en": "Format Error",
        "color": "yellow",
        "hints_zh": [
            "确认是有效 mp4/mkv/mp3/wav/srt",
            "SRT 编码请用 UTF-8",
        ],
        "hints_en": [
            "Verify file format",
            "SRT must be UTF-8",
        ],
    },
    "duration": {
        "label_zh": "时长/体积超限",
        "label_en": "Duration / Size Exceeded",
        "color": "yellow",
        "hints_zh": [
            "Paraformer-v2 单文件上限 12 小时 / 2GB",
            "请先用 ffmpeg 手动截取后再上传",
        ],
        "hints_en": [
            "Paraformer-v2 single-file limit: 12h / 2GB",
            "Trim with ffmpeg before upload",
        ],
    },
    "unknown": {
        "label_zh": "未知错误",
        "label_en": "Unknown Error",
        "color": "gray",
        "hints_zh": [
            "请将 task_id 反馈给开发者",
            "截图 metadata.json 一并提供",
        ],
        "hints_en": [
            "Report task_id to developer",
            "Screenshot metadata.json",
        ],
    },
}


# Stable canonical 9-key set used by red-line tests and pipeline write paths.
ALLOWED_ERROR_KINDS: frozenset[str] = frozenset(ERROR_HINTS.keys())


def get_error_hints(kind: str) -> dict[str, Any]:
    """Return error hint dict for the given error_kind, with unknown fallback."""
    return ERROR_HINTS.get(kind, ERROR_HINTS["unknown"])


def map_vendor_kind_to_error_kind(vendor_kind: str) -> str:
    """Map a raw ``subtitle_craft_inline.vendor_client.ERROR_KIND_*`` into the
    canonical 9-key taxonomy used by ERROR_HINTS / DB writes.

    Vendor client raw kinds (``rate_limit``, ``not_found``, ``client``,
    ``server``) are *internal HTTP transport* classifications — pipeline
    code must always remap them before writing ``tasks.error_kind`` so the
    UI ErrorPanel receives one of the 9 documented kinds.
    """
    mapping = {
        "network": "network",
        "timeout": "timeout",
        "rate_limit": "quota",
        "auth": "auth",
        "not_found": "format",
        "moderation": "moderation",
        "client": "format",
        "server": "network",
        "unknown": "unknown",
    }
    return mapping.get(vendor_kind, "unknown")
