"""Tongyi image model definitions, capability matrix, and API endpoint mapping."""

from __future__ import annotations

from dataclasses import dataclass, field


ENDPOINT_MULTIMODAL = "/services/aigc/multimodal-generation/generation"
ENDPOINT_IMAGE_GEN = "/services/aigc/image-generation/generation"
ENDPOINT_BG_GEN = "/services/aigc/background-generation/generation/"
ENDPOINT_OUTPAINT = "/services/aigc/image2image/out-painting"
ENDPOINT_IMAGE_SYNTH = "/services/aigc/image2image/image-synthesis"


@dataclass
class ImageModel:
    id: str
    name: str
    model_id: str
    category: str
    api_endpoint: str
    api_type: str  # "sync" | "async" | "both"
    max_resolution: str = ""
    supports_negative: bool = False
    supports_prompt_extend: bool = False
    supports_thinking: bool = False
    supports_sequential: bool = False
    supports_color_palette: bool = False
    supports_image_input: bool = False
    supports_bbox: bool = False
    max_input_images: int = 0
    sizes: list[str] = field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------------------
# Text-to-Image models
# ---------------------------------------------------------------------------

IMAGE_MODELS: list[ImageModel] = [
    ImageModel(
        id="wan27-pro",
        name="万相 2.7 Pro",
        model_id="wan2.7-image-pro",
        category="text2img",
        api_endpoint=ENDPOINT_MULTIMODAL,
        api_type="both",
        max_resolution="4K",
        supports_negative=False,
        supports_prompt_extend=False,
        supports_thinking=True,
        supports_sequential=True,
        supports_color_palette=True,
        supports_image_input=True,
        supports_bbox=True,
        max_input_images=9,
        sizes=["1K", "2K", "4K"],
        note="功能最全面，文生图支持4K，图像编辑/组图支持2K",
    ),
    ImageModel(
        id="wan27",
        name="万相 2.7",
        model_id="wan2.7-image",
        category="text2img",
        api_endpoint=ENDPOINT_MULTIMODAL,
        api_type="both",
        max_resolution="2K",
        supports_negative=False,
        supports_prompt_extend=False,
        supports_thinking=True,
        supports_sequential=True,
        supports_color_palette=True,
        supports_image_input=True,
        supports_bbox=True,
        max_input_images=9,
        sizes=["1K", "2K"],
        note="速度更快",
    ),
    ImageModel(
        id="qwen-pro",
        name="千问 2.0 Pro",
        model_id="qwen-image-2.0-pro",
        category="text2img",
        api_endpoint=ENDPOINT_MULTIMODAL,
        api_type="sync",
        max_resolution="2048*2048",
        supports_negative=True,
        supports_prompt_extend=True,
        supports_thinking=False,
        supports_sequential=False,
        supports_color_palette=False,
        supports_image_input=True,
        supports_bbox=False,
        max_input_images=3,
        sizes=["512*512", "1024*1024", "1024*1536", "1536*1024", "2048*2048"],
        note="擅长文本渲染、海报、PPT生成",
    ),
    ImageModel(
        id="qwen",
        name="千问 2.0",
        model_id="qwen-image-2.0",
        category="text2img",
        api_endpoint=ENDPOINT_MULTIMODAL,
        api_type="sync",
        max_resolution="2048*2048",
        supports_negative=True,
        supports_prompt_extend=True,
        supports_thinking=False,
        supports_sequential=False,
        supports_color_palette=False,
        supports_image_input=True,
        supports_bbox=False,
        max_input_images=3,
        sizes=["512*512", "1024*1024", "1024*1536", "1536*1024", "2048*2048"],
        note="千问加速版",
    ),

    # ---------------------------------------------------------------------------
    # Image Edit models (some overlap with text2img — wan2.7 does both)
    # ---------------------------------------------------------------------------
    ImageModel(
        id="wan26",
        name="万相 2.6",
        model_id="wan2.6-image",
        category="img_edit",
        api_endpoint=ENDPOINT_MULTIMODAL,
        api_type="both",
        max_resolution="2K",
        supports_negative=True,
        supports_prompt_extend=True,
        supports_image_input=True,
        max_input_images=4,
        sizes=["1K", "2K"],
        note="图文混排输出模式",
    ),
    ImageModel(
        id="wan25",
        name="万相 2.5 i2i",
        model_id="wan2.5-i2i-preview",
        category="img_edit",
        api_endpoint=ENDPOINT_IMAGE_SYNTH,
        api_type="both",
        max_resolution="1280*1280",
        supports_negative=True,
        supports_prompt_extend=True,
        supports_image_input=True,
        max_input_images=3,
        sizes=["768*768", "1024*1024", "1280*1280"],
        note="简单图像编辑与多图融合",
    ),

    # ---------------------------------------------------------------------------
    # Style Repaint
    # ---------------------------------------------------------------------------
    ImageModel(
        id="style-repaint",
        name="人像风格重绘",
        model_id="wanx-style-repaint-v1",
        category="style_repaint",
        api_endpoint=ENDPOINT_IMAGE_GEN,
        api_type="async",
        supports_image_input=True,
        max_input_images=1,
        note="7种预置风格 + 自定义风格参考图",
    ),

    # ---------------------------------------------------------------------------
    # Background Generation
    # ---------------------------------------------------------------------------
    ImageModel(
        id="bg-gen",
        name="图像背景生成",
        model_id="wanx-background-generation-v2",
        category="background",
        api_endpoint=ENDPOINT_BG_GEN,
        api_type="async",
        supports_image_input=True,
        max_input_images=1,
        note="文本/图像/边缘引导，电商商品换背景",
    ),

    # ---------------------------------------------------------------------------
    # Outpainting
    # ---------------------------------------------------------------------------
    ImageModel(
        id="outpaint",
        name="图像画面扩展",
        model_id="image-out-painting",
        category="outpaint",
        api_endpoint=ENDPOINT_OUTPAINT,
        api_type="async",
        supports_image_input=True,
        max_input_images=1,
        note="宽高比/等比例/方向像素/旋转扩图",
    ),

    # ---------------------------------------------------------------------------
    # Sketch-to-Image
    # ---------------------------------------------------------------------------
    ImageModel(
        id="sketch",
        name="涂鸦作画",
        model_id="wanx-sketch-to-image-lite",
        category="sketch",
        api_endpoint=ENDPOINT_IMAGE_SYNTH,
        api_type="async",
        supports_image_input=True,
        max_input_images=1,
        sizes=["768*768", "1024*1024"],
        note="手绘草图+文字描述，5种风格",
    ),
]

