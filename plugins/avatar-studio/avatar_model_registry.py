"""Model registry — 5 modes x N candidate models per backend.

Each entry describes one selectable model for a given (mode, backend)
combination.  The registry is queried by the UI (via ``GET /catalog``)
to populate backend-aware model selectors and by the pipeline to
resolve which DashScope endpoint or workflow to use.

Design:
- ``REGISTRY`` is the flat list; helpers filter by mode/backend.
- DashScope entries carry ``model_id`` (the DashScope model name).
- RunningHub / ComfyUI entries carry empty ``model_id`` — the actual
  workflow_id comes from user settings or ``recommended.json``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BackendId = Literal["dashscope", "runninghub", "comfyui_local"]
ModeId = Literal["photo_speak", "video_relip", "video_reface", "avatar_compose", "pose_drive"]


@dataclass(frozen=True)
class ModelEntry:
    mode: ModeId
    backend: BackendId
    model_id: str
    label_zh: str
    label_en: str
    cost_note: str
    is_default: bool = False
    requires_oss: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "backend": self.backend,
            "model_id": self.model_id,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "cost_note": self.cost_note,
            "is_default": self.is_default,
            "requires_oss": self.requires_oss,
        }


REGISTRY: tuple[ModelEntry, ...] = (
    # ── photo_speak ──
    ModelEntry("photo_speak", "dashscope", "wan2.2-s2v",
               "万相 S2V", "Wan S2V", "0.50/0.90元/秒", is_default=True, requires_oss=True),
    ModelEntry("photo_speak", "dashscope", "emo-v1",
               "悦动人像 (旧)", "Emo V1", "0.08/0.16元/秒", requires_oss=True),
    ModelEntry("photo_speak", "dashscope", "liveportrait",
               "灵动人像 (旧)", "LivePortrait", "更低", requires_oss=True),
    ModelEntry("photo_speak", "runninghub", "",
               "RunningHub Workflow", "RunningHub Workflow", "按 RH 实际用量"),
    ModelEntry("photo_speak", "comfyui_local", "",
               "本地 Workflow", "Local Workflow", "免费"),
    # ── video_relip ──
    ModelEntry("video_relip", "dashscope", "videoretalk",
               "VideoReTalk", "VideoReTalk", "0.30元/秒", is_default=True, requires_oss=True),
    ModelEntry("video_relip", "runninghub", "",
               "RunningHub Workflow", "RunningHub Workflow", "按 RH 实际用量"),
    ModelEntry("video_relip", "comfyui_local", "",
               "本地 Workflow", "Local Workflow", "免费"),
    # ── video_reface ──
    ModelEntry("video_reface", "dashscope", "wan2.2-animate-mix",
               "万相 Animate Mix", "Wan Animate Mix", "std 0.60/pro 1.20元/秒",
               is_default=True, requires_oss=True),
    ModelEntry("video_reface", "runninghub", "",
               "RunningHub Workflow", "RunningHub Workflow", "按 RH 实际用量"),
    ModelEntry("video_reface", "comfyui_local", "",
               "本地 Workflow", "Local Workflow", "免费"),
    # ── avatar_compose ──
    ModelEntry("avatar_compose", "dashscope", "wan2.7-image",
               "万相 2.7 Image", "Wan 2.7 Image", "0.20元/张 + s2v",
               is_default=True, requires_oss=True),
    ModelEntry("avatar_compose", "dashscope", "wan2.7-image-pro",
               "万相 2.7 Image Pro", "Wan 2.7 Image Pro", "0.50元/张 + s2v",
               requires_oss=True),
    ModelEntry("avatar_compose", "dashscope", "wan2.5-i2i-preview",
               "万相 I2I Preview (旧)", "Wan I2I Preview", "0.20元/张 + s2v",
               requires_oss=True),
    ModelEntry("avatar_compose", "runninghub", "",
               "RunningHub Workflow", "RunningHub Workflow", "按 RH 实际用量"),
    ModelEntry("avatar_compose", "comfyui_local", "",
               "本地 Workflow", "Local Workflow", "免费"),
    # ── pose_drive ──
    ModelEntry("pose_drive", "dashscope", "wan2.2-animate-move",
               "万相 Animate Move", "Wan Animate Move", "std 0.40/pro 0.60元/秒",
               is_default=True, requires_oss=True),
    ModelEntry("pose_drive", "runninghub", "",
               "RunningHub Workflow", "RunningHub Workflow", "按 RH 实际用量"),
    ModelEntry("pose_drive", "comfyui_local", "",
               "本地 Workflow", "Local Workflow", "免费"),
)


def models_for(mode: str, backend: str) -> list[ModelEntry]:
    """Return candidate models for the given mode + backend."""
    return [e for e in REGISTRY if e.mode == mode and e.backend == backend]


def default_model(mode: str, backend: str) -> ModelEntry | None:
    """Return the default model for a mode + backend, or ``None``."""
    for e in REGISTRY:
        if e.mode == mode and e.backend == backend and e.is_default:
            return e
    candidates = models_for(mode, backend)
    return candidates[0] if candidates else None


ALL_MODES = ("photo_speak", "video_relip", "video_reface", "avatar_compose", "pose_drive")
ALL_BACKENDS = ("dashscope", "runninghub", "comfyui_local")
