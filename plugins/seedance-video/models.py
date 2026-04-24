"""Seedance model definitions, capability matrix, and resolution pixel tables."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SeedanceModel:
    id: str
    name: str
    model_id: str
    modes: list[str]
    duration_range: tuple[int, int]
    resolutions: list[str]
    supports_draft: bool = False
    supports_audio: bool = False
    supports_web_search: bool = False
    supports_camera_fixed: bool = False
    rpm: int = 600
    concurrency: int = 10
    note: str = ""


SEEDANCE_MODELS: list[SeedanceModel] = [
    SeedanceModel(
        id="2.0",
        name="Seedance 2.0",
        model_id="doubao-seedance-2-0-260128",
        modes=["t2v", "i2v", "i2v_end", "multimodal", "edit", "extend"],
        duration_range=(4, 15),
        resolutions=["480p", "720p"],
        supports_audio=True,
        supports_web_search=True,
        rpm=600, concurrency=10,
    ),
    SeedanceModel(
        id="2.0-fast",
        name="Seedance 2.0 Fast",
        model_id="doubao-seedance-2-0-fast-260128",
        modes=["t2v", "i2v", "i2v_end", "multimodal", "edit", "extend"],
        duration_range=(4, 15),
        resolutions=["480p", "720p"],
        supports_audio=True,
        supports_web_search=True,
        rpm=600, concurrency=10,
        note="Faster and cheaper than 2.0",
    ),
    SeedanceModel(
        id="1.5-pro",
        name="Seedance 1.5 Pro",
        model_id="doubao-seedance-1-5-pro-251215",
        modes=["t2v", "i2v", "i2v_end"],
        duration_range=(4, 12),
        resolutions=["480p", "720p", "1080p"],
        supports_draft=True,
        supports_audio=True,
        supports_camera_fixed=True,
        rpm=300, concurrency=5,
    ),
    SeedanceModel(
        id="1.0-pro",
        name="Seedance 1.0 Pro",
        model_id="doubao-seedance-1-0-pro-250528",
        modes=["t2v", "i2v", "i2v_end"],
        duration_range=(2, 12),
        resolutions=["480p", "720p", "1080p"],
        supports_camera_fixed=True,
        rpm=300, concurrency=5,
    ),
    SeedanceModel(
        id="1.0-pro-fast",
        name="Seedance 1.0 Pro Fast",
        model_id="doubao-seedance-1-0-pro-fast-251015",
        modes=["t2v", "i2v", "i2v_end"],
        duration_range=(2, 12),
        resolutions=["480p", "720p", "1080p"],
        supports_camera_fixed=True,
        rpm=300, concurrency=5,
        note="Faster than 1.0 Pro",
    ),
    SeedanceModel(
        id="1.0-lite-t2v",
        name="Seedance 1.0 Lite T2V",
        model_id="doubao-seedance-1-0-lite-t2v-250428",
        modes=["t2v"],
        duration_range=(2, 12),
        resolutions=["480p", "720p", "1080p"],
        supports_camera_fixed=True,
        rpm=300, concurrency=5,
        note="Text-to-video only",
    ),
    SeedanceModel(
        id="1.0-lite-i2v",
        name="Seedance 1.0 Lite I2V",
        model_id="doubao-seedance-1-0-lite-i2v-250428",
        modes=["i2v", "multimodal"],
        duration_range=(2, 12),
        resolutions=["480p", "720p", "1080p"],
        supports_camera_fixed=True,
        rpm=300, concurrency=5,
        note="Image-to-video and multimodal reference",
    ),
]

MODELS_BY_ID: dict[str, SeedanceModel] = {m.id: m for m in SEEDANCE_MODELS}

RESOLUTION_PIXEL_MAP: dict[str, dict[str, dict[str, tuple[int, int]]]] = {
    "2.0": {
        "480p": {
            "21:9": (624, 256), "16:9": (832, 480), "4:3": (624, 480),
            "1:1": (480, 480), "3:4": (480, 624), "9:16": (480, 832),
        },
        "720p": {
            "21:9": (1680, 720), "16:9": (1280, 720), "4:3": (960, 720),
            "1:1": (720, 720), "3:4": (720, 960), "9:16": (720, 1280),
        },
    },
    "2.0-fast": {
        "480p": {
            "21:9": (624, 256), "16:9": (832, 480), "4:3": (624, 480),
            "1:1": (480, 480), "3:4": (480, 624), "9:16": (480, 832),
        },
        "720p": {
            "21:9": (1680, 720), "16:9": (1280, 720), "4:3": (960, 720),
            "1:1": (720, 720), "3:4": (720, 960), "9:16": (720, 1280),
        },
    },
    "1.5-pro": {
        "480p": {
            "16:9": (848, 480), "1:1": (544, 544), "9:16": (480, 848),
        },
        "720p": {
            "16:9": (1280, 720), "1:1": (720, 720), "9:16": (720, 1280),
        },
        "1080p": {
            "16:9": (1920, 1080), "1:1": (1080, 1080), "9:16": (1080, 1920),
        },
    },
}

# 1.0 models share the same resolution map
for _mid in ("1.0-pro", "1.0-pro-fast", "1.0-lite-t2v", "1.0-lite-i2v"):
    RESOLUTION_PIXEL_MAP[_mid] = RESOLUTION_PIXEL_MAP["1.5-pro"]


def get_model(model_id: str) -> SeedanceModel | None:
    return MODELS_BY_ID.get(model_id)


def model_to_dict(m: SeedanceModel) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "model_id": m.model_id,
        "modes": m.modes,
        "duration_range": list(m.duration_range),
        "resolutions": m.resolutions,
        "supports_draft": m.supports_draft,
        "supports_audio": m.supports_audio,
        "supports_web_search": m.supports_web_search,
        "supports_camera_fixed": m.supports_camera_fixed,
        "rpm": m.rpm,
        "concurrency": m.concurrency,
        "note": m.note,
    }

