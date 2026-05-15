"""happyhorse-video data layer — 12 modes / voices / pricing / cost / error hints.

Pure data + pure functions. No I/O, no DashScope SDK import. Imported by
the client / pipeline / plugin layers and by tests with zero side effects.

Design notes
------------
- ``MODES`` is the single source of truth for the 12 generative flows
  exposed by the CreateTab top-level button group. Each ``ModeSpec``
  carries its ``required_assets`` (used by the cost-preview validator),
  ``cost_strategy`` (human-readable formula) and ``description_*``
  (UI tooltip text). ``ModeSpec`` deliberately *does not* hard-code
  ``dashscope_endpoint`` — endpoint dispatch lives in
  :mod:`happyhorse_model_registry` keyed by ``model_id`` so a single
  mode can route to multiple endpoints depending on the chosen model.
- ``PRICE_TABLE`` keeps DashScope's *officially documented* unit prices
  in one place. Tests freeze a known set so a remote price change can
  never silently shift the displayed cost.
- ``estimate_cost`` returns a ``CostPreview`` *without* any "milk-tea
  translation" gimmick — money is shown as ``¥{:.2f}`` end-to-end (the
  user explicitly rejected the previous translator).
- ``ERROR_HINTS`` maps the 9 ``ERROR_KIND_*`` values from
  :mod:`happyhorse_inline.vendor_client` to actionable bilingual hints
  (Pixelle C2 — no generic "Generation Failed" allowed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, TypedDict

from happyhorse_model_registry import (
    ALL_MODES,
    REGISTRY,
    RegistryPayload,
    default_model,
    models_for,
)

# ─── Mode catalog ────────────────────────────────────────────────────────

ModeId = Literal[
    "t2v",
    "i2v",
    "i2v_end",
    "video_extend",
    "r2v",
    "video_edit",
    "photo_speak",
    "video_relip",
    "video_reface",
    "pose_drive",
    "avatar_compose",
    "long_video",
]


@dataclass(frozen=True)
class ModeSpec:
    """One generative flow exposed by the CreateTab top button group."""

    id: ModeId
    label_zh: str
    label_en: str
    icon: str  # Iconify name (e.g. "lucide:video"), rendered by the React <Ico>
    group: Literal["video", "digital_human", "long_video"]
    required_assets: tuple[str, ...]
    description_zh: str
    description_en: str
    cost_strategy: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "icon": self.icon,
            "group": self.group,
            "required_assets": list(self.required_assets),
            "description_zh": self.description_zh,
            "description_en": self.description_en,
            "cost_strategy": self.cost_strategy,
        }


MODES: tuple[ModeSpec, ...] = (
    # ── Video generation group ────────────────────────────────────────
    ModeSpec(
        id="t2v",
        label_zh="文生视频",
        label_en="Text → Video",
        icon="lucide:video",
        group="video",
        required_assets=("prompt",),
        description_zh="一句中英文描述生成 3-15 秒视频。HappyHorse 1.0 原生音视频同步。",
        description_en="Generate a 3-15 s video from a text prompt. HappyHorse 1.0 emits audio synced natively.",
        cost_strategy="HappyHorse 720P 0.90元/秒 / 1080P 1.60元/秒；Wan 2.6 按官网定价",
    ),
    ModeSpec(
        id="i2v",
        label_zh="图生视频",
        label_en="Image → Video",
        icon="lucide:image-play",
        group="video",
        required_assets=("first_frame",),
        description_zh="一张首帧图 + prompt 生成 3-15 秒视频。",
        description_en="Generate a 3-15 s video from a first-frame image + prompt.",
        cost_strategy="同 t2v；Wan 2.6 i2v-flash 更便宜",
    ),
    ModeSpec(
        id="i2v_end",
        label_zh="首尾帧生视频",
        label_en="First & Last Frame → Video",
        icon="lucide:image-up",
        group="video",
        required_assets=("first_frame", "last_frame"),
        description_zh="首帧 + 尾帧生成连贯过渡视频（仅 wan2.7-i2v 支持）。",
        description_en="Generate a transition video from first + last frame (wan2.7-i2v only).",
        cost_strategy="使用 wan2.7-i2v 的 first-and-last-frame task type",
    ),
    ModeSpec(
        id="video_extend",
        label_zh="视频续写",
        label_en="Video Continuation",
        icon="lucide:fast-forward",
        group="video",
        required_assets=("source_video",),
        description_zh="从已有视频末尾向后续写一段（仅 wan2.7-i2v 支持）。",
        description_en="Continue an existing video forward (wan2.7-i2v only).",
        cost_strategy="使用 wan2.7-i2v 的 video-continuation task type",
    ),
    ModeSpec(
        id="r2v",
        label_zh="参考生视频",
        label_en="Reference → Video",
        icon="lucide:users",
        group="video",
        required_assets=("reference_urls",),
        description_zh="多角色 / 物品参考图生成互动视频，支持中英对话与镜头切换。",
        description_en="Multi-character reference-driven video with dialogue and cuts.",
        cost_strategy="HappyHorse 720P 0.90元/秒 / 1080P 1.60元/秒",
    ),
    ModeSpec(
        id="video_edit",
        label_zh="视频编辑",
        label_en="Video Edit",
        icon="lucide:wand-sparkles",
        group="video",
        required_assets=("source_video",),
        description_zh="风格转换 / 局部替换 / 画面增强。HappyHorse 1.0 video-edit 全自动完成。",
        description_en="Style transfer / local replacement / enhancement via HappyHorse 1.0 video-edit.",
        cost_strategy="按输入与输出视频时长计费",
    ),
    # ── Digital-human group ────────────────────────────────────────────
    ModeSpec(
        id="photo_speak",
        label_zh="照片说话",
        label_en="Photo Speak",
        icon="lucide:mic",
        group="digital_human",
        required_assets=("image", "audio_or_text"),
        description_zh="一张正面人像 + 一段语音/文本 → 会说话的视频。",
        description_en="One frontal portrait + speech/text → talking-head video.",
        cost_strategy="detect 0.004元/张 + s2v 0.50/0.90元/秒（按音频时长）+ TTS 0.20元/万字",
    ),
    ModeSpec(
        id="video_relip",
        label_zh="视频换嘴",
        label_en="Video Relip",
        icon="lucide:lips",
        group="digital_human",
        required_assets=("video", "audio_or_text"),
        description_zh="给已有视频换一段台词，自动同步口型。",
        description_en="Replace a video's speech and resync lip motion.",
        cost_strategy="videoretalk 0.30元/秒（按音频时长）+ TTS 0.20元/万字",
    ),
    ModeSpec(
        id="video_reface",
        label_zh="视频换人",
        label_en="Video Reface",
        icon="lucide:user-round-cog",
        group="digital_human",
        required_assets=("image", "video"),
        description_zh="保留参考视频的场景 / 动作，把主角替换成你提供的人像。",
        description_en="Keep the reference scene/motion, swap the actor to your portrait.",
        cost_strategy="wan-std 0.60元/秒 / wan-pro 1.20元/秒（按视频时长）",
    ),
    ModeSpec(
        id="pose_drive",
        label_zh="图生动作",
        label_en="Pose Drive",
        icon="lucide:person-standing",
        group="digital_human",
        required_assets=("image", "video"),
        description_zh="将参考视频的动作 / 表情迁移到一张人像上。",
        description_en="Transfer motion/expression from reference video to a portrait.",
        cost_strategy="wan-std 0.40元/秒 / wan-pro 0.60元/秒（按视频时长）",
    ),
    ModeSpec(
        id="avatar_compose",
        label_zh="数字人合成",
        label_en="Avatar Compose",
        icon="lucide:layers",
        group="digital_human",
        required_assets=("ref_images", "audio_or_text"),
        description_zh="多张参考图（人 + 场景）融合后生成会说话的数字人视频。",
        description_en="Blend portrait + scene references, then turn into a talking video.",
        cost_strategy="image 0.20/0.50元/张 + (可选 qwen-vl) + s2v + TTS",
    ),
    # ── Long-video group ────────────────────────────────────────────────
    ModeSpec(
        id="long_video",
        label_zh="长视频拼接",
        label_en="Long Video",
        icon="lucide:film",
        group="long_video",
        required_assets=("story",),
        description_zh="AI 自动拆分镜 → 串行 / 并行生成 → ffmpeg 拼接 → 输出长视频。",
        description_en="AI storyboard decomposition → serial/parallel chain generation → ffmpeg concat → long video.",
        cost_strategy="按每段 i2v 计费 + 可选 cosyvoice TTS",
    ),
)

MODES_BY_ID: dict[str, ModeSpec] = {m.id: m for m in MODES}


# ─── Audio duration constraints (DashScope hard caps) ────────────────────


@dataclass(frozen=True)
class AudioLimit:
    min_sec: float
    max_sec: float
    model_label: str

    def violates(self, dur: float) -> str | None:
        if dur is None or dur <= 0:
            return None
        if dur < self.min_sec:
            return (
                f"{self.model_label} 模型要求音频时长 ≥ {self.min_sec:g} 秒，"
                f"当前 {dur:.1f} 秒，请加长台词或上传更长的音频。"
            )
        if dur > self.max_sec:
            return (
                f"{self.model_label} 模型限制音频时长 ≤ {self.max_sec:g} 秒，"
                f"当前 {dur:.1f} 秒，请缩短台词或截取更短的音频片段。"
            )
        return None


# Conservative caps mirror avatar-studio (matches DashScope 2026-Q2 docs).
AUDIO_LIMITS: dict[str, AudioLimit] = {
    "photo_speak": AudioLimit(0.5, 19.5, "wan2.2-s2v"),
    "avatar_compose": AudioLimit(0.5, 19.5, "wan2.2-s2v"),
    "video_relip": AudioLimit(2.0, 120.0, "videoretalk"),
}


def check_audio_duration(mode: str, duration_sec: float | None) -> str | None:
    limit = AUDIO_LIMITS.get(mode)
    if limit is None or duration_sec is None:
        return None
    return limit.violates(float(duration_sec))


# ─── Voice catalog (12 cosyvoice-v2 system voices + 12 Edge-TTS voices) ──


@dataclass(frozen=True)
class VoiceSpec:
    id: str
    label_zh: str
    label_en: str
    gender: Literal["female", "male", "neutral"]
    style_zh: str
    style_en: str
    engine: Literal["cosyvoice", "edge"] = "cosyvoice"
    is_system: bool = True

    def to_dict(self) -> dict[str, object]:
        dashscope_id = f"{self.id}_v2" if self.engine == "cosyvoice" else self.id
        return {
            "id": self.id,
            "label": self.label_zh,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "gender": self.gender,
            "style": self.style_zh,
            "style_zh": self.style_zh,
            "style_en": self.style_en,
            "engine": self.engine,
            "is_system": self.is_system,
            "dashscope_voice_id": dashscope_id,
            "language": "zh",
        }


COSYVOICE_VOICES: tuple[VoiceSpec, ...] = (
    VoiceSpec("longxiaochun", "龙小淳", "Long Xiaochun", "female", "知性温暖", "intellectual"),
    VoiceSpec("longxiaobai", "龙小白", "Long Xiaobai", "female", "清亮活泼", "bright"),
    VoiceSpec("longxiaocheng", "龙小诚", "Long Xiaocheng", "male", "沉稳磁性", "calm"),
    VoiceSpec("longxiaoxia", "龙小夏", "Long Xiaoxia", "female", "甜美可爱", "sweet"),
    VoiceSpec("longxiaoshi", "龙小诗", "Long Xiaoshi", "female", "诗意优雅", "elegant"),
    VoiceSpec("longxiaoxi", "龙小溪", "Long Xiaoxi", "female", "灵动柔软", "gentle"),
    VoiceSpec("longxiaoxuan", "龙小璇", "Long Xiaoxuan", "female", "成熟稳重", "mature"),
    VoiceSpec("longwan", "龙婉", "Long Wan", "female", "温柔治愈", "warm"),
    VoiceSpec("longhan", "龙寒", "Long Han", "male", "冷峻深沉", "deep"),
    VoiceSpec("longhua", "龙华", "Long Hua", "male", "朝气阳光", "energetic"),
    VoiceSpec("longxiaohui", "龙小卉", "Long Xiaohui", "female", "邻家少女", "youthful"),
    VoiceSpec("longmiao", "龙妙", "Long Miao", "female", "知性主播", "news"),
)

# Edge-TTS Chinese voices — free tier, no API key needed.
EDGE_VOICES: tuple[VoiceSpec, ...] = (
    VoiceSpec("zh-CN-XiaoxiaoNeural", "晓晓 (Xiaoxiao)", "Xiaoxiao", "female", "温柔活泼", "warm", engine="edge"),
    VoiceSpec("zh-CN-YunxiNeural", "云希 (Yunxi)", "Yunxi", "male", "阳光少年", "bright", engine="edge"),
    VoiceSpec("zh-CN-YunyangNeural", "云扬 (Yunyang)", "Yunyang", "male", "新闻播报", "news", engine="edge"),
    VoiceSpec("zh-CN-XiaoyiNeural", "晓伊 (Xiaoyi)", "Xiaoyi", "female", "甜美", "sweet", engine="edge"),
    VoiceSpec("zh-CN-YunjianNeural", "云健 (Yunjian)", "Yunjian", "male", "沉稳", "calm", engine="edge"),
    VoiceSpec("zh-CN-YunxiaNeural", "云夏 (Yunxia)", "Yunxia", "male", "活力少年", "energetic", engine="edge"),
    VoiceSpec("zh-CN-liaoning-XiaobeiNeural", "晓贝 (Xiaobei 辽宁)", "Xiaobei", "female", "辽宁口音", "regional", engine="edge"),
    VoiceSpec("zh-CN-shaanxi-XiaoniNeural", "晓妮 (Xiaoni 陕西)", "Xiaoni", "female", "陕西口音", "regional", engine="edge"),
    VoiceSpec("zh-HK-HiuMaanNeural", "曉曼 (粤语)", "HiuMaan", "female", "粤语温柔", "cantonese", engine="edge"),
    VoiceSpec("zh-HK-WanLungNeural", "雲龍 (粤语)", "WanLung", "male", "粤语沉稳", "cantonese", engine="edge"),
    VoiceSpec("zh-TW-HsiaoChenNeural", "曉臻 (台湾)", "HsiaoChen", "female", "台湾口音", "taiwanese", engine="edge"),
    VoiceSpec("zh-TW-YunJheNeural", "雲哲 (台湾)", "YunJhe", "male", "台湾口音", "taiwanese", engine="edge"),
)

SYSTEM_VOICES: tuple[VoiceSpec, ...] = COSYVOICE_VOICES + EDGE_VOICES
VOICES_BY_ID: dict[str, VoiceSpec] = {v.id: v for v in SYSTEM_VOICES}


# ─── Resolution / aspect / duration option lists ─────────────────────────

RESOLUTIONS: tuple[str, ...] = ("480P", "720P", "1080P")
ASPECTS: tuple[str, ...] = ("1:1", "9:16", "16:9", "3:4", "4:3", "21:9", "4:5", "5:4")
DURATIONS_VIDEO: tuple[int, ...] = tuple(range(3, 16))
ANIMATE_MODES: tuple[str, ...] = ("wan-std", "wan-pro")
DEFAULT_COST_THRESHOLD_CNY: float = 5.00


# ─── Price table (officially documented DashScope unit prices, CNY) ──────

PRICE_TABLE: dict[str, dict[str, float]] = {
    # HappyHorse 1.0 family (4 endpoints, same per-second pricing):
    "happyhorse-1.0-t2v": {"720P_per_sec": 0.90, "1080P_per_sec": 1.60},
    "happyhorse-1.0-i2v": {"720P_per_sec": 0.90, "1080P_per_sec": 1.60},
    "happyhorse-1.0-r2v": {"720P_per_sec": 0.90, "1080P_per_sec": 1.60},
    "happyhorse-1.0-video-edit": {"720P_per_sec": 0.90, "1080P_per_sec": 1.60},
    # Wan 2.6 / 2.7 family — placeholder pricing (kept aligned with public
    # docs at v1.0.0 release; tests freeze a copy so a remote drift is
    # caught on next CI run):
    "wan2.6-t2v": {"720P_per_sec": 0.70, "1080P_per_sec": 1.20},
    "wan2.6-i2v": {"720P_per_sec": 0.70, "1080P_per_sec": 1.20},
    "wan2.6-i2v-flash": {"720P_per_sec": 0.30, "1080P_per_sec": 0.50},
    "wan2.6-r2v": {"720P_per_sec": 0.70, "1080P_per_sec": 1.20},
    "wan2.6-r2v-flash": {"720P_per_sec": 0.30, "1080P_per_sec": 0.50},
    "wan2.7-i2v": {"720P_per_sec": 0.85, "1080P_per_sec": 1.50},
    # Digital-human family (unchanged from avatar-studio):
    "wan2.2-s2v-detect": {"per_image": 0.004},
    "wan2.2-s2v": {"480P_per_sec": 0.50, "720P_per_sec": 0.90},
    "videoretalk": {"per_sec": 0.30},
    "wan2.2-animate-mix": {"wan-std_per_sec": 0.60, "wan-pro_per_sec": 1.20},
    "wan2.2-animate-move": {"wan-std_per_sec": 0.40, "wan-pro_per_sec": 0.60},
    # Image edit:
    "wan2.5-i2i-preview": {"per_image": 0.20},
    "wan2.7-image": {"per_image": 0.20},
    "wan2.7-image-pro": {"per_image": 0.50},
    # Auxiliary:
    "qwen-vl-max": {"per_1k_input_token": 0.02, "per_1k_output_token": 0.06},
    "cosyvoice-v2": {"per_10k_chars": 0.20},
    # Edge-TTS is free; included as zero so the cost row renders explicitly.
    "edge-tts": {"per_10k_chars": 0.0},
}


# ─── Cost preview types ──────────────────────────────────────────────────


class CostItem(TypedDict):
    name: str
    units: float
    unit_label: str
    unit_price: float
    subtotal: float
    note: str


class CostPreview(TypedDict):
    total: float
    currency: str
    items: list[CostItem]
    exceeds_threshold: bool
    threshold: float
    formatted_total: str


def _round(x: float | Decimal, places: int = 2) -> float:
    quant = Decimal(10) ** -places
    return float(Decimal(str(x)).quantize(quant, rounding=ROUND_HALF_UP))


def _fmt(x: float | Decimal) -> str:
    return f"¥{_round(x):.2f}"


# ─── Cost preview helpers ────────────────────────────────────────────────


def _resolution_key(params: dict[str, object], default: str = "720P") -> str:
    res = str(params.get("resolution") or default).upper()
    return res if res in {"480P", "720P", "1080P"} else default


def _duration_seconds(
    params: dict[str, object], audio_duration_sec: float | None
) -> float:
    if audio_duration_sec and audio_duration_sec > 0:
        return float(audio_duration_sec)
    raw = params.get("duration") or params.get("video_duration_sec") or 5.0
    return float(raw)


def _video_synth_item(
    model_id: str, params: dict[str, object], audio_duration_sec: float | None
) -> CostItem:
    """Per-second cost for HappyHorse / Wan video models (resolution-tiered)."""
    table = PRICE_TABLE.get(model_id, {})
    res = _resolution_key(params, default="720P")
    sec = _duration_seconds(params, audio_duration_sec)
    key = f"{res}_per_sec"
    per_sec = float(table.get(key, table.get("720P_per_sec", 0.0)))
    return CostItem(
        name=f"{model_id} {res}",
        units=sec,
        unit_label="秒",
        unit_price=per_sec,
        subtotal=_round(sec * per_sec),
        note="按音频时长计费" if audio_duration_sec else "按视频时长估算",
    )


def _item_face_detect() -> CostItem:
    price = PRICE_TABLE["wan2.2-s2v-detect"]["per_image"]
    return CostItem(
        name="wan2.2-s2v-detect",
        units=1.0,
        unit_label="张",
        unit_price=price,
        subtotal=_round(price, places=4),
        note="人脸预检（必要）",
    )


def _item_s2v(params: dict[str, object], audio_duration_sec: float | None) -> CostItem:
    resolution = _resolution_key(params, default="480P")
    sec = float(audio_duration_sec or params.get("duration") or 5.0)
    key = "720P_per_sec" if resolution == "720P" else "480P_per_sec"
    per_sec = PRICE_TABLE["wan2.2-s2v"][key]
    return CostItem(
        name=f"wan2.2-s2v {resolution}",
        units=sec,
        unit_label="秒",
        unit_price=per_sec,
        subtotal=_round(sec * per_sec),
        note="按音频时长计费" if audio_duration_sec else "按预估时长估算",
    )


def _item_videoretalk(params: dict[str, object], audio_duration_sec: float | None) -> CostItem:
    sec = float(audio_duration_sec or params.get("video_duration_sec") or 5.0)
    per_sec = PRICE_TABLE["videoretalk"]["per_sec"]
    return CostItem(
        name="videoretalk",
        units=sec,
        unit_label="秒",
        unit_price=per_sec,
        subtotal=_round(sec * per_sec),
        note="按音频时长计费" if audio_duration_sec else "按视频时长估算",
    )


def _item_animate(family: str, params: dict[str, object]) -> CostItem:
    sec = float(params.get("video_duration_sec") or 5.0)
    is_pro = bool(params.get("mode_pro"))
    table_key = f"wan2.2-animate-{family}"
    per_sec = PRICE_TABLE[table_key][
        "wan-pro_per_sec" if is_pro else "wan-std_per_sec"
    ]
    return CostItem(
        name=f"wan2.2-animate-{family} ({'wan-pro' if is_pro else 'wan-std'})",
        units=sec,
        unit_label="秒",
        unit_price=per_sec,
        subtotal=_round(sec * per_sec),
        note="按参考视频时长计费",
    )


def _item_image(model_id: str, count: int) -> CostItem:
    table = PRICE_TABLE.get(model_id, PRICE_TABLE["wan2.7-image"])
    per = table.get("per_image", 0.20)
    return CostItem(
        name=model_id,
        units=float(count),
        unit_label="张",
        unit_price=per,
        subtotal=_round(count * per),
        note="多图融合 / 风格生成",
    )


def _item_tts(text_chars: int, *, engine: str = "cosyvoice-v2") -> CostItem:
    units = max(1, text_chars) / 10000.0
    table_key = "cosyvoice-v2" if engine == "cosyvoice-v2" else "edge-tts"
    per = PRICE_TABLE[table_key]["per_10k_chars"]
    return CostItem(
        name=f"{table_key} TTS",
        units=round(units, 4),
        unit_label="万字",
        unit_price=per,
        subtotal=_round(units * per, places=4),
        note=f"约 {text_chars} 字" + ("（免费）" if per == 0 else ""),
    )


def _item_qwen(token_estimate: int) -> CostItem:
    units = max(1, token_estimate) / 1000.0
    per_in = PRICE_TABLE["qwen-vl-max"]["per_1k_input_token"]
    per_out = PRICE_TABLE["qwen-vl-max"]["per_1k_output_token"]
    blended = (per_in + per_out) / 2.0
    return CostItem(
        name="qwen-vl-max (prompt 辅助)",
        units=round(units, 4),
        unit_label="千 token",
        unit_price=round(blended, 4),
        subtotal=_round(units * blended, places=4),
        note="可选：让 LLM 写融合 prompt",
    )


# ─── estimate_cost (12-mode dispatch) ────────────────────────────────────


def estimate_cost(  # noqa: C901, PLR0912 — 12-mode dispatch is intentionally explicit
    mode: str,
    params: dict[str, object],
    *,
    audio_duration_sec: float | None = None,
    text_chars: int | None = None,
    threshold: float = DEFAULT_COST_THRESHOLD_CNY,
) -> CostPreview:
    """Estimate the CNY cost of one job, broken down by chargeable item.

    The model_id is read from ``params['model']``; if missing or invalid
    we fall back to the registry default for that mode.

    Args:
        mode: One of the 12 ``ModeId`` values.
        params: Mode-specific keys (must include ``model`` for video modes
            or ``ref_image_count`` for avatar_compose).
        audio_duration_sec: Real audio length when known (Pixelle P1 — TTS
            drives video length).
        text_chars: TTS script length in characters.
        threshold: CNY ceiling above which the UI shows the cost gate.
    """
    items: list[CostItem] = []
    model_id = str(params.get("model") or "")
    if not model_id:
        d = default_model(mode)
        if d is not None:
            model_id = d.model_id

    tts_engine = str(params.get("tts_engine") or "cosyvoice-v2")

    # ── Native video modes (HappyHorse + Wan 2.6/2.7 + long_video base) ──
    if mode in {
        "t2v",
        "i2v",
        "i2v_end",
        "video_extend",
        "r2v",
        "video_edit",
        "long_video",
    }:
        items.append(_video_synth_item(model_id, params, audio_duration_sec))
        # Long video also pays for cosyvoice TTS if the user supplied text.
        if mode == "long_video" and text_chars:
            items.append(_item_tts(text_chars, engine=tts_engine))

    elif mode == "photo_speak":
        items.append(_item_face_detect())
        items.append(_item_s2v(params, audio_duration_sec))
        if text_chars:
            items.append(_item_tts(text_chars, engine=tts_engine))

    elif mode == "video_relip":
        items.append(_item_videoretalk(params, audio_duration_sec))
        if text_chars:
            items.append(_item_tts(text_chars, engine=tts_engine))

    elif mode == "video_reface":
        items.append(_item_animate("mix", params))
        if text_chars:
            items.append(_item_tts(text_chars, engine=tts_engine))

    elif mode == "pose_drive":
        items.append(_item_animate("move", params))

    elif mode == "avatar_compose":
        n_ref = max(1, min(3, int(params.get("ref_image_count") or 1)))
        ic_model = model_id if model_id in PRICE_TABLE else "wan2.7-image"
        items.append(_item_image(ic_model, n_ref))
        if params.get("use_qwen_vl"):
            tokens = int(params.get("qwen_token_estimate") or 600)
            items.append(_item_qwen(tokens))
        items.append(_item_face_detect())
        items.append(_item_s2v(params, audio_duration_sec))
        if text_chars:
            items.append(_item_tts(text_chars, engine=tts_engine))

    else:
        raise ValueError(f"unknown mode: {mode!r}")

    raw_total = sum(it["units"] * it["unit_price"] for it in items)
    total = _round(raw_total)
    return CostPreview(
        total=total,
        currency="CNY",
        items=items,
        exceeds_threshold=total > threshold,
        threshold=threshold,
        formatted_total=_fmt(total),
    )


# ─── Error hints (Pixelle C2 — bilingual, actionable, 9 kinds) ───────────


class ErrorHint(TypedDict):
    title_zh: str
    title_en: str
    hints_zh: list[str]
    hints_en: list[str]


ERROR_HINTS: dict[str, ErrorHint] = {
    "network": {
        "title_zh": "网络异常",
        "title_en": "Network error",
        "hints_zh": [
            "请检查网络连接",
            "若使用代理请确认 https://dashscope.aliyuncs.com 可达",
            "稍后会自动重试 3 次",
        ],
        "hints_en": [
            "Check the network connection",
            "If a proxy is in use, verify dashscope.aliyuncs.com is reachable",
            "Will auto-retry up to 3 times",
        ],
    },
    "timeout": {
        "title_zh": "请求超时",
        "title_en": "Timeout",
        "hints_zh": [
            "任务可能仍在 DashScope 队列，30 秒后刷新「任务」页查看",
            "可在「设置」调高超时阈值",
        ],
        "hints_en": [
            "Task may still be queued; refresh Tasks in 30s",
            "Increase timeout in Settings",
        ],
    },
    "rate_limit": {
        "title_zh": "并发受限",
        "title_en": "Rate limited",
        "hints_zh": [
            "DashScope 异步任务并发上限为 1 / API Key，请等待当前任务完成",
            "或联系阿里云开通更高配额",
        ],
        "hints_en": [
            "DashScope concurrent limit = 1/API key; wait for the current task",
            "Or contact Aliyun to raise the quota",
        ],
    },
    "auth": {
        "title_zh": "鉴权失败",
        "title_en": "Auth failed",
        "hints_zh": [
            "请到「设置 → API Key」重新填写",
            "确认所选地域（北京 / 新加坡）的 Key 与 base_url 匹配",
        ],
        "hints_en": [
            "Re-enter the API Key in Settings",
            "Ensure the key matches the selected region (BJ / SG)",
        ],
    },
    "not_found": {
        "title_zh": "任务不存在",
        "title_en": "Task not found",
        "hints_zh": [
            "DashScope task_id 有效期 24 小时，可能已过期",
            "可在「任务」详情找到 dashscope_id 后重新提交",
        ],
        "hints_en": [
            "DashScope task_id expires after 24h",
            "Find the dashscope_id in Task details and resubmit",
        ],
    },
    "moderation": {
        "title_zh": "内容审核未通过",
        "title_en": "Content moderation",
        "hints_zh": [
            "输入图 / 视频 / 文本被识别为敏感，请更换素材",
            "常见原因：人脸不清晰 / 含水印 / 敏感主题 / 违规内容",
        ],
        "hints_en": [
            "Input was flagged sensitive; replace the asset",
            "Common: blurry face, watermark, violence, politics",
        ],
    },
    "client": {
        "title_zh": "请求参数错误",
        "title_en": "Bad request",
        "hints_zh": [
            "检查 prompt / resolution / duration 是否在该模型支持范围",
            "HappyHorse 1.0 不支持 with_audio / size / quality / fps / audio 参数",
        ],
        "hints_en": [
            "Check prompt / resolution / duration are in range",
            "HappyHorse 1.0 forbids with_audio / size / quality / fps / audio params",
        ],
    },
    "server": {
        "title_zh": "服务暂时不可用",
        "title_en": "Server error",
        "hints_zh": [
            "DashScope 后端 5xx，已自动重试 3 次仍失败",
            "可隔几分钟再重试，或在 Settings 切换地域",
        ],
        "hints_en": [
            "DashScope returned 5xx after 3 retries",
            "Try again in a few minutes or switch region in Settings",
        ],
    },
    "quota": {
        "title_zh": "余额不足",
        "title_en": "Quota exceeded",
        "hints_zh": [
            "请到阿里云百炼控制台充值",
            "或在「设置」切换到其他 API Key",
        ],
        "hints_en": [
            "Top up at the Bailian console",
            "Or switch the API Key in Settings",
        ],
    },
    "dependency": {
        "title_zh": "Python 依赖缺失",
        "title_en": "Python dependency missing",
        "hints_zh": [
            "在 Settings → Python 依赖 中点击「一键安装」",
            "需要的包：oss2（必装）/ dashscope（CosyVoice TTS）/ edge-tts（免费 TTS）",
        ],
        "hints_en": [
            "Click \"Install\" under Settings → Python dependencies",
            "Required: oss2 (must) / dashscope (CosyVoice TTS) / edge-tts (free TTS)",
        ],
    },
    "asset_rejected": {
        "title_zh": "素材不符合要求",
        "title_en": "Asset rejected",
        "hints_zh": [
            "输入图必须是真人正面照（不能是动物 / 卡通 / 侧脸 / 多人）",
            "参考视频建议 ≤ 30 秒，主角清晰可见",
        ],
        "hints_en": [
            "Input must be a clear, frontal human face (no cartoon / pets / multi-face)",
            "Reference video should be ≤ 30s with the actor visible",
        ],
    },
    "unknown": {
        "title_zh": "未知错误",
        "title_en": "Unknown error",
        "hints_zh": [
            "请将任务 id 发给开发者",
            "或截图任务详情页的 metadata json",
        ],
        "hints_en": [
            "Report the task id to the developer",
            "Or screenshot the metadata json from Task details",
        ],
    },
}


def hint_for(error_kind: str | None) -> ErrorHint:
    if not error_kind:
        return ERROR_HINTS["unknown"]
    return ERROR_HINTS.get(error_kind, ERROR_HINTS["unknown"])


# ─── Public dataclass surface for plugin.py wiring ───────────────────────


@dataclass(frozen=True)
class CatalogPayload:
    """Snapshot returned by GET /catalog so the UI gets one round-trip seed."""

    modes: list[dict[str, object]] = field(default_factory=list)
    voices: list[dict[str, object]] = field(default_factory=list)
    resolutions: list[str] = field(default_factory=list)
    aspects: list[str] = field(default_factory=list)
    animate_modes: list[str] = field(default_factory=list)
    durations_video: list[int] = field(default_factory=list)
    cost_threshold: float = DEFAULT_COST_THRESHOLD_CNY
    # Registry snapshot — list of model entries + per-mode default model_id.
    models: list[dict[str, object]] = field(default_factory=list)
    default_models: dict[str, str] = field(default_factory=dict)
    # Audio limit hints (UI inline tooltip).
    audio_limits: dict[str, dict[str, float | str]] = field(default_factory=dict)


def build_catalog() -> CatalogPayload:
    reg = RegistryPayload.build()
    return CatalogPayload(
        modes=[m.to_dict() for m in MODES],
        voices=[v.to_dict() for v in SYSTEM_VOICES],
        resolutions=list(RESOLUTIONS),
        aspects=list(ASPECTS),
        animate_modes=list(ANIMATE_MODES),
        durations_video=list(DURATIONS_VIDEO),
        cost_threshold=DEFAULT_COST_THRESHOLD_CNY,
        models=reg.models,
        default_models=reg.defaults,
        audio_limits={
            mode: {
                "min_sec": limit.min_sec,
                "max_sec": limit.max_sec,
                "model_label": limit.model_label,
            }
            for mode, limit in AUDIO_LIMITS.items()
        },
    )


# Re-export so callers can import everything from one place.
__all__ = [
    "ALL_MODES",
    "ANIMATE_MODES",
    "ASPECTS",
    "AUDIO_LIMITS",
    "AudioLimit",
    "CatalogPayload",
    "COSYVOICE_VOICES",
    "CostItem",
    "CostPreview",
    "DEFAULT_COST_THRESHOLD_CNY",
    "DURATIONS_VIDEO",
    "EDGE_VOICES",
    "ERROR_HINTS",
    "ErrorHint",
    "MODES",
    "MODES_BY_ID",
    "ModeId",
    "ModeSpec",
    "PRICE_TABLE",
    "REGISTRY",
    "RESOLUTIONS",
    "SYSTEM_VOICES",
    "VOICES_BY_ID",
    "VoiceSpec",
    "build_catalog",
    "check_audio_duration",
    "default_model",
    "estimate_cost",
    "hint_for",
    "models_for",
]
