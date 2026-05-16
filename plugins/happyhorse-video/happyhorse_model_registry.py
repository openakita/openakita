"""Model registry — single source of truth for the (mode, model_id) catalog.

This module powers three independent consumers, all of which MUST agree
on the same physical truth — hard-coding ``if model_id == ...`` in the
client / pipeline / UI is the original sin we are designing out:

1. ``happyhorse_dashscope_client`` uses ``endpoint_family`` /
   ``protocol_version`` / ``forbidden_params`` / ``size_format`` to
   build the right HTTP request for each model without any branching
   on string equality.
2. The Create form on the React UI populates the per-mode model
   ``<select>`` from ``models_for(mode)``.
3. The Settings tab ``default_model_<mode>`` selectors fall back to
   ``default_model(mode).model_id`` for any mode missing in stored
   settings.

Adding a new model is a one-liner here — no client / pipeline / UI
edits needed unless the model has a *new* protocol or *new* parameters.

Latest models on Aliyun Bailian (DashScope) as of 2026-Q2:

- HappyHorse 1.0 family — native audio-video sync, 7-language lip-sync,
  ``resolution: "720P" | "1080P"``. **Rejects** ``with_audio`` /
  ``size`` / ``quality`` / ``fps`` / ``audio`` per official docs.
- Wan 2.7 i2v — multimodal task type (first-frame /
  first-and-last-frame / video-continuation). Uses new async protocol.
- Wan 2.6 t2v / i2v / r2v (and ``-flash`` variants) — use legacy async
  protocol with ``size: "1280*720"`` (W*H, *star*-separated, lowercase).
- wan2.2-s2v / wan2.2-s2v-detect — speech-to-video photo speaker.
- videoretalk — lip-sync replacement on existing video.
- wan2.2-animate-mix / wan2.2-animate-move — video reface / pose drive.
- wan2.7-image / wan2.7-image-pro / wan2.5-i2i-preview — image
  generation supporting avatar_compose pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

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

# ``endpoint_family`` decides which DashScope HTTP path the client posts
# to. ``protocol_version`` decides whether to use the *new async*
# (``X-DashScope-Async: enable`` + ``output.results[*].url``) or *legacy
# async* (``output.video_url``) response shape. ``size_format`` decides
# how to render width/height (the new protocol uses ``resolution: "720P"``
# while Wan 2.6 legacy uses ``size: "1280*720"``).
EndpointFamily = Literal[
    "video_synthesis",   # HappyHorse + Wan 2.6/2.7 video gen
    "image2video",       # wan2.2-s2v / wan2.2-s2v-detect
    "videoretalk",
    "animate",           # wan2.2-animate-mix / wan2.2-animate-move
    "image_synthesis",   # wan2.7-image / wan2.5-i2i-preview
    "cosyvoice",         # cosyvoice-v2 TTS (SDK-only)
    "qwen_vl",           # qwen-vl-max prompt assist
]
ProtocolVersion = Literal["new_async", "legacy_async", "sdk"]
SizeFormat = Literal["resolution_p", "size_star", "size_x"]


@dataclass(frozen=True)
class ModelEntry:
    """One selectable model for a given mode.

    Fields are immutable on purpose: the registry is a constant the
    client / UI / pipeline copy from, never modify.
    """

    mode: ModeId
    model_id: str
    label_zh: str
    label_en: str
    endpoint_family: EndpointFamily
    protocol_version: ProtocolVersion
    size_format: SizeFormat
    cost_note: str
    # Resolutions accepted by this specific model. Order matters: the
    # first entry is the "recommended" default the UI pre-selects.
    resolutions: tuple[str, ...] = ("720P",)
    # Aspect ratios accepted by this model.
    aspects: tuple[str, ...] = ("16:9", "9:16", "1:1", "4:3", "3:4")
    # Allowed video durations in seconds (HappyHorse: 3-15; Wan 2.6: 5/10/15).
    duration_range: tuple[int, int] = (3, 15)
    # Optional sub-task selector (only ``wan2.7-i2v`` uses this so far —
    # ``first-frame`` / ``first-and-last-frame`` / ``video-continuation``).
    task_types: tuple[str, ...] = ()
    # Parameter keys the model REJECTS — sent verbatim to the client
    # as the validation deny-list. HappyHorse 1.0 rejects with_audio /
    # size / quality / fps / audio per official docs.
    forbidden_params: tuple[str, ...] = ()
    # When True, this model expects an OSS-fetchable signed URL for any
    # input image / video / audio.
    requires_oss: bool = True
    # Whether this model emits audio-synced output natively (no TTS step
    # needed in the pipeline). HappyHorse 1.0 family is True; everything
    # else is False (and pipeline runs cosyvoice / edge-tts first).
    native_audio_sync: bool = False
    is_default: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "model_id": self.model_id,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "endpoint_family": self.endpoint_family,
            "protocol_version": self.protocol_version,
            "size_format": self.size_format,
            "cost_note": self.cost_note,
            "resolutions": list(self.resolutions),
            "aspects": list(self.aspects),
            "duration_range": list(self.duration_range),
            "task_types": list(self.task_types),
            "forbidden_params": list(self.forbidden_params),
            "requires_oss": self.requires_oss,
            "native_audio_sync": self.native_audio_sync,
            "is_default": self.is_default,
        }


# ── HappyHorse 1.0 forbidden-params constant (DRY across 4 entries) ───

_HAPPYHORSE_FORBIDDEN: tuple[str, ...] = (
    "with_audio",
    "size",
    "quality",
    "fps",
    "audio",
)

_HAPPYHORSE_RES = ("720P", "1080P")
_HAPPYHORSE_DUR: tuple[int, int] = (3, 15)


# ── Wan 2.6 / 2.7 ──────────────────────────────────────────────────────

_WAN_LEGACY_RES = ("720P", "1080P")
_WAN_NEW_RES = ("720P", "1080P")


# ── Registry ──────────────────────────────────────────────────────────

REGISTRY: tuple[ModelEntry, ...] = (
    # ── t2v ────────────────────────────────────────────────────────────
    ModelEntry(
        mode="t2v",
        model_id="happyhorse-1.0-t2v",
        label_zh="HappyHorse 1.0 文生视频",
        label_en="HappyHorse 1.0 T2V",
        endpoint_family="video_synthesis",
        protocol_version="new_async",
        size_format="resolution_p",
        cost_note="720P 0.90 元/秒；1080P 1.60 元/秒",
        resolutions=_HAPPYHORSE_RES,
        duration_range=_HAPPYHORSE_DUR,
        forbidden_params=_HAPPYHORSE_FORBIDDEN,
        native_audio_sync=True,
        is_default=True,
    ),
    ModelEntry(
        mode="t2v",
        model_id="wan2.6-t2v",
        label_zh="万相 2.6 文生视频",
        label_en="Wan 2.6 T2V",
        endpoint_family="video_synthesis",
        protocol_version="legacy_async",
        size_format="size_star",
        cost_note="按官网定价",
        resolutions=_WAN_LEGACY_RES,
        duration_range=(5, 15),
    ),
    # ── i2v (first frame) ──────────────────────────────────────────────
    ModelEntry(
        mode="i2v",
        model_id="happyhorse-1.0-i2v",
        label_zh="HappyHorse 1.0 图生视频",
        label_en="HappyHorse 1.0 I2V",
        endpoint_family="video_synthesis",
        protocol_version="new_async",
        size_format="resolution_p",
        cost_note="720P 0.90 元/秒；1080P 1.60 元/秒",
        resolutions=_HAPPYHORSE_RES,
        duration_range=_HAPPYHORSE_DUR,
        forbidden_params=_HAPPYHORSE_FORBIDDEN,
        native_audio_sync=True,
        is_default=True,
    ),
    ModelEntry(
        mode="i2v",
        model_id="wan2.6-i2v",
        label_zh="万相 2.6 图生视频",
        label_en="Wan 2.6 I2V",
        endpoint_family="video_synthesis",
        protocol_version="legacy_async",
        size_format="size_star",
        cost_note="按官网定价",
        resolutions=_WAN_LEGACY_RES,
        duration_range=(5, 15),
    ),
    ModelEntry(
        mode="i2v",
        model_id="wan2.6-i2v-flash",
        label_zh="万相 2.6 图生快速版",
        label_en="Wan 2.6 I2V Flash",
        endpoint_family="video_synthesis",
        protocol_version="legacy_async",
        size_format="size_star",
        cost_note="比标准版更便宜",
        resolutions=_WAN_LEGACY_RES,
        duration_range=(5, 15),
    ),
    ModelEntry(
        mode="i2v",
        model_id="wan2.7-i2v",
        label_zh="万相 2.7 多模态图生视频",
        label_en="Wan 2.7 i2v multimodal",
        endpoint_family="video_synthesis",
        protocol_version="new_async",
        size_format="resolution_p",
        cost_note="支持首帧 / 首尾帧 / 视频续写",
        resolutions=_WAN_NEW_RES,
        duration_range=(5, 15),
        task_types=(
            "first-frame",
            "first-and-last-frame",
            "video-continuation",
        ),
    ),
    # ── i2v_end (first + last frame) ───────────────────────────────────
    ModelEntry(
        mode="i2v_end",
        model_id="wan2.7-i2v",
        label_zh="万相 2.7 首尾帧生视频",
        label_en="Wan 2.7 first-and-last-frame",
        endpoint_family="video_synthesis",
        protocol_version="new_async",
        size_format="resolution_p",
        cost_note="使用 wan2.7-i2v 的 first-and-last-frame task type",
        resolutions=_WAN_NEW_RES,
        duration_range=(5, 15),
        task_types=("first-and-last-frame",),
        is_default=True,
    ),
    # ── video_extend (continuation) ────────────────────────────────────
    ModelEntry(
        mode="video_extend",
        model_id="wan2.7-i2v",
        label_zh="万相 2.7 视频续写",
        label_en="Wan 2.7 video continuation",
        endpoint_family="video_synthesis",
        protocol_version="new_async",
        size_format="resolution_p",
        cost_note="使用 wan2.7-i2v 的 video-continuation task type",
        resolutions=_WAN_NEW_RES,
        duration_range=(5, 15),
        task_types=("video-continuation",),
        is_default=True,
    ),
    # ── r2v (reference-to-video, multi-character) ──────────────────────
    ModelEntry(
        mode="r2v",
        model_id="happyhorse-1.0-r2v",
        label_zh="HappyHorse 1.0 参考生视频",
        label_en="HappyHorse 1.0 R2V",
        endpoint_family="video_synthesis",
        protocol_version="new_async",
        size_format="resolution_p",
        cost_note="720P 0.90 元/秒；1080P 1.60 元/秒",
        resolutions=_HAPPYHORSE_RES,
        duration_range=_HAPPYHORSE_DUR,
        forbidden_params=_HAPPYHORSE_FORBIDDEN,
        native_audio_sync=True,
        is_default=True,
    ),
    ModelEntry(
        mode="r2v",
        model_id="wan2.6-r2v",
        label_zh="万相 2.6 参考生视频",
        label_en="Wan 2.6 R2V",
        endpoint_family="video_synthesis",
        protocol_version="legacy_async",
        size_format="size_star",
        cost_note="多角色互动，按官网定价",
        resolutions=_WAN_LEGACY_RES,
        duration_range=(5, 15),
    ),
    ModelEntry(
        mode="r2v",
        model_id="wan2.6-r2v-flash",
        label_zh="万相 2.6 参考生视频快速版",
        label_en="Wan 2.6 R2V Flash",
        endpoint_family="video_synthesis",
        protocol_version="legacy_async",
        size_format="size_star",
        cost_note="比标准版更便宜",
        resolutions=_WAN_LEGACY_RES,
        duration_range=(5, 15),
    ),
    # ── video_edit ─────────────────────────────────────────────────────
    ModelEntry(
        mode="video_edit",
        model_id="happyhorse-1.0-video-edit",
        label_zh="HappyHorse 1.0 视频编辑",
        label_en="HappyHorse 1.0 Video Edit",
        endpoint_family="video_synthesis",
        protocol_version="new_async",
        size_format="resolution_p",
        cost_note="按输入与输出视频时长计费",
        resolutions=_HAPPYHORSE_RES,
        duration_range=_HAPPYHORSE_DUR,
        forbidden_params=_HAPPYHORSE_FORBIDDEN,
        native_audio_sync=True,
        is_default=True,
    ),
    # ── photo_speak (digital human) ────────────────────────────────────
    ModelEntry(
        mode="photo_speak",
        model_id="wan2.2-s2v",
        label_zh="万相 S2V 照片说话",
        label_en="Wan S2V",
        endpoint_family="image2video",
        protocol_version="legacy_async",
        size_format="resolution_p",
        cost_note="480P 0.50 元/秒；720P 0.90 元/秒",
        resolutions=("480P", "720P"),
        duration_range=(3, 15),
        is_default=True,
    ),
    # ── video_relip ────────────────────────────────────────────────────
    ModelEntry(
        mode="video_relip",
        model_id="videoretalk",
        label_zh="VideoReTalk 视频换嘴",
        label_en="VideoReTalk",
        endpoint_family="videoretalk",
        protocol_version="legacy_async",
        size_format="size_x",
        cost_note="0.30 元/秒（按音频时长）",
        resolutions=("720P",),
        duration_range=(2, 120),
        is_default=True,
    ),
    # ── video_reface ───────────────────────────────────────────────────
    ModelEntry(
        mode="video_reface",
        model_id="wan2.2-animate-mix",
        label_zh="万相 Animate Mix 视频换人",
        label_en="Wan Animate Mix",
        endpoint_family="animate",
        protocol_version="legacy_async",
        size_format="resolution_p",
        cost_note="wan-std 0.60 元/秒；wan-pro 1.20 元/秒",
        resolutions=("480P", "720P"),
        duration_range=(3, 30),
        task_types=("wan-std", "wan-pro"),
        is_default=True,
    ),
    # ── pose_drive ─────────────────────────────────────────────────────
    ModelEntry(
        mode="pose_drive",
        model_id="wan2.2-animate-move",
        label_zh="万相 Animate Move 图生动作",
        label_en="Wan Animate Move",
        endpoint_family="animate",
        protocol_version="legacy_async",
        size_format="resolution_p",
        cost_note="wan-std 0.40 元/秒；wan-pro 0.60 元/秒",
        resolutions=("480P", "720P"),
        duration_range=(3, 30),
        task_types=("wan-std", "wan-pro"),
        is_default=True,
    ),
    # ── avatar_compose (image edit → s2v) ──────────────────────────────
    ModelEntry(
        mode="avatar_compose",
        model_id="wan2.7-image",
        label_zh="万相 2.7 Image (融合)",
        label_en="Wan 2.7 Image",
        endpoint_family="image_synthesis",
        protocol_version="legacy_async",
        size_format="resolution_p",
        cost_note="0.20 元/张 + 后续 s2v",
        resolutions=("720P", "1080P"),
        duration_range=(3, 15),
        is_default=True,
    ),
    ModelEntry(
        mode="avatar_compose",
        model_id="wan2.7-image-pro",
        label_zh="万相 2.7 Image Pro",
        label_en="Wan 2.7 Image Pro",
        endpoint_family="image_synthesis",
        protocol_version="legacy_async",
        size_format="resolution_p",
        cost_note="0.50 元/张 + 后续 s2v",
        resolutions=("720P", "1080P"),
        duration_range=(3, 15),
    ),
    ModelEntry(
        mode="avatar_compose",
        model_id="wan2.5-i2i-preview",
        label_zh="万相 I2I Preview (旧版)",
        label_en="Wan I2I Preview",
        endpoint_family="image_synthesis",
        protocol_version="legacy_async",
        size_format="resolution_p",
        cost_note="0.20 元/张 + 后续 s2v",
        resolutions=("720P",),
        duration_range=(3, 15),
    ),
    # ── long_video (storyboard pipeline; reuses i2v base model) ────────
    ModelEntry(
        mode="long_video",
        model_id="happyhorse-1.0-i2v",
        label_zh="长视频拼接（HappyHorse 1.0 i2v 基础段）",
        label_en="Long video chain (HappyHorse 1.0 i2v base)",
        endpoint_family="video_synthesis",
        protocol_version="new_async",
        size_format="resolution_p",
        cost_note="按每段 i2v 计费",
        resolutions=_HAPPYHORSE_RES,
        duration_range=_HAPPYHORSE_DUR,
        forbidden_params=_HAPPYHORSE_FORBIDDEN,
        native_audio_sync=True,
        is_default=True,
    ),
    ModelEntry(
        mode="long_video",
        model_id="wan2.6-i2v",
        label_zh="长视频拼接（万相 2.6 i2v 基础段）",
        label_en="Long video chain (Wan 2.6 i2v base)",
        endpoint_family="video_synthesis",
        protocol_version="legacy_async",
        size_format="size_star",
        cost_note="按每段 i2v 计费",
        resolutions=_WAN_LEGACY_RES,
        duration_range=(5, 15),
    ),
)


# ── Public lookup helpers ─────────────────────────────────────────────


REGISTRY_BY_KEY: dict[tuple[str, str], ModelEntry] = {
    (e.mode, e.model_id): e for e in REGISTRY
}
REGISTRY_BY_MODEL_ID: dict[str, ModelEntry] = {e.model_id: e for e in REGISTRY}


def models_for(mode: str) -> list[ModelEntry]:
    """Return candidate models for the given mode (default first)."""
    out = [e for e in REGISTRY if e.mode == mode]
    out.sort(key=lambda e: (0 if e.is_default else 1, e.model_id))
    return out


def default_model(mode: str) -> ModelEntry | None:
    """Return the registry default for a mode, or None."""
    for e in REGISTRY:
        if e.mode == mode and e.is_default:
            return e
    candidates = models_for(mode)
    return candidates[0] if candidates else None


def lookup(mode: str, model_id: str) -> ModelEntry | None:
    """Look up an entry by ``(mode, model_id)``. Returns None if missing."""
    entry = REGISTRY_BY_KEY.get((mode, model_id))
    if entry is not None:
        return entry
    # Fallback: a `model_id` that exists under another mode (e.g. user
    # passed `happyhorse-1.0-i2v` while the request says `mode=long_video`
    # — we want the long_video registry entry, not the i2v one).
    return None


def by_model_id(model_id: str) -> ModelEntry | None:
    """Look up the *canonical* entry for a given model_id (mode-agnostic).

    Used by the client when the pipeline only knows the model_id. If the
    same model_id appears under multiple modes (e.g. ``wan2.7-i2v`` is
    registered for ``i2v`` / ``i2v_end`` / ``video_extend``), the one
    with the lowest mode order is returned — endpoint_family /
    protocol_version / size_format / forbidden_params are identical
    across modes for the same model_id by construction.
    """
    return REGISTRY_BY_MODEL_ID.get(model_id)


# ── Static catalogues used elsewhere ──────────────────────────────────

ALL_MODES: tuple[str, ...] = (
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
)


@dataclass(frozen=True)
class RegistryPayload:
    """Snapshot returned by ``GET /catalog`` — registry + per-mode default."""

    models: list[dict[str, object]] = field(default_factory=list)
    defaults: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls) -> RegistryPayload:
        defaults: dict[str, str] = {}
        for m in ALL_MODES:
            d = default_model(m)
            if d is not None:
                defaults[m] = d.model_id
        return cls(
            models=[e.to_dict() for e in REGISTRY],
            defaults=defaults,
        )