MODELS_BY_ID: dict[str, ImageModel] = {m.id: m for m in IMAGE_MODELS}
MODELS_BY_MODEL_ID: dict[str, ImageModel] = {m.model_id: m for m in IMAGE_MODELS}
MODELS_BY_CATEGORY: dict[str, list[ImageModel]] = {}
for _m in IMAGE_MODELS:
    MODELS_BY_CATEGORY.setdefault(_m.category, []).append(_m)

STYLE_REPAINT_PRESETS = [
    {"index": 0, "name": "复古漫画", "name_en": "Retro Comic"},
    {"index": 1, "name": "3D童话", "name_en": "3D Fairy Tale"},
    {"index": 2, "name": "二次元", "name_en": "Anime"},
    {"index": 3, "name": "小清新", "name_en": "Fresh & Clean"},
    {"index": 4, "name": "未来科技", "name_en": "Futuristic"},
    {"index": 5, "name": "国画古风", "name_en": "Traditional Chinese"},
    {"index": 6, "name": "将军百战", "name_en": "General's Glory"},
]

SKETCH_STYLES = [
    {"id": "<flat>", "name": "扁平插画", "name_en": "Flat Illustration"},
    {"id": "<oil_painting>", "name": "油画", "name_en": "Oil Painting"},
    {"id": "<anime>", "name": "二次元", "name_en": "Anime"},
    {"id": "<3d_cartoon>", "name": "3D卡通", "name_en": "3D Cartoon"},
    {"id": "<watercolor>", "name": "水彩", "name_en": "Watercolor"},
]

ECOMMERCE_SCENE_PRESETS = [
    {
        "id": "hero",
        "name": "商品主图",
        "name_en": "Hero Image",
        "desc": "正面展示，干净背景，突出商品",
    },
    {
        "id": "bg_white",
        "name": "白底图",
        "name_en": "White Background",
        "desc": "纯白背景产品图，适合平台上架",
    },
    {
        "id": "bg_scene",
        "name": "场景图",
        "name_en": "Scene Image",
        "desc": "商品在使用场景中的效果展示",
    },
    {
        "id": "bg_lifestyle",
        "name": "生活方式图",
        "name_en": "Lifestyle Image",
        "desc": "商品融入生活场景的氛围图",
    },
    {
        "id": "detail",
        "name": "细节图",
        "name_en": "Detail Shot",
        "desc": "商品材质、工艺、细节特写",
    },
    {
        "id": "banner",
        "name": "促销横幅",
        "name_en": "Promo Banner",
        "desc": "电商活动横幅 / 海报",
    },
]

RECOMMENDED_SIZES: dict[str, dict[str, str]] = {
    "wan27-pro": {
        "1:1": "2048*2048", "16:9": "2688*1536", "9:16": "1536*2688",
        "4:3": "2368*1728", "3:4": "1728*2368",
        "1:1_4k": "4096*4096", "16:9_4k": "4096*2304", "9:16_4k": "2304*4096",
    },
    "wan27": {
        "1:1": "2048*2048", "16:9": "2688*1536", "9:16": "1536*2688",
        "4:3": "2368*1728", "3:4": "1728*2368",
    },
    "qwen-pro": {
        "1:1": "2048*2048", "16:9": "2048*1152", "9:16": "1152*2048",
        "4:3": "2048*1536", "3:4": "1536*2048",
    },
    "qwen": {
        "1:1": "2048*2048", "16:9": "2048*1152", "9:16": "1152*2048",
        "4:3": "2048*1536", "3:4": "1536*2048",
    },
}


def get_model(model_id: str) -> ImageModel | None:
    return MODELS_BY_ID.get(model_id) or MODELS_BY_MODEL_ID.get(model_id)


def get_models_for_category(category: str) -> list[ImageModel]:
    return MODELS_BY_CATEGORY.get(category, [])


def model_to_dict(m: ImageModel) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "model_id": m.model_id,
        "category": m.category,
        "api_type": m.api_type,
        "max_resolution": m.max_resolution,
        "supports_negative": m.supports_negative,
        "supports_prompt_extend": m.supports_prompt_extend,
        "supports_thinking": m.supports_thinking,
        "supports_sequential": m.supports_sequential,
        "supports_color_palette": m.supports_color_palette,
        "supports_image_input": m.supports_image_input,
        "supports_bbox": m.supports_bbox,
        "max_input_images": m.max_input_images,
        "sizes": m.sizes,
        "note": m.note,
    }
