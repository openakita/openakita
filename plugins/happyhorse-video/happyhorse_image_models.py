"""Image-generation catalog for happyhorse-video.

This mirrors the useful surface from ``plugins/tongyi-image`` but keeps the
HappyHorse plugin self-contained: users can generate storyboard/key-frame
images inside the same app, then pass the produced ``asset_ids`` directly into
``hh_i2v`` / ``hh_r2v`` / digital-human modes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageModeSpec:
    id: str
    label_zh: str
    label_en: str
    icon: str
    required_assets: tuple[str, ...]
    description_zh: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "icon": self.icon,
            "required_assets": list(self.required_assets),
            "description_zh": self.description_zh,
        }


@dataclass(frozen=True)
class ImageModelSpec:
    id: str
    model_id: str
    label_zh: str
    category: str
    api_type: str
    sizes: tuple[str, ...]
    max_input_images: int = 0
    supports_negative: bool = False
    supports_prompt_extend: bool = False
    supports_thinking: bool = False
    supports_sequential: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "model_id": self.model_id,
            "label_zh": self.label_zh,
            "category": self.category,
            "api_type": self.api_type,
            "sizes": list(self.sizes),
            "max_input_images": self.max_input_images,
            "supports_negative": self.supports_negative,
            "supports_prompt_extend": self.supports_prompt_extend,
            "supports_thinking": self.supports_thinking,
            "supports_sequential": self.supports_sequential,
        }


IMAGE_MODES: tuple[ImageModeSpec, ...] = (
    ImageModeSpec(
        id="image_text2img",
        label_zh="文生图片",
        label_en="Text to Image",
        icon="image",
        required_assets=("prompt",),
        description_zh="用文字生成关键帧、海报、角色设定或分镜图。",
    ),
    ImageModeSpec(
        id="image_edit",
        label_zh="图像编辑",
        label_en="Image Edit",
        icon="wand-sparkles",
        required_assets=("prompt", "images"),
        description_zh="上传 1-9 张参考图，按提示词做融合、改图或重新设计。",
    ),
    ImageModeSpec(
        id="image_style_repaint",
        label_zh="风格重绘",
        label_en="Style Repaint",
        icon="palette",
        required_assets=("images",),
        description_zh="把人像或插画转换为漫画、二次元、国风、未来科技等风格。",
    ),
    ImageModeSpec(
        id="image_background",
        label_zh="背景生成",
        label_en="Background Generation",
        icon="layers",
        required_assets=("images",),
        description_zh="适合商品图换背景：保留主体，按文本或参考图生成新背景。",
    ),
    ImageModeSpec(
        id="image_outpaint",
        label_zh="画面扩展",
        label_en="Outpainting",
        icon="expand",
        required_assets=("images",),
        description_zh="把现有图片向外扩展成新比例或更大画幅。",
    ),
    ImageModeSpec(
        id="image_sketch",
        label_zh="涂鸦作画",
        label_en="Sketch to Image",
        icon="pen-tool",
        required_assets=("prompt", "images"),
        description_zh="用草图 + 文字生成完整图片，适合快速视觉草案。",
    ),
    ImageModeSpec(
        id="image_ecommerce",
        label_zh="电商场景图",
        label_en="E-commerce Image",
        icon="shopping-bag",
        required_assets=("prompt",),
        description_zh="按商品名和场景生成主图、白底图、生活方式图等电商素材。",
    ),
)


IMAGE_MODE_BY_ID = {m.id: m for m in IMAGE_MODES}


IMAGE_MODELS: tuple[ImageModelSpec, ...] = (
    ImageModelSpec(
        id="wan27-pro",
        model_id="wan2.7-image-pro",
        label_zh="万相 2.7 Pro",
        category="multimodal",
        api_type="async",
        sizes=("1K", "2K", "4K"),
        max_input_images=9,
        supports_thinking=True,
        supports_sequential=True,
    ),
    ImageModelSpec(
        id="wan27",
        model_id="wan2.7-image",
        label_zh="万相 2.7",
        category="multimodal",
        api_type="async",
        sizes=("1K", "2K"),
        max_input_images=9,
        supports_thinking=True,
        supports_sequential=True,
    ),
    ImageModelSpec(
        id="qwen-pro",
        model_id="qwen-image-2.0-pro",
        label_zh="千问 2.0 Pro",
        category="multimodal",
        api_type="sync",
        sizes=("512*512", "1024*1024", "1024*1536", "1536*1024", "2048*2048"),
        max_input_images=3,
        supports_negative=True,
        supports_prompt_extend=True,
    ),
    ImageModelSpec(
        id="qwen",
        model_id="qwen-image-2.0",
        label_zh="千问 2.0",
        category="multimodal",
        api_type="sync",
        sizes=("512*512", "1024*1024", "1024*1536", "1536*1024", "2048*2048"),
        max_input_images=3,
        supports_negative=True,
        supports_prompt_extend=True,
    ),
    ImageModelSpec(
        id="wan26",
        model_id="wan2.6-image",
        label_zh="万相 2.6 图文编辑",
        category="multimodal",
        api_type="async",
        sizes=("1K", "2K"),
        max_input_images=4,
        supports_negative=True,
        supports_prompt_extend=True,
    ),
)


IMAGE_MODEL_BY_ID = {m.id: m for m in IMAGE_MODELS}


STYLE_REPAINT_PRESETS: tuple[dict[str, object], ...] = (
    {"index": 0, "name": "复古漫画"},
    {"index": 1, "name": "3D童话"},
    {"index": 2, "name": "二次元"},
    {"index": 3, "name": "小清新"},
    {"index": 4, "name": "未来科技"},
    {"index": 5, "name": "国画古风"},
    {"index": 6, "name": "将军百战"},
)


SKETCH_STYLES: tuple[dict[str, str], ...] = (
    {"id": "<flat>", "name": "扁平插画"},
    {"id": "<oil_painting>", "name": "油画"},
    {"id": "<anime>", "name": "二次元"},
    {"id": "<3d_cartoon>", "name": "3D卡通"},
    {"id": "<watercolor>", "name": "水彩"},
)


ECOMMERCE_SCENES: tuple[dict[str, str], ...] = (
    {"id": "hero", "name": "商品主图", "prompt": "干净背景，正面展示，突出商品卖点"},
    {"id": "white", "name": "白底图", "prompt": "纯白背景，电商平台上架图，主体清晰"},
    {"id": "scene", "name": "场景图", "prompt": "真实使用场景，生活方式摄影，商业广告质感"},
    {"id": "detail", "name": "细节图", "prompt": "产品局部特写，展示材质、纹理和核心功能"},
)


DEFAULT_IMAGE_MODEL = "wan27-pro"
DEFAULT_IMAGE_SIZE = "2K"


def image_model_for(model_id: str | None) -> ImageModelSpec:
    if model_id and model_id in IMAGE_MODEL_BY_ID:
        return IMAGE_MODEL_BY_ID[model_id]
    if model_id:
        for model in IMAGE_MODELS:
            if model.model_id == model_id:
                return model
    return IMAGE_MODEL_BY_ID[DEFAULT_IMAGE_MODEL]


def build_image_catalog() -> dict[str, object]:
    return {
        "modes": [m.to_dict() for m in IMAGE_MODES],
        "models": [m.to_dict() for m in IMAGE_MODELS],
        "default_model": DEFAULT_IMAGE_MODEL,
        "default_size": DEFAULT_IMAGE_SIZE,
        "style_presets": list(STYLE_REPAINT_PRESETS),
        "sketch_styles": list(SKETCH_STYLES),
        "ecommerce_scenes": list(ECOMMERCE_SCENES),
        "sizes": sorted({s for m in IMAGE_MODELS for s in m.sizes}),
    }
