"""media-post mode catalog, platform/aspect tables, pricing, and error hints.

Aligned with ``docs/media-post-plan.md`` §3.1 (4 modes), §4 (pricing
table + cost thresholds), §5 (9-key ``ERROR_HINTS`` 1:1 with
clip-sense / subtitle-craft / avatar-studio), §3.4 (8 step pipeline
names per mode), and Phase 1 DoD.

Red-line guards baked in:

- ``ERROR_HINTS`` keys are exactly the 9 strings used by all sibling
  first-class plugins (``network / timeout / auth / quota / moderation
  / dependency / format / duration / unknown``). 429 / rate-limit hits
  map to ``quota`` at write time inside the pipeline (no ``rate_limit``
  key here — the plan §5 explicitly forbids it).
- Mode ids are the canonical orchestration names used by
  ``provides.tools`` prefix matching, the UI ``mode-btn`` ``data-mode``
  attribute, and the ``STEP_DISPATCH`` table in
  ``mediapost_pipeline``: ``cover_pick / multi_aspect / seo_pack /
  chapter_cards``. Any rename here cascades into UI / pipeline /
  i18n / tests.
- ``COST_THRESHOLD_*`` constants are the public single-task spend
  guardrails referenced by §4 (¥10 / ¥30) and the UI ``oa-cost`` color
  ramp (green / orange / red).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Mode definitions (4 modes — frozen for v1.0; v1.1 may extend `seo_pack` to
# accept user-supplied prompt yaml, but the id will not change).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaPostMode:
    """Definition of a single media-post mode.

    ``requires_*`` flags drive the UI ``oa-config-banner`` / ``oa-dep-banner``
    pre-flight check (Phase 5) and the pipeline early-exit guards (Phase 3).
    """

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


MODES: list[MediaPostMode] = [
    MediaPostMode(
        id="cover_pick",
        label_zh="智能选封面",
        label_en="Smart Cover Pick",
        icon="image",
        description_zh="ffmpeg thumbnail 预筛 30 候选 → Qwen-VL-max 6 维评分排序 → top-N 落盘",
        description_en="ffmpeg thumbnail prefilter -> Qwen-VL-max 6-axis aesthetic scoring -> top-N",
        requires_api_key=True,
        requires_ffmpeg=True,
    ),
    MediaPostMode(
        id="multi_aspect",
        label_zh="横竖屏适配",
        label_en="Smart Recompose",
        icon="aspect-ratio",
        description_zh="场景切分 + Qwen-VL 主体跟踪 + EMA 平滑 + ffmpeg 动态 crop（9:16 / 1:1）",
        description_en="Scene cuts + Qwen-VL subject tracking + EMA smoothing + ffmpeg dynamic crop",
        requires_api_key=True,
        requires_ffmpeg=True,
    ),
    MediaPostMode(
        id="seo_pack",
        label_zh="5 平台 SEO 包",
        label_en="5-Platform SEO Pack",
        icon="hash",
        description_zh="抖音 / B 站 / 视频号 / 小红书 / YouTube 五平台并行 Qwen-Plus 文案",
        description_en="TikTok / Bilibili / WeChat / Xiaohongshu / YouTube parallel SEO text",
        requires_api_key=True,
        requires_ffmpeg=False,
        # `seo_pack` only needs subtitle text + 1 thumbnail frame; skip the
        # heavy `extract_frames` + VLM-detect steps. Cost stays ~¥0.025.
        skip_steps=frozenset({"extract_frames"}),
    ),
    MediaPostMode(
        id="chapter_cards",
        label_zh="章节卡 PNG",
        label_en="Chapter Cards",
        icon="layers",
        description_zh="Playwright HTML 渲染（A 路径） + ffmpeg drawtext 兜底（B 路径）",
        description_en="Playwright HTML rendering (A) + ffmpeg drawtext fallback (B)",
        requires_api_key=False,
        requires_ffmpeg=True,
        # `chapter_cards` is purely local: no video upload required, no VLM.
        skip_steps=frozenset({"extract_frames", "vlm_or_seo"}),
    ),
]

MODES_BY_ID: dict[str, MediaPostMode] = {m.id: m for m in MODES}

ALLOWED_MODES: frozenset[str] = frozenset(MODES_BY_ID.keys())


def get_mode(mode_id: str) -> MediaPostMode | None:
    return MODES_BY_ID.get(mode_id)


def mode_to_dict(m: MediaPostMode) -> dict[str, Any]:
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
# SEO platforms (used by `seo_pack` mode).
#
# v1.0 freezes 5 platforms; the actual prompt templates live in
# `mediapost_seo_generator.PLATFORM_PROMPTS` (Phase 3) so this catalog
# only carries display-side metadata. Any new platform here MUST also
# add a key in `PLATFORM_PROMPTS` or the pipeline raises `format`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeoPlatform:
    id: str
    label_zh: str
    label_en: str
    char_limit_title: int
    char_limit_description: int
    supports_chapters: bool = False


PLATFORMS: list[SeoPlatform] = [
    SeoPlatform(
        id="tiktok",
        label_zh="抖音 / TikTok",
        label_en="TikTok / Douyin",
        char_limit_title=30,
        char_limit_description=300,
    ),
    SeoPlatform(
        id="bilibili",
        label_zh="B 站",
        label_en="Bilibili",
        char_limit_title=80,
        char_limit_description=300,
        supports_chapters=True,
    ),
    SeoPlatform(
        id="wechat",
        label_zh="视频号",
        label_en="WeChat Channels",
        char_limit_title=22,
        char_limit_description=200,
    ),
    SeoPlatform(
        id="xiaohongshu",
        label_zh="小红书",
        label_en="Xiaohongshu",
        char_limit_title=20,
        char_limit_description=1000,
    ),
    SeoPlatform(
        id="youtube",
        label_zh="YouTube",
        label_en="YouTube",
        char_limit_title=100,
        char_limit_description=5000,
        supports_chapters=True,
    ),
]

PLATFORMS_BY_ID: dict[str, SeoPlatform] = {p.id: p for p in PLATFORMS}

ALLOWED_PLATFORMS: frozenset[str] = frozenset(PLATFORMS_BY_ID.keys())


def platform_to_dict(p: SeoPlatform) -> dict[str, Any]:
    return {
        "id": p.id,
        "label_zh": p.label_zh,
        "label_en": p.label_en,
        "char_limit_title": p.char_limit_title,
        "char_limit_description": p.char_limit_description,
        "supports_chapters": p.supports_chapters,
    }


# ---------------------------------------------------------------------------
# Aspect ratios (used by `multi_aspect`).
#
# v1.0 freezes 2 (9:16 + 1:1) per §1.5; v1.1 may add 3:4 / 21:9 / 4:5.
# Output dimensions are sized for 1080p source video; `multi_aspect`
# pipeline derives crop_w / crop_h from these + the source video's
# orientation rather than hard-coding pixel sizes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AspectRatio:
    id: str  # canonical "W:H" string used in API + UI
    label_zh: str
    label_en: str
    output_w: int
    output_h: int
    is_default: bool = False


ASPECTS: list[AspectRatio] = [
    AspectRatio(
        id="9:16",
        label_zh="竖版 9:16（抖音 / 视频号）",
        label_en="Vertical 9:16 (TikTok / WeChat)",
        output_w=608,
        output_h=1080,
        is_default=True,
    ),
    AspectRatio(
        id="1:1",
        label_zh="方版 1:1（视频号方图）",
        label_en="Square 1:1 (WeChat square)",
        output_w=1080,
        output_h=1080,
    ),
]

ASPECTS_BY_ID: dict[str, AspectRatio] = {a.id: a for a in ASPECTS}

ALLOWED_ASPECTS: frozenset[str] = frozenset(ASPECTS_BY_ID.keys())


def aspect_to_dict(a: AspectRatio) -> dict[str, Any]:
    return {
        "id": a.id,
        "label_zh": a.label_zh,
        "label_en": a.label_en,
        "output_w": a.output_w,
        "output_h": a.output_h,
        "is_default": a.is_default,
    }


# ---------------------------------------------------------------------------
# Pricing & cost estimation (per §4).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceEntry:
    """Per-unit price for a single API."""

    api: str
    unit: str
    price_cny: float


PRICE_TABLE: list[PriceEntry] = [
    # Qwen-VL-max input ≈ ¥0.02/k token; one 512x288 base64 frame ≈ 500 tokens.
    # An 8-frame batch (~4k tokens input + ~500 tokens output) costs ≈ ¥0.08.
    PriceEntry(api="qwen-vl-max", unit="千 input token", price_cny=0.02),
    PriceEntry(api="qwen-vl-max-batch-8frames", unit="批", price_cny=0.08),
    # Qwen-Plus for SEO text: ~¥0.0008 input + ~¥0.002 output per k token.
    # Per-platform call ≈ ¥0.005 with conservative excerpt sizing.
    PriceEntry(api="qwen-plus", unit="次（5k input + 1k output）", price_cny=0.005),
    PriceEntry(api="ffmpeg-local", unit="—", price_cny=0.0),
    PriceEntry(api="playwright-local", unit="—", price_cny=0.0),
]

PRICE_TABLE_BY_API: dict[str, PriceEntry] = {p.api: p for p in PRICE_TABLE}

# Per-batch cost for the 8-frame Qwen-VL-max call (cover_pick + multi_aspect).
_COST_PER_VLM_BATCH: float = 0.08

# Per-platform cost for Qwen-Plus SEO call (seo_pack).
_COST_PER_SEO_PLATFORM: float = 0.005

# Multi-aspect runs share VLM detection across aspects, so the second / Nth
# aspect re-uses the first detection pass (only ffmpeg crop runs again).
# Approximated as ``len(target_aspects) * 0.5`` instead of ``* 1.0``.
_MULTI_ASPECT_REUSE_FACTOR: float = 0.5

# v1.0 fixed batch size for VLM (locked per red-line §13 #6).
DEFAULT_VLM_BATCH_SIZE: int = 8

# v1.0 default frame extraction rate for `multi_aspect`.
DEFAULT_RECOMPOSE_FPS: float = 2.0

# Cost ramp thresholds (§4 + §9.6 oa-cost color):
COST_THRESHOLD_WARN_CNY: float = 10.0  # banner turns orange + button needs confirm
COST_THRESHOLD_DANGER_CNY: float = 30.0  # banner turns red + extra confirm

# Multi-aspect duration cap (mins) — v1.0 strongly recommends ≤30 min per
# §1.5; longer videos are still allowed but raise an `oa-config-banner` warning.
MULTI_ASPECT_RECOMMENDED_MAX_MIN: int = 30


@dataclass
class CostPreview:
    """Estimated cost breakdown for a task."""

    total_cny: float
    items: list[dict[str, Any]]
    cost_kind: str  # "ok" | "warn" | "danger" — drives oa-cost color


def _classify_cost(total_cny: float) -> str:
    if total_cny >= COST_THRESHOLD_DANGER_CNY:
        return "danger"
    if total_cny >= COST_THRESHOLD_WARN_CNY:
        return "warn"
    return "ok"


def estimate_cost(  # noqa: C901  (per-mode branch table is intentionally explicit)
    mode_id: str,
    *,
    duration_sec: float = 0.0,
    quantity: int = 8,
    target_aspects: list[str] | None = None,
    platforms: list[str] | None = None,
    recompose_fps: float = DEFAULT_RECOMPOSE_FPS,
    chapter_count: int = 0,
) -> CostPreview:
    """Estimate API cost for a media-post task.

    Formulas mirror ``docs/media-post-plan.md`` §4:

    - ``cover_pick``: ``ceil(quantity / 8 + 1) * 0.08`` — one prefilter
      pass plus ``ceil(quantity / 8)`` VLM batches over the 30 candidates.
    - ``multi_aspect``: ``frames / 8 * 0.08 * len(aspects) * 0.5`` where
      ``frames = duration_sec * recompose_fps``. The 0.5 reuse factor
      reflects that the second aspect re-uses the first VLM detection
      pass (only ffmpeg crop is rerun). Floor cost at one batch.
    - ``seo_pack``: ``len(platforms) * 0.005``.
    - ``chapter_cards``: 0 — purely local rendering.

    Unknown modes return an empty preview rather than raising so the UI
    can render gracefully while the user picks a mode.
    """
    mode = MODES_BY_ID.get(mode_id)
    if mode is None:
        return CostPreview(total_cny=0.0, items=[], cost_kind="ok")

    items: list[dict[str, Any]] = []

    if mode.id == "cover_pick":
        # `quantity` candidates land in ceil(quantity / 8) batches, plus one
        # "30 prefilter -> 4 batches" pass that costs the same shape.
        n_batches = max(1, -(-int(quantity) // DEFAULT_VLM_BATCH_SIZE))
        cost = n_batches * _COST_PER_VLM_BATCH
        items.append(
            {
                "api": "qwen-vl-max",
                "description": "VLM 6 维评分（30 候选 → top-N）",
                "quantity": f"{n_batches}批×8帧",
                "cost_cny": round(cost, 4),
            }
        )

    elif mode.id == "multi_aspect":
        aspects = list(target_aspects or [])
        if not aspects:
            aspects = [ASPECTS[0].id]
        n_frames = max(1.0, duration_sec) * max(0.1, recompose_fps)
        n_batches = max(1, -(-int(n_frames) // DEFAULT_VLM_BATCH_SIZE))
        # First aspect carries full VLM cost; remaining aspects share via reuse.
        weight = 1.0 + (len(aspects) - 1) * _MULTI_ASPECT_REUSE_FACTOR
        cost = n_batches * _COST_PER_VLM_BATCH * weight
        items.append(
            {
                "api": "qwen-vl-max",
                "description": (
                    f"主体检测（fps={recompose_fps}, "
                    f"{int(n_frames)} 帧, {len(aspects)} aspect）"
                ),
                "quantity": f"{n_batches}批",
                "cost_cny": round(cost, 4),
            }
        )

    elif mode.id == "seo_pack":
        plats = list(platforms or [p.id for p in PLATFORMS])
        cost = len(plats) * _COST_PER_SEO_PLATFORM
        items.append(
            {
                "api": "qwen-plus",
                "description": f"SEO 文案（{len(plats)} 平台并行）",
                "quantity": f"{len(plats)}×平台",
                "cost_cny": round(cost, 4),
            }
        )

    elif mode.id == "chapter_cards":
        items.append(
            {
                "api": "playwright-local",
                "description": f"本地渲染（Playwright A / drawtext B），{chapter_count} 章",
                "quantity": f"{chapter_count}张",
                "cost_cny": 0.0,
            }
        )

    total = round(sum(it["cost_cny"] for it in items), 4)
    return CostPreview(total_cny=total, items=items, cost_kind=_classify_cost(total))


# ---------------------------------------------------------------------------
# Error hints (9 categories, **1:1 with clip-sense / subtitle-craft**).
#
# Per ``docs/media-post-plan.md`` §5: no ``rate_limit`` key. 429 hits map
# to ``quota`` at write-time inside the pipeline. ``moderation`` covers
# Qwen-VL refusing to analyze sensitive frames.
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
            "视频太长可能引发超时（multi_aspect 建议 ≤30 分钟）",
            "可在 Settings 调高超时阈值",
            "或分段处理",
        ],
        "hints_en": [
            "Long video may timeout (multi_aspect recommends ≤30 min)",
            "Increase timeout in Settings",
            "Or split video",
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
            "DashScope VLM 并发上限较低，等待当前任务完成",
            "或在 Settings 切换 API Key",
        ],
        "hints_en": [
            "Top up at Bailian console or upgrade quota",
            "VLM concurrent limit reached; wait for the current task",
            "or switch API Key",
        ],
    },
    "moderation": {
        "label_zh": "内容审核未过",
        "label_en": "Content Moderation",
        "color": "red",
        "hints_zh": [
            "VLM 拒绝分析（敏感内容）",
            "可手动选封面或换视频",
        ],
        "hints_en": [
            "VLM refused (sensitive content)",
            "Pick cover manually or switch video",
        ],
    },
    "dependency": {
        "label_zh": "依赖缺失",
        "label_en": "Missing Dependency",
        "color": "yellow",
        "hints_zh": [
            "前往 Settings 查看 ffmpeg 安装引导",
            "章节卡 HTML 渲染需要 Playwright + 中文字体；已自动降级到 drawtext",
        ],
        "hints_en": [
            "See ffmpeg install guide",
            "Chapter cards need Playwright + CJK fonts; fell back to drawtext",
        ],
    },
    "format": {
        "label_zh": "格式错误",
        "label_en": "Format Error",
        "color": "yellow",
        "hints_zh": [
            "确认是有效 mp4/mkv/mov",
            "ffprobe 无法解析说明文件损坏",
        ],
        "hints_en": [
            "Verify mp4/mkv/mov",
            "File corrupted if ffprobe fails",
        ],
    },
    "duration": {
        "label_zh": "时长/体积超限",
        "label_en": "Duration / Size Exceeded",
        "color": "yellow",
        "hints_zh": [
            "multi_aspect 模式建议 ≤30 分钟（成本随时长线性增长）",
            "cover_pick / seo_pack 可处理任意时长",
        ],
        "hints_en": [
            "multi_aspect recommends ≤30 min (cost scales linearly)",
            "cover_pick / seo_pack handle any length",
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
    """Map a raw vendor-client error kind into the 9-key taxonomy.

    The vendor client (``mediapost_vlm_client``) classifies HTTP failures
    using transport-level kinds (``rate_limit``, ``not_found``, ``client``,
    ``server``). Pipeline code must always remap these before writing
    ``tasks.error_kind`` so the UI ErrorPanel receives one of the 9
    documented kinds.
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


