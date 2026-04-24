"""Model registry — available DashScope image models and Ark video models.

Provides model metadata for frontend model selectors and API routing.
Video model capability matrix mirrors plugins/seedance-video/models.py so the
frontend can render the same advanced controls (resolution, audio, camera
fixed, web search, draft, etc.).
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Image models (DashScope)
# ---------------------------------------------------------------------------

IMAGE_MODELS: list[dict] = [
    {
        "id": "wan2.7-image-pro",
        "name": "万相 2.7 Pro",
        "name_en": "Wan 2.7 Image Pro",
        "provider": "dashscope",
        "type": "text_to_image",
        "capability": "multimodal",
        "sizes": ["1K", "2K", "4K"],
        "max_n": 4,
        "speed": "normal",
        "recommended_for": [
            "image_main_replicate", "image_main_gen", "image_main_suite",
            "image_batch_gen", "detail_replicate", "detail_suite",
            "detail_long", "detail_new_product",
        ],
    },
    {
        "id": "wan2.7-image",
        "name": "万相 2.7",
        "name_en": "Wan 2.7 Image",
        "provider": "dashscope",
        "type": "text_to_image",
        "capability": "multimodal",
        "sizes": ["1K", "2K"],
        "max_n": 4,
        "speed": "fast",
        "recommended_for": ["image_batch_gen"],
    },
    {
        "id": "wan2.6-image",
        "name": "万相 2.6 · 仅编辑",
        "name_en": "Wan 2.6 Image Edit",
        "provider": "dashscope",
        "type": "image_edit",
        "requires_image": True,
        "capability": "multimodal",
        "sizes": ["1K", "2K"],
        "max_n": 4,
        "speed": "normal",
        "recommended_for": [
            "image_batch_edit", "image_batch_replace", "image_translate",
        ],
    },
    {
        "id": "qwen-image-2.0-pro",
        "name": "千问 2.0 Pro",
        "name_en": "Qwen Image 2.0 Pro",
        "provider": "dashscope",
        "type": "text_to_image",
        "capability": "multimodal",
        "sizes": ["512*512", "1024*1024", "1024*1536", "1536*1024", "2048*2048"],
        "max_n": 4,
        "speed": "normal",
        "note": "擅长文本渲染、海报、PPT生成",
        "recommended_for": [
            "poster_private_domain", "poster_product",
            "poster_holiday", "poster_campaign",
        ],
    },
    {
        "id": "qwen-image-2.0",
        "name": "千问 2.0",
        "name_en": "Qwen Image 2.0",
        "provider": "dashscope",
        "type": "text_to_image",
        "capability": "multimodal",
        "sizes": ["512*512", "1024*1024", "1024*1536", "1536*1024", "2048*2048"],
        "max_n": 4,
        "speed": "fast",
        "note": "千问加速版",
        "recommended_for": [],
    },
]


# ---------------------------------------------------------------------------
# Video models (Ark / Volcengine)
# ---------------------------------------------------------------------------

# Alias -> backend model id (the actual ID Ark accepts in the request body).
_ARK_MODEL_MAP: dict[str, str] = {
    "seedance-2-0": "doubao-seedance-2-0-260128",
    "seedance-2-0-fast": "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-1-5-pro": "doubao-seedance-1-5-pro-251215",
    "doubao-seedance-1-0-pro": "doubao-seedance-1-0-pro-250528",
    "doubao-seedance-1-0-pro-fast": "doubao-seedance-1-0-pro-fast-251015",
    "doubao-seedance-1-0-lite-t2v": "doubao-seedance-1-0-lite-t2v-250428",
    "doubao-seedance-1-0-lite-i2v": "doubao-seedance-1-0-lite-i2v-250428",
    # Backward-compat aliases for old saved tasks; do NOT show in the UI.
    "seedance-1-lite": "doubao-seedance-1-0-lite-t2v-250428",
    "doubao-seedance-1-lite": "doubao-seedance-1-0-lite-t2v-250428",
}


VIDEO_MODELS: list[dict] = [
    {
        "id": "seedance-2-0",
        "name": "Seedance 2.0 (旗舰)",
        "name_en": "Seedance 2.0",
        "provider": "ark",
        "type": "multimodal_to_video",
        "modes": ["t2v", "i2v", "i2v_end", "multimodal", "edit", "extend"],
        "duration_range": [4, 15],
        "resolutions": ["480p", "720p"],
        "max_duration": 15,
        "speed": "normal",
        "supports_image": True,
        "supports_audio": True,
        "supports_camera_fixed": False,
        "supports_draft": False,
        "supports_web_search": True,
        "rpm": 600,
        "concurrency": 10,
        "recommended_for": [
            "video_hot_replicate",
            "video_ad_oneclick",
            "video_storyboard",
            "video_character_replace",
        ],
    },
    {
        "id": "seedance-2-0-fast",
        "name": "Seedance 2.0 Fast (低延迟)",
        "name_en": "Seedance 2.0 Fast",
        "provider": "ark",
        "type": "multimodal_to_video",
        "modes": ["t2v", "i2v", "i2v_end", "multimodal", "edit", "extend"],
        "duration_range": [4, 15],
        "resolutions": ["480p", "720p"],
        "max_duration": 15,
        "speed": "fast",
        "supports_image": True,
        "supports_audio": True,
        "supports_camera_fixed": False,
        "supports_draft": False,
        "supports_web_search": True,
        "rpm": 600,
        "concurrency": 10,
        "recommended_for": ["video_ad_oneclick", "video_hot_replicate"],
    },
    {
        "id": "doubao-seedance-1-5-pro",
        "name": "豆包 Seedance 1.5 Pro (高清)",
        "name_en": "Doubao Seedance 1.5 Pro",
        "provider": "ark",
        "type": "multimodal_to_video",
        "modes": ["t2v", "i2v", "i2v_end"],
        "duration_range": [4, 12],
        "resolutions": ["480p", "720p", "1080p"],
        "max_duration": 12,
        "speed": "normal",
        "supports_image": True,
        "supports_audio": True,
        "supports_camera_fixed": True,
        "supports_draft": True,
        "supports_web_search": False,
        "rpm": 300,
        "concurrency": 5,
        "recommended_for": ["video_hot_replicate", "video_storyboard"],
    },
    {
        "id": "doubao-seedance-1-0-pro",
        "name": "豆包 Seedance 1.0 Pro",
        "name_en": "Doubao Seedance 1.0 Pro",
        "provider": "ark",
        "type": "multimodal_to_video",
        "modes": ["t2v", "i2v", "i2v_end"],
        "duration_range": [2, 12],
        "resolutions": ["480p", "720p", "1080p"],
        "max_duration": 12,
        "speed": "normal",
        "supports_image": True,
        "supports_audio": False,
        "supports_camera_fixed": True,
        "supports_draft": False,
        "supports_web_search": False,
        "rpm": 300,
        "concurrency": 5,
        "recommended_for": ["video_storyboard"],
    },
    {
        "id": "doubao-seedance-1-0-pro-fast",
        "name": "豆包 Seedance 1.0 Pro (快速)",
        "name_en": "Doubao Seedance 1.0 Pro Fast",
        "provider": "ark",
        "type": "multimodal_to_video",
        "modes": ["t2v", "i2v", "i2v_end"],
        "duration_range": [2, 12],
        "resolutions": ["480p", "720p", "1080p"],
        "max_duration": 12,
        "speed": "fast",
        "supports_image": True,
        "supports_audio": False,
        "supports_camera_fixed": True,
        "supports_draft": False,
        "supports_web_search": False,
        "rpm": 300,
        "concurrency": 5,
        "recommended_for": ["video_ad_oneclick"],
    },
    {
        "id": "doubao-seedance-1-0-lite-t2v",
        "name": "豆包 Seedance 1.0 Lite (文生视频)",
        "name_en": "Doubao Seedance 1.0 Lite T2V",
        "provider": "ark",
        "type": "text_to_video",
        "modes": ["t2v"],
        "duration_range": [2, 12],
        "resolutions": ["480p", "720p", "1080p"],
        "max_duration": 12,
        "speed": "fast",
        "supports_image": False,
        "supports_audio": False,
        "supports_camera_fixed": True,
        "supports_draft": False,
        "supports_web_search": False,
        "rpm": 300,
        "concurrency": 5,
        "recommended_for": ["video_storyboard"],
    },
    {
        "id": "doubao-seedance-1-0-lite-i2v",
        "name": "豆包 Seedance 1.0 Lite (图生视频)",
        "name_en": "Doubao Seedance 1.0 Lite I2V",
        "provider": "ark",
        "type": "image_to_video",
        "modes": ["i2v", "multimodal"],
        "duration_range": [2, 12],
        "resolutions": ["480p", "720p", "1080p"],
        "max_duration": 12,
        "speed": "fast",
        "supports_image": True,
        "supports_audio": False,
        "supports_camera_fixed": True,
        "supports_draft": False,
        "supports_web_search": False,
        "rpm": 300,
        "concurrency": 5,
        "recommended_for": ["video_character_replace"],
    },
]


# ---------------------------------------------------------------------------
# Resolution -> pixel map (per model + resolution + ratio)
# Mirrors plugins/seedance-video/models.py for accurate display.
# ---------------------------------------------------------------------------

_PIXELS_2_0: dict[str, dict[str, list[int]]] = {
    "480p": {
        "21:9": [624, 256], "16:9": [832, 480], "4:3": [624, 480],
        "1:1": [480, 480], "3:4": [480, 624], "9:16": [480, 832],
    },
    "720p": {
        "21:9": [1680, 720], "16:9": [1280, 720], "4:3": [960, 720],
        "1:1": [720, 720], "3:4": [720, 960], "9:16": [720, 1280],
    },
}

_PIXELS_1_X: dict[str, dict[str, list[int]]] = {
    "480p": {
        "16:9": [848, 480], "1:1": [544, 544], "9:16": [480, 848],
    },
    "720p": {
        "16:9": [1280, 720], "1:1": [720, 720], "9:16": [720, 1280],
    },
    "1080p": {
        "16:9": [1920, 1080], "1:1": [1080, 1080], "9:16": [1080, 1920],
    },
}

RESOLUTION_PIXEL_MAP: dict[str, dict[str, dict[str, list[int]]]] = {
    "seedance-2-0": _PIXELS_2_0,
    "seedance-2-0-fast": _PIXELS_2_0,
    "doubao-seedance-1-5-pro": _PIXELS_1_X,
    "doubao-seedance-1-0-pro": _PIXELS_1_X,
    "doubao-seedance-1-0-pro-fast": _PIXELS_1_X,
    "doubao-seedance-1-0-lite-t2v": _PIXELS_1_X,
    "doubao-seedance-1-0-lite-i2v": _PIXELS_1_X,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_all_models() -> dict:
    return {
        "image": IMAGE_MODELS,
        "video": VIDEO_MODELS,
        "resolution_map": RESOLUTION_PIXEL_MAP,
    }


def get_image_models() -> list[dict]:
    return IMAGE_MODELS


def get_video_models() -> list[dict]:
    """Return user-visible video models (excludes deprecated aliases)."""
    return VIDEO_MODELS


def get_video_model(model_id: str) -> dict | None:
    for m in VIDEO_MODELS:
        if m["id"] == model_id:
            return m
    return None


def get_model_by_id(model_id: str) -> dict | None:
    for m in IMAGE_MODELS + VIDEO_MODELS:
        if m["id"] == model_id:
            return m
    return None


def get_video_model_id(model_alias: str) -> str:
    """Resolve a model alias to actual Ark model endpoint ID."""
    return _ARK_MODEL_MAP.get(model_alias, model_alias)


def get_recommended_model(feature_id: str, model_type: str = "image") -> str:
    """Get the first recommended model for a feature."""
    models = IMAGE_MODELS if model_type == "image" else VIDEO_MODELS
    for m in models:
        if feature_id in m.get("recommended_for", []):
            return m["id"]
    return models[0]["id"] if models else ""
