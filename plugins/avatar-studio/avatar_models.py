"""avatar-studio data layer — modes, voices, pricing, cost estimation, error hints.

Pure data + pure functions. No I/O, no DashScope SDK import. Imported by the
client / pipeline / plugin layers and by tests with zero side effects.

Design notes
------------
- ``MODES`` is the single source of truth for the 4 generative flows. Each
  ``ModeSpec.dashscope_endpoint`` drives the pipeline's ``video_synth`` step
  dispatch (Pixelle A3 — naming-prefix routing).
- ``PRICE_TABLE`` keeps DashScope's *officially documented* unit prices in
  one place. Tests freeze a known set so a remote price change can never
  silently shift the displayed cost.
- ``estimate_cost`` returns a ``CostPreview`` *without* any "milk-tea
  translation" gimmick — money is shown as ``¥{:.2f}`` end-to-end (the
  user explicitly rejected the previous translator).
- ``ERROR_HINTS`` maps the 9 ``ERROR_KIND_*`` values from
  ``avatar_studio_inline.vendor_client`` to actionable bilingual hints
  (Pixelle C2 — no generic "Generation Failed" allowed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, TypedDict

# ─── Mode catalog ────────────────────────────────────────────────────────

ModeId = Literal["photo_speak", "video_relip", "video_reface", "avatar_compose", "pose_drive"]


@dataclass(frozen=True)
class ModeSpec:
    """One generative flow exposed by the CreateTab top button group."""

    id: ModeId
    label_zh: str
    label_en: str
    icon: str  # OpenAkitaIcons key, rendered by <Ico>
    required_assets: tuple[str, ...]  # ("image",), ("video", "audio"), ...
    dashscope_endpoint: str  # which client method to dispatch to (Pixelle A3)
    description_zh: str
    description_en: str
    cost_strategy: str  # human-readable formula, used by /cost-preview

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "icon": self.icon,
            "required_assets": list(self.required_assets),
            "dashscope_endpoint": self.dashscope_endpoint,
            "description_zh": self.description_zh,
            "description_en": self.description_en,
            "cost_strategy": self.cost_strategy,
        }


MODES: tuple[ModeSpec, ...] = (
    ModeSpec(
        id="photo_speak",
        label_zh="照片说话",
        label_en="Photo Speak",
        icon="user-voice",
        required_assets=("image", "audio_or_text"),
        dashscope_endpoint="submit_s2v",
        description_zh="一张正面人像照 + 一段语音/文本，输出会说话的视频",
        description_en="One frontal portrait + speech/text → talking-head video",
        cost_strategy="detect 0.004元/张 + s2v 0.50/0.90元/秒（按音频时长）+ TTS 0.20元/万字",
    ),
    ModeSpec(
        id="video_relip",
        label_zh="视频换嘴",
        label_en="Video Relip",
        icon="video-edit",
        required_assets=("video", "audio_or_text"),
        dashscope_endpoint="submit_videoretalk",
        description_zh="给已有视频换一段台词，自动同步口型",
        description_en="Replace a video's speech and resync the lip movement",
        cost_strategy="videoretalk 0.30元/秒（按音频时长）+ TTS 0.20元/万字",
    ),
    ModeSpec(
        id="video_reface",
        label_zh="视频换人",
        label_en="Video Reface",
        icon="user-swap",
        required_assets=("image", "video"),
        dashscope_endpoint="submit_animate_mix",
        description_zh="保留参考视频的场景与动作，把主角替换成你提供的人像",
        description_en="Keep the reference video's scene/motion, swap the actor",
        cost_strategy="wan-std 0.60元/秒 或 wan-pro 1.20元/秒（按视频时长）",
    ),
    ModeSpec(
        id="avatar_compose",
        label_zh="数字人合成",
        label_en="Avatar Compose",
        icon="image-merge",
        required_assets=("image", "scene_or_image", "audio_or_text"),
        dashscope_endpoint="submit_image_edit",  # then chains into submit_s2v
        description_zh="多图融合（人 + 场景）后，生成会说话的数字人视频",
        description_en="Blend portrait + scene, then turn it into a talking video",
        cost_strategy="i2i 0.20元/张 + (可选 qwen-vl 写 prompt) + s2v 0.50/0.90元/秒 + TTS",
    ),
    ModeSpec(
        id="pose_drive",
        label_zh="图生动作",
        label_en="Pose Drive",
        icon="walk",
        required_assets=("image", "video"),
        dashscope_endpoint="submit_animate_move",
        description_zh="将参考视频的动作/表情迁移到人像照片上，生成动作视频",
        description_en="Transfer motion/expression from reference video to a portrait photo",
        cost_strategy="wan-std 0.40元/秒 或 wan-pro 0.60元/秒（按视频时长）",
    ),
)

MODES_BY_ID: dict[str, ModeSpec] = {m.id: m for m in MODES}


# ─── Audio duration constraints ──────────────────────────────────────────
#
# DashScope models have hard caps on the input audio length. Hitting them
# late (i.e. inside the pipeline) burns user money on TTS + face-detect
# before failing at the s2v step with the misleading
# ``video synth failed: The input audio is longer than 20s``.
#
# We surface these limits in three places to give layered feedback:
# 1. ``MODE_DEFS`` (UI) — static hint under the audio uploader/TTS panel.
# 2. ``/cost-preview`` (backend) — refuses oversized audio with 422.
# 3. Pipeline guard before ``_step_video_synth`` — last-line defence for
#    third-party callers / hot-reload corner cases.
#
# Sources (October 2025 docs):
# - wan2.2-s2v: audio < 20s, < 15MB, wav/mp3
#     https://help.aliyun.com/zh/model-studio/wan-s2v-api
# - videoretalk: 2s ≤ audio,video ≤ 120s
#     https://help.aliyun.com/zh/model-studio/videoretalk-api
# - wan2.2-animate-* (move / mix): no audio input — driven by video only.

AUDIO_LIMIT_HINT_KEY: dict[str, str] = {
    # mode → "<min>-<max>" seconds, used by the UI for the inline hint
    "photo_speak": "0.5-19.5",
    "video_relip": "2-120",
    "avatar_compose": "0.5-19.5",
}


@dataclass(frozen=True)
class AudioLimit:
    min_sec: float
    max_sec: float
    model_label: str  # shown in error message for clarity

    def violates(self, dur: float) -> str | None:
        """Return a Chinese error message if ``dur`` is out of range, else None."""
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


# Conservative caps: DashScope says <20s for s2v, so we treat 19.5s as the
# practical ceiling (round-trip latency + sample-rate quantisation can push
# a 19.9s upload over the line). Min 0.5s avoids false-positives for very
# short sentences.
AUDIO_LIMITS: dict[str, AudioLimit] = {
    "photo_speak": AudioLimit(0.5, 19.5, "wan2.2-s2v"),
    "avatar_compose": AudioLimit(0.5, 19.5, "wan2.2-s2v"),
    "video_relip": AudioLimit(2.0, 120.0, "videoretalk"),
}


def check_audio_duration(mode: str, duration_sec: float | None) -> str | None:
    """Validate ``duration_sec`` against the per-mode cap.

    Returns a user-facing Chinese error message (suitable for HTTP 422 /
    inline UI banner) or ``None`` if the duration is acceptable / not
    applicable for this mode.
    """
    limit = AUDIO_LIMITS.get(mode)
    if limit is None or duration_sec is None:
        return None
    return limit.violates(float(duration_sec))


# ─── Voice catalog (12 cosyvoice-v2 system voices) ─────────────────────────


@dataclass(frozen=True)
class VoiceSpec:
    """One TTS voice option, system-bundled or user-cloned."""

    id: str  # cosyvoice voice_id passed to the API
    label_zh: str
    label_en: str
    gender: Literal["female", "male", "neutral"]
    style_zh: str
    style_en: str
    is_system: bool = True

    def to_dict(self) -> dict[str, object]:
        # ``dashscope_voice_id`` is the value passed to cosyvoice-v2 SDK
        # ``model="cosyvoice-v2", voice=…``. The DashScope public catalog
        # lists every system timbre with a ``_v2`` suffix (e.g.
        # ``longxiaochun_v2``); we keep ``id`` as the bare key so existing
        # records remain stable, then expose the v2-suffixed string as the
        # canonical identifier the UI sends back. ``label`` is a flat alias
        # the React layer can render without needing the locale dict.
        return {
            "id": self.id,
            "label": self.label_zh,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "gender": self.gender,
            "style": self.style_zh,
            "style_zh": self.style_zh,
            "style_en": self.style_en,
            "is_system": self.is_system,
            "dashscope_voice_id": f"{self.id}_v2",
            "language": "zh",
        }


SYSTEM_VOICES: tuple[VoiceSpec, ...] = (
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

VOICES_BY_ID: dict[str, VoiceSpec] = {v.id: v for v in SYSTEM_VOICES}


# ─── Resolution / aspect / duration option lists ─────────────────────────

RESOLUTIONS: tuple[str, ...] = ("480P", "720P")
ASPECTS: tuple[str, ...] = ("1:1", "9:16", "16:9", "3:4", "4:3")
DURATIONS_S2V: tuple[int, ...] = tuple(range(3, 16))  # 3..15 s, only used when no audio
ANIMATE_MIX_MODES: tuple[str, ...] = ("wan-std", "wan-pro")
ANIMATE_MOVE_MODES: tuple[str, ...] = ("wan-std", "wan-pro")
DEFAULT_COST_THRESHOLD_CNY: float = 5.00  # exceedance triggers user confirmation


# ─── Price table (officially documented DashScope unit prices, CNY) ───────

# Pricing reference: DashScope console / pricing page (CN site).
# Tests freeze a copy so a remote price drift never silently changes UI numbers.
PRICE_TABLE: dict[str, dict[str, float]] = {
    "wan2.2-s2v-detect": {"per_image": 0.004},
    "wan2.2-s2v": {"480P_per_sec": 0.50, "720P_per_sec": 0.90},
    "videoretalk": {"per_sec": 0.30},
    "wan2.2-animate-mix": {"wan-std_per_sec": 0.60, "wan-pro_per_sec": 1.20},
    "wan2.2-animate-move": {"wan-std_per_sec": 0.40, "wan-pro_per_sec": 0.60},
    "wan2.5-i2i-preview": {"per_image": 0.20},
    "wan2.7-image": {"per_image": 0.20},
    "wan2.7-image-pro": {"per_image": 0.50},
    "qwen-vl-max": {"per_1k_input_token": 0.02, "per_1k_output_token": 0.06},
    "cosyvoice-v2": {"per_10k_chars": 0.20},
}


# ─── Cost preview types ──────────────────────────────────────────────────


class CostItem(TypedDict):
    """One itemised line in the cost breakdown."""

    name: str  # e.g. "wan2.2-s2v 720P"
    units: float  # e.g. 5.0 (seconds, or 1 for per-image)
    unit_label: str  # e.g. "秒" / "张" / "万字"
    unit_price: float  # CNY per unit
    subtotal: float  # CNY (units * unit_price, rounded 2 dp)
    note: str  # short explanation, optional


class CostPreview(TypedDict):
    """Result of ``estimate_cost`` — fed to /cost-preview and the UI modal."""

    total: float  # CNY, rounded 2 dp
    currency: str  # always "CNY"
    items: list[CostItem]
    exceeds_threshold: bool
    threshold: float
    formatted_total: str  # "¥0.42" — the ONLY presentation format (no milk-tea)


def _round(x: float | Decimal, places: int = 2) -> float:
    """Round a value to ``places`` decimal places using HALF_UP."""
    quant = Decimal(10) ** -places
    return float(Decimal(str(x)).quantize(quant, rounding=ROUND_HALF_UP))


def _fmt(x: float | Decimal) -> str:
    """Format a CNY amount as ``¥0.42`` — explicit, no translation."""
    return f"¥{_round(x):.2f}"


def estimate_cost(
    mode: str,
    params: dict[str, object],
    *,
    audio_duration_sec: float | None = None,
    text_chars: int | None = None,
    threshold: float = DEFAULT_COST_THRESHOLD_CNY,
) -> CostPreview:
    """Estimate the CNY cost of one job, broken down by chargeable item.

    Args:
        mode: One of the 4 ``ModeId`` values.
        params: Mode-specific keys; see per-branch handling below.
        audio_duration_sec: When TTS already ran (or audio uploaded), use the
            real audio length so s2v / videoretalk get the canonical duration
            (Pixelle P1 — TTS drives video length).
        text_chars: When TTS hasn't run yet but we know the script length,
            use it for a TTS subtotal.
        threshold: CNY ceiling above which the UI must show
            ``<CostExceedModal>`` and require explicit confirmation.

    Returns:
        A ``CostPreview`` dict that is JSON-serialisable end-to-end.
    """
    items: list[CostItem] = []

    if mode == "photo_speak":
        items.append(_item_face_detect())
        items.append(_item_s2v(params, audio_duration_sec))
        if text_chars:
            items.append(_item_tts(text_chars))

    elif mode == "video_relip":
        sec = float(audio_duration_sec or params.get("video_duration_sec") or 5.0)
        items.append(
            CostItem(
                name="videoretalk",
                units=sec,
                unit_label="秒",
                unit_price=PRICE_TABLE["videoretalk"]["per_sec"],
                subtotal=_round(sec * PRICE_TABLE["videoretalk"]["per_sec"]),
                note="按音频时长计费" if audio_duration_sec else "按视频时长估算",
            )
        )
        if text_chars:
            items.append(_item_tts(text_chars))

    elif mode == "video_reface":
        sec = float(params.get("video_duration_sec") or 5.0)
        is_pro = bool(params.get("mode_pro"))
        per_sec = PRICE_TABLE["wan2.2-animate-mix"][
            "wan-pro_per_sec" if is_pro else "wan-std_per_sec"
        ]
        items.append(
            CostItem(
                name=f"wan2.2-animate-mix ({'wan-pro' if is_pro else 'wan-std'})",
                units=sec,
                unit_label="秒",
                unit_price=per_sec,
                subtotal=_round(sec * per_sec),
                note="按参考视频时长计费",
            )
        )
        if text_chars:
            items.append(_item_tts(text_chars))

    elif mode == "avatar_compose":
        n_ref = max(1, min(3, int(params.get("ref_image_count") or 1)))
        items.append(
            CostItem(
                name="wan2.5-i2i-preview",
                units=float(n_ref),
                unit_label="次",
                unit_price=PRICE_TABLE["wan2.5-i2i-preview"]["per_image"],
                subtotal=_round(n_ref * PRICE_TABLE["wan2.5-i2i-preview"]["per_image"]),
                note=f"{n_ref} 张参考图融合",
            )
        )
        if params.get("use_qwen_vl"):
            tokens = int(params.get("qwen_token_estimate") or 600)
            items.append(_item_qwen(tokens))
        items.append(_item_face_detect())
        items.append(_item_s2v(params, audio_duration_sec))
        if text_chars:
            items.append(_item_tts(text_chars))

    elif mode == "pose_drive":
        sec = float(params.get("video_duration_sec") or 5.0)
        is_pro = bool(params.get("mode_pro"))
        per_sec = PRICE_TABLE["wan2.2-animate-move"][
            "wan-pro_per_sec" if is_pro else "wan-std_per_sec"
        ]
        items.append(
            CostItem(
                name=f"wan2.2-animate-move ({'wan-pro' if is_pro else 'wan-std'})",
                units=sec,
                unit_label="秒",
                unit_price=per_sec,
                subtotal=_round(sec * per_sec),
                note="按参考视频时长计费",
            )
        )

    else:
        raise ValueError(f"unknown mode: {mode!r}")

    # Total is computed from raw (unrounded) item amounts so that sub-cent
    # items (e.g. 0.004 detect, 0.0024 TTS) still contribute correctly to
    # the rounded display total.
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


def _item_face_detect() -> CostItem:
    price = PRICE_TABLE["wan2.2-s2v-detect"]["per_image"]
    return CostItem(
        name="wan2.2-s2v-detect",
        units=1.0,
        unit_label="张",
        unit_price=price,
        subtotal=_round(price, places=4),  # keep sub-cent precision
        note="人脸预检（必要）",
    )


def _item_s2v(params: dict[str, object], audio_duration_sec: float | None) -> CostItem:
    resolution = str(params.get("resolution") or "480P").upper()
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


def _item_tts(text_chars: int) -> CostItem:
    units = max(1, text_chars) / 10000.0
    per = PRICE_TABLE["cosyvoice-v2"]["per_10k_chars"]
    return CostItem(
        name="cosyvoice-v2 TTS",
        units=round(units, 4),
        unit_label="万字",
        unit_price=per,
        subtotal=_round(units * per, places=4),
        note=f"约 {text_chars} 字",
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


# ─── Error hints (Pixelle C2 — bilingual, actionable, 9 kinds) ────────────

# Keys here MUST mirror the constants exported by
# ``avatar_studio_inline.vendor_client`` (ERROR_KIND_*) plus two
# avatar-studio-only kinds (``quota`` / ``dependency``) the vendor base
# does not classify. Anything else is mapped to ``unknown``.


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
            "DashScope 异步任务并发上限为 1，请等待当前任务完成",
            "或联系阿里云开通更高配额",
        ],
        "hints_en": [
            "DashScope concurrent limit = 1; wait for current task",
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
    """Look up bilingual hints for an ``error_kind``; falls back to ``unknown``."""
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
    animate_mix_modes: list[str] = field(default_factory=list)
    cost_threshold: float = DEFAULT_COST_THRESHOLD_CNY


def build_catalog() -> CatalogPayload:
    """Materialise the static UI catalog (modes + system voices + option lists)."""
    return CatalogPayload(
        modes=[m.to_dict() for m in MODES],
        voices=[v.to_dict() for v in SYSTEM_VOICES],
        resolutions=list(RESOLUTIONS),
        aspects=list(ASPECTS),
        animate_mix_modes=list(ANIMATE_MIX_MODES),
        cost_threshold=DEFAULT_COST_THRESHOLD_CNY,
    )