# ---------------------------------------------------------------------------
# MediaPostError — structured exception used by all client / mode modules.
# Pipeline catches this and writes ``error_kind`` straight into the task row.
# ---------------------------------------------------------------------------


class MediaPostError(Exception):
    """Structured exception with a canonical 9-key ``error_kind``.

    Modules raise ``MediaPostError("dependency", "ffmpeg crop failed: …")``
    and the pipeline (``mediapost_pipeline.run_pipeline``) translates it
    into a task-row update + UI broadcast. Unknown kinds are coerced to
    ``"unknown"`` so the DB column always satisfies the ALLOWED_ERROR_KINDS
    invariant tested in ``test_models.py``.
    """

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message)
        self.kind = kind if kind in ALLOWED_ERROR_KINDS else "unknown"
        self.message = message

    def __str__(self) -> str:
        return f"[{self.kind}] {self.message}" if self.message else f"[{self.kind}]"


__all__ = [
    "ALLOWED_ASPECTS",
    "ALLOWED_ERROR_KINDS",
    "ALLOWED_MODES",
    "ALLOWED_PLATFORMS",
    "ASPECTS",
    "ASPECTS_BY_ID",
    "AspectRatio",
    "COST_THRESHOLD_DANGER_CNY",
    "COST_THRESHOLD_WARN_CNY",
    "CostPreview",
    "DEFAULT_RECOMPOSE_FPS",
    "DEFAULT_VLM_BATCH_SIZE",
    "ERROR_HINTS",
    "MODES",
    "MODES_BY_ID",
    "MULTI_ASPECT_RECOMMENDED_MAX_MIN",
    "MediaPostError",
    "MediaPostMode",
    "PLATFORMS",
    "PLATFORMS_BY_ID",
    "PRICE_TABLE",
    "PRICE_TABLE_BY_API",
    "PriceEntry",
    "SeoPlatform",
    "aspect_to_dict",
    "estimate_cost",
    "get_error_hints",
    "get_mode",
    "map_vendor_kind_to_error_kind",
    "mode_to_dict",
    "platform_to_dict",
]
