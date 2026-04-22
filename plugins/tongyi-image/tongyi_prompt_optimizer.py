"""Prompt optimization engine for Tongyi Image — LLM-powered refinement plus
static keyword libraries for the UI prompt guide page."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM system prompt based on official DashScope prompt guidelines
# ---------------------------------------------------------------------------

OPTIMIZE_SYSTEM_PROMPT = """\
你是通义万相/千问图像生成提示词专家。根据用户的简短描述，生成专业的图像生成提示词。

## 提示词公式

### 基础公式
主体描述 + 场景/背景 + 风格类型

### 进阶公式
主体描述 + 场景/背景 + 镜头语言 + 光线/氛围词 + 风格类型 + 细节修饰

## 核心原则
1. 主体先行：明确描述画面中的核心主体（人物、物体、场景）
2. 场景渲染：交代背景环境、时间、空间
3. 风格定调：明确艺术风格（写实摄影、水彩、油画、3D渲染、动漫等）
4. 镜头语言：景别（特写/近景/中景/远景/全景）+ 视角（平视/俯视/仰视/航拍）
5. 光线氛围：光源类型 + 氛围词（温馨/史诗/神秘/治愈）
6. 细节修饰：材质、纹理、色彩、品质描述词

## 注意事项
- 提示词保持 50-200 字，简洁精炼
- 英文提示词通常效果更好，但中文也支持
- 避免歧义、矛盾的描述
- 使用具体的描述替代抽象概念
- 人像类建议加入面部特征、服装、动作等
- 产品类建议加入材质、光泽、摆放方式等
"""

OPTIMIZE_USER_TEMPLATE = """\
## 用户输入
{user_prompt}

## 当前参数
目标模型: {model}
图片尺寸: {size}
{style_hint}

## 优化级别: {level}
{level_instruction}

请生成优化后的图像生成提示词。只输出最终提示词，不要解释。"""

LEVEL_INSTRUCTIONS = {
    "light": "轻度润色：保留原意，优化措辞和结构，补充必要的风格和质量描述词。",
    "professional": "专业扩写：在原意基础上，补充完整的场景、光线、构图、风格细节，输出专业级提示词。",
    "creative": "创意发散：基于原始描述进行创意发散，加入新颖的视觉元素和艺术表达，追求独特视觉效果。",
}


class PromptOptimizeError(Exception):
    """Raised when prompt optimization fails."""


async def optimize_prompt(
    brain: Any,
    user_prompt: str,
    model: str = "wan27-pro",
    size: str = "2K",
    style: str = "",
    level: str = "professional",
) -> str:
    """Call the host LLM to refine a user prompt for image generation.

    Raises PromptOptimizeError on failure.
    """
    level_instruction = LEVEL_INSTRUCTIONS.get(level, LEVEL_INSTRUCTIONS["professional"])
    style_hint = f"风格偏好: {style}" if style else ""

    user_msg = OPTIMIZE_USER_TEMPLATE.format(
        user_prompt=user_prompt,
        model=model,
        size=size,
        style_hint=style_hint,
        level=level,
        level_instruction=level_instruction,
    )

    if hasattr(brain, "think_lightweight"):
        try:
            result = await brain.think_lightweight(
                prompt=user_msg, system=OPTIMIZE_SYSTEM_PROMPT
            )
            text = _extract_text(result)
            if text.strip():
                return text
        except Exception as e:
            logger.warning("think_lightweight failed, falling back to think: %s", e)

    try:
        if hasattr(brain, "think"):
            result = await brain.think(prompt=user_msg, system=OPTIMIZE_SYSTEM_PROMPT)
            text = _extract_text(result)
            if not text.strip():
                raise PromptOptimizeError("LLM 返回了空内容")
            return text
        elif hasattr(brain, "chat"):
            result = await brain.chat(
                messages=[
                    {"role": "system", "content": OPTIMIZE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ]
            )
            text = _extract_text(result)
            if not text.strip():
                raise PromptOptimizeError("LLM 返回了空内容")
            return text
        else:
            raise PromptOptimizeError("Brain 对象没有 think() 或 chat() 方法")
    except PromptOptimizeError:
        raise
    except Exception as e:
        logger.error("Prompt optimization failed: %s", e)
        raise PromptOptimizeError(f"LLM 调用失败: {e}") from e


def _extract_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("content", "")
    return getattr(result, "content", "") or str(result)


# ---------------------------------------------------------------------------
# Static resources — served to UI prompt guide page
# ---------------------------------------------------------------------------

PROMPT_TEMPLATES = [
    {
        "id": "portrait",
        "name": "人像写真",
        "name_en": "Portrait",
        "description": "人物肖像、写真、半身照",
        "categories": ["text2img"],
        "template": "一位{age}{gender}，{features}，穿着{clothing}，{pose}，{background}，{lighting}，{style}摄影，高清细腻",
        "example": "一位年轻女性，长发飘逸，穿着白色连衣裙，侧身回眸微笑，薰衣草花田背景，金色夕阳逆光，写实摄影，高清细腻",
    },
    {
        "id": "landscape",
        "name": "自然风光",
        "name_en": "Landscape",
        "description": "自然风景、山水、星空",
        "categories": ["text2img"],
        "template": "{scene}，{time}，{weather}，{details}，{style}风格，{quality}",
        "example": "雪山湖泊倒影，清晨日出，薄雾弥漫，远处雪峰连绵，近处野花盛开，风景摄影风格，8K超高清",
    },
    {
        "id": "ecommerce",
        "name": "电商海报",
        "name_en": "E-commerce",
        "description": "产品图、电商海报、商品展示",
        "categories": ["text2img", "background"],
        "template": "{product}，{placement}，{background}，{lighting}，商业产品摄影，{quality}",
        "example": "一瓶精致的香水，45度角摆放在大理石台面上，浅粉色渐变背景，柔和的侧光和高光，商业产品摄影，高质感高清",
    },
    {
        "id": "chinese_ink",
        "name": "水墨国风",
        "name_en": "Chinese Ink",
        "description": "中国水墨画、国风插画",
        "categories": ["text2img"],
        "template": "{subject}，{scene}，中国水墨画风格，{ink_style}，{composition}，宣纸质感",
        "example": "山间古寺，云雾缭绕松林间，中国水墨画风格，浓淡干湿变化丰富，留白构图，宣纸质感",
    },
    {
        "id": "3d_render",
        "name": "3D渲染",
        "name_en": "3D Render",
        "description": "3D模型、产品渲染、C4D风格",
        "categories": ["text2img"],
        "template": "{subject}，3D渲染风格，{material}，{lighting}，{background}，{quality}",
        "example": "可爱的卡通小熊IP形象，3D渲染风格，光滑塑料材质，柔和的环境光，纯色背景，Blender渲染，8K高清",
    },
    {
        "id": "illustration",
        "name": "插画风格",
        "name_en": "Illustration",
        "description": "扁平插画、概念设计、儿童绘本",
        "categories": ["text2img", "sketch"],
        "template": "{subject}，{scene}，{illustration_style}插画风格，{colors}色彩，{quality}",
        "example": "小女孩牵着气球走在童话小镇街道上，两旁是彩色糖果屋，扁平矢量插画风格，马卡龙色彩，高清精致",
    },
    {
        "id": "cyberpunk",
        "name": "赛博朋克",
        "name_en": "Cyberpunk",
        "description": "未来都市、霓虹灯、科幻场景",
        "categories": ["text2img"],
        "template": "{subject}，赛博朋克风格，{scene}，霓虹灯光，{details}，{quality}",
        "example": "雨夜中的未来都市街道，赛博朋克风格，巨大的全息广告牌投射蓝紫色光芒，积水路面反射霓虹灯光，写实渲染，8K超清",
    },
    {
        "id": "food",
        "name": "美食摄影",
        "name_en": "Food Photography",
        "description": "美食、餐饮、食品广告",
        "categories": ["text2img"],
        "template": "{food}，{plating}，{props}，{lighting}，美食摄影，{quality}",
        "example": "精致的日式刺身拼盘，白色陶瓷餐盘摆盘，搭配新鲜山葵和紫苏叶，自然窗光侧照，美食摄影，高清诱人",
    },
]

STYLE_KEYWORDS = {
    "realistic": ["写实摄影", "超写实", "照片级", "电影画质", "高清细腻"],
    "watercolor": ["水彩", "水彩风格", "透明水彩", "湿画法", "晕染效果"],
    "oil_painting": ["油画", "厚涂油画", "印象派", "古典油画", "笔触质感"],
    "3d_cartoon": ["3D卡通", "皮克斯风格", "迪士尼风格", "Blender渲染", "C4D风格"],
    "chinese_ink": ["水墨", "写意水墨", "工笔画", "国画", "宣纸质感"],
    "anime": ["二次元", "日系动漫", "赛璐珞", "轻小说插画", "动漫风格"],
    "flat_vector": ["扁平插画", "矢量插画", "几何图形", "极简主义", "图形设计"],
    "surreal": ["超现实主义", "梦幻", "达利风格", "魔幻现实", "意识流"],
    "cyberpunk": ["赛博朋克", "蒸汽朋克", "复古未来", "霓虹", "数字艺术"],
    "origami": ["折纸", "纸艺", "剪纸", "立体纸雕", "纸质感"],
    "clay": ["粘土", "定格动画", "手办", "微缩模型", "黏土质感"],
    "pixel": ["像素画", "8bit风格", "复古游戏", "点阵风格"],
}

LIGHTING_KEYWORDS = {
    "natural": ["自然光", "窗光", "柔和日光", "正午阳光", "多云天漫射光"],
    "dramatic": ["逆光", "侧逆光", "轮廓光", "伦勃朗光", "分割光"],
    "atmospheric": ["丁达尔效应", "体积光", "氛围光", "雾气光线", "光束穿透"],
    "artificial": ["霓虹灯", "聚光灯", "环形灯", "LED灯带", "烛光"],
    "golden_hour": ["金色时刻", "日落光线", "暖色调光", "夕阳余晖"],
    "blue_hour": ["蓝调时刻", "冷色调光", "月光", "星光"],
    "studio": ["摄影棚光", "柔光箱", "反光板", "蝴蝶光", "美人光"],
}

COMPOSITION_KEYWORDS = {
    "distance": {
        "label": "景别",
        "keywords": [
            {"zh": "特写", "en": "Close-up", "desc": "聚焦局部细节"},
            {"zh": "近景", "en": "Close shot", "desc": "胸部以上"},
            {"zh": "中景", "en": "Medium shot", "desc": "腰部以上"},
            {"zh": "全景", "en": "Full shot", "desc": "完整人物"},
            {"zh": "远景", "en": "Long shot", "desc": "环境中的小人物"},
        ],
    },
    "angle": {
        "label": "视角",
        "keywords": [
            {"zh": "平视", "en": "Eye level", "desc": "平行视角"},
            {"zh": "俯视", "en": "High angle", "desc": "从上往下"},
            {"zh": "仰视", "en": "Low angle", "desc": "从下往上，显高大"},
            {"zh": "航拍", "en": "Aerial view", "desc": "高空俯瞰"},
            {"zh": "虫眼视角", "en": "Worm's eye view", "desc": "极低角度"},
        ],
    },
    "lens": {
        "label": "镜头",
        "keywords": [
            {"zh": "微距", "en": "Macro", "desc": "极近距离拍摄细节"},
            {"zh": "广角", "en": "Wide angle", "desc": "扩大视野，夸张透视"},
            {"zh": "长焦", "en": "Telephoto", "desc": "压缩空间感"},
            {"zh": "鱼眼", "en": "Fisheye", "desc": "球面畸变效果"},
            {"zh": "移轴", "en": "Tilt-shift", "desc": "微缩模型效果"},
        ],
    },
}

NEGATIVE_PROMPT_PRESETS = {
    "general": "低质量, 模糊, 变形, 丑陋, 水印, 文字, 标志, lowres, bad quality, blurry, deformed",
    "portrait": "变形的手, 多余手指, 面部变形, 不对称眼睛, 模糊面部, 身体比例异常, extra fingers, deformed hands",
    "landscape": "人物, 文字, 水印, 建筑物变形, 不自然颜色, 过度饱和, text, watermark, oversaturated",
}

MODE_FORMULAS = {
    "text2img": {
        "basic": "主体描述 + 场景/背景 + 风格类型",
        "advanced": "主体描述 + 场景/背景 + 镜头语言 + 光线/氛围词 + 风格类型 + 细节修饰",
        "tips": [
            "主体先行：优先描述画面核心内容",
            "风格明确：写实、水彩、油画、3D等",
            "光线加持：逆光、丁达尔效应等增加氛围",
            "景别搭配：特写、中景、远景等构图控制",
        ],
    },
    "img_edit": {
        "basic": "参考图片 + 编辑指令",
        "advanced": "参考图片 + 具体编辑指令 + 保留元素说明 + 输出风格要求",
        "tips": [
            "指令具体：明确说明要改什么",
            "可搭配框选 bbox 定位编辑区域",
            "多图融合时描述各图片的作用",
        ],
    },
    "style_repaint": {
        "basic": "人物照片 + 风格选择",
        "tips": [
            "使用高清正面照效果最佳",
            "人脸占比不宜过小",
            "避免夸张姿势和表情",
        ],
    },
    "background": {
        "basic": "主体图(透明背景) + 文本/图像引导",
        "tips": [
            "主体图需要RGBA透明背景",
            "文本引导描述目标背景场景",
            "图像引导提供参考背景图",
        ],
    },
    "outpaint": {
        "basic": "原图 + 扩展方式(比例/方向/旋转)",
        "tips": [
            "支持宽高比扩图和等比例扩图",
            "可指定上下左右方向的像素扩展",
            "旋转扩图可矫正倾斜照片",
        ],
    },
    "sketch": {
        "basic": "草图 + 文字描述 + 风格",
        "tips": [
            "草图提供形状和布局",
            "文字描述补充细节和氛围",
            "sketch_weight 控制草图约束强度",
        ],
    },
    "ecommerce": {
        "basic": "商品名称/描述 + 选择场景类型",
        "advanced": "商品名称/描述 + 商品图(可选) + 勾选需要的图片类型",
        "tips": [
            "上传透明背景商品图可自动换背景",
            "未上传图片则用 AI 从描述生成",
            "可勾选多种场景一键批量生成",
            "主图/白底图适合平台上架",
            "场景图/生活方式图适合详情页",
        ],
    },
}


# ---------------------------------------------------------------------------
# i18n payloads for the prompt guide page.
#
# Strategy (intentionally hybrid, matches the UI's "wide table" layout):
#
#   * Short keywords (style / lighting):
#       Returned as bilingual {zh, en} pairs so the UI can render them
#       SIDE-BY-SIDE in any locale. This costs almost no payload (~20 KB)
#       and avoids a refetch when the user toggles language for a single
#       chip-list page.
#
#   * Long-form copy (mode formulas / templates / negative presets):
#       Returned in ONE locale per request. Translating a paragraph into the
#       wrong language is much worse than refetching, so we let the client
#       pass ?locale=zh|en and serve the matching dictionary.
#
#   * composition_keywords:
#       Already shipped with bilingual {zh, en} keyword entries. We additionally
#       localize the per-category `label` and the per-keyword `desc` text so
#       the headings and tooltips follow the active locale.
#
# To add a new locale, add a "<lang>" entry to the *_I18N dicts below; missing
# entries silently fall back to the request's base language and finally to "zh".
# ---------------------------------------------------------------------------

# --- Short keywords: bilingual entries, no locale switching needed ---------

_STYLE_KEYWORDS_I18N: dict[str, list[dict[str, str]]] = {
    "realistic":   [
        {"zh": "写实摄影", "en": "Photorealistic"},
        {"zh": "超写实",   "en": "Hyperrealistic"},
        {"zh": "照片级",   "en": "Photographic"},
        {"zh": "电影画质", "en": "Cinematic"},
        {"zh": "高清细腻", "en": "High detail"},
    ],
    "watercolor":  [
        {"zh": "水彩",       "en": "Watercolor"},
        {"zh": "水彩风格",   "en": "Watercolor style"},
        {"zh": "透明水彩",   "en": "Transparent watercolor"},
        {"zh": "湿画法",     "en": "Wet-on-wet"},
        {"zh": "晕染效果",   "en": "Wash gradient"},
    ],
    "oil_painting": [
        {"zh": "油画",       "en": "Oil painting"},
        {"zh": "厚涂油画",   "en": "Impasto oil"},
        {"zh": "印象派",     "en": "Impressionist"},
        {"zh": "古典油画",   "en": "Classical oil"},
        {"zh": "笔触质感",   "en": "Visible brushstrokes"},
    ],
    "3d_cartoon":  [
        {"zh": "3D卡通",       "en": "3D cartoon"},
        {"zh": "皮克斯风格",   "en": "Pixar-style"},
        {"zh": "迪士尼风格",   "en": "Disney-style"},
        {"zh": "Blender渲染",  "en": "Blender render"},
        {"zh": "C4D风格",      "en": "Cinema 4D style"},
    ],
    "chinese_ink": [
        {"zh": "水墨",       "en": "Ink wash"},
        {"zh": "写意水墨",   "en": "Freehand ink"},
        {"zh": "工笔画",     "en": "Gongbi (fine brush)"},
        {"zh": "国画",       "en": "Chinese painting"},
        {"zh": "宣纸质感",   "en": "Xuan paper texture"},
    ],
    "anime":       [
        {"zh": "二次元",       "en": "Anime"},
        {"zh": "日系动漫",     "en": "Japanese anime"},
        {"zh": "赛璐珞",       "en": "Cel-shaded"},
        {"zh": "轻小说插画",   "en": "Light-novel illustration"},
        {"zh": "动漫风格",     "en": "Anime style"},
    ],
    "flat_vector": [
        {"zh": "扁平插画",   "en": "Flat illustration"},
        {"zh": "矢量插画",   "en": "Vector illustration"},
        {"zh": "几何图形",   "en": "Geometric shapes"},
        {"zh": "极简主义",   "en": "Minimalism"},
        {"zh": "图形设计",   "en": "Graphic design"},
    ],
    "surreal":     [
        {"zh": "超现实主义", "en": "Surrealism"},
        {"zh": "梦幻",       "en": "Dreamlike"},
        {"zh": "达利风格",   "en": "Dalí-style"},
        {"zh": "魔幻现实",   "en": "Magical realism"},
        {"zh": "意识流",     "en": "Stream of consciousness"},
    ],
    "cyberpunk":   [
        {"zh": "赛博朋克",   "en": "Cyberpunk"},
        {"zh": "蒸汽朋克",   "en": "Steampunk"},
        {"zh": "复古未来",   "en": "Retro-futurism"},
        {"zh": "霓虹",       "en": "Neon-lit"},
        {"zh": "数字艺术",   "en": "Digital art"},
    ],
    "origami":     [
        {"zh": "折纸",       "en": "Origami"},
        {"zh": "纸艺",       "en": "Paper craft"},
        {"zh": "剪纸",       "en": "Papercut"},
        {"zh": "立体纸雕",   "en": "3D paper sculpture"},
        {"zh": "纸质感",     "en": "Paper texture"},
    ],
    "clay":        [
        {"zh": "粘土",       "en": "Clay"},
        {"zh": "定格动画",   "en": "Stop-motion"},
        {"zh": "手办",       "en": "Figurine"},
        {"zh": "微缩模型",   "en": "Diorama miniature"},
        {"zh": "黏土质感",   "en": "Clay texture"},
    ],
    "pixel":       [
        {"zh": "像素画",     "en": "Pixel art"},
        {"zh": "8bit风格",   "en": "8-bit style"},
        {"zh": "复古游戏",   "en": "Retro game"},
        {"zh": "点阵风格",   "en": "Dot-matrix style"},
    ],
}

_LIGHTING_KEYWORDS_I18N: dict[str, list[dict[str, str]]] = {
    "natural":     [
        {"zh": "自然光",         "en": "Natural light"},
        {"zh": "窗光",           "en": "Window light"},
        {"zh": "柔和日光",       "en": "Soft daylight"},
        {"zh": "正午阳光",       "en": "Noon sunlight"},
        {"zh": "多云天漫射光",   "en": "Overcast diffuse light"},
    ],
    "dramatic":    [
        {"zh": "逆光",       "en": "Backlight"},
        {"zh": "侧逆光",     "en": "Rim back-light"},
        {"zh": "轮廓光",     "en": "Rim light"},
        {"zh": "伦勃朗光",   "en": "Rembrandt lighting"},
        {"zh": "分割光",     "en": "Split lighting"},
    ],
    "atmospheric": [
        {"zh": "丁达尔效应", "en": "Tyndall effect"},
        {"zh": "体积光",     "en": "Volumetric light"},
        {"zh": "氛围光",     "en": "Ambient light"},
        {"zh": "雾气光线",   "en": "Misty light shafts"},
        {"zh": "光束穿透",   "en": "God rays"},
    ],
    "artificial":  [
        {"zh": "霓虹灯",     "en": "Neon lights"},
        {"zh": "聚光灯",     "en": "Spotlight"},
        {"zh": "环形灯",     "en": "Ring light"},
        {"zh": "LED灯带",    "en": "LED strip"},
        {"zh": "烛光",       "en": "Candlelight"},
    ],
    "golden_hour": [
        {"zh": "金色时刻",   "en": "Golden hour"},
        {"zh": "日落光线",   "en": "Sunset light"},
        {"zh": "暖色调光",   "en": "Warm tone lighting"},
        {"zh": "夕阳余晖",   "en": "Afterglow"},
    ],
    "blue_hour":   [
        {"zh": "蓝调时刻",   "en": "Blue hour"},
        {"zh": "冷色调光",   "en": "Cool tone lighting"},
        {"zh": "月光",       "en": "Moonlight"},
        {"zh": "星光",       "en": "Starlight"},
    ],
    "studio":      [
        {"zh": "摄影棚光",   "en": "Studio lighting"},
        {"zh": "柔光箱",     "en": "Softbox"},
        {"zh": "反光板",     "en": "Reflector"},
        {"zh": "蝴蝶光",     "en": "Butterfly lighting"},
        {"zh": "美人光",     "en": "Beauty lighting"},
    ],
}

# --- Composition: localize label + desc; keywords already bilingual --------

_COMPOSITION_LABELS_I18N: dict[str, dict[str, str]] = {
    "distance": {"zh": "景别", "en": "Shot size"},
    "angle":    {"zh": "视角", "en": "Camera angle"},
    "lens":     {"zh": "镜头", "en": "Lens"},
}

# Keyed by zh so we can look up the EN tooltip without changing COMPOSITION_KEYWORDS.
_COMPOSITION_DESC_EN: dict[str, str] = {
    "特写":         "Tight focus on a small detail",
    "近景":         "From the chest up",
    "中景":         "From the waist up",
    "全景":         "Whole subject in frame",
    "远景":         "Subject small within environment",
    "平视":         "Eye-level perspective",
    "俯视":         "Looking down from above",
    "仰视":         "Looking up from below; conveys grandeur",
    "航拍":         "Bird's-eye / aerial view",
    "虫眼视角":     "Extreme low angle",
    "微距":         "Macro close-up of fine detail",
    "广角":         "Wide field of view, exaggerated perspective",
    "长焦":         "Compressed depth of field",
    "鱼眼":         "Spherical fisheye distortion",
    "移轴":         "Tilt-shift miniature effect",
}

# --- Long-form: full localized copies (one shot per locale) ----------------

_MODE_FORMULAS_EN: dict[str, dict] = {
    "text2img": {
        "basic":    "Subject + Scene/Background + Style",
        "advanced": "Subject + Scene/Background + Camera language + Lighting/Mood + Style + Detail modifiers",
        "tips": [
            "Subject first: describe the focal point of the image up front.",
            "Be explicit about style: photorealistic, watercolor, oil, 3D, etc.",
            "Add lighting (backlight, Tyndall effect…) to shape mood.",
            "Combine shot sizes (close-up, medium, long) to control framing.",
        ],
    },
    "img_edit": {
        "basic":    "Reference image + Edit instruction",
        "advanced": "Reference image + Specific edit instruction + Elements to preserve + Output style",
        "tips": [
            "Be specific about WHAT to change.",
            "Combine with bbox selection to localize the edit.",
            "When mixing multiple images, describe the role of each.",
        ],
    },
    "style_repaint": {
        "basic": "Portrait photo + Target style",
        "tips": [
            "High-resolution frontal portraits give the best results.",
            "Avoid faces that are too small in the frame.",
            "Avoid extreme poses or exaggerated expressions.",
        ],
    },
    "background": {
        "basic": "Subject (transparent background) + Text/image guidance",
        "tips": [
            "Subject image must have an RGBA transparent background.",
            "Text guidance describes the target background scene.",
            "Image guidance provides a reference background.",
        ],
    },
    "outpaint": {
        "basic": "Original image + Expansion mode (ratio / direction / rotation)",
        "tips": [
            "Supports both aspect-ratio and proportional outpainting.",
            "Specify pixel padding for top/bottom/left/right.",
            "Rotation outpainting can correct tilted photos.",
        ],
    },
    "sketch": {
        "basic": "Sketch + Text description + Style",
        "tips": [
            "The sketch provides shape and layout.",
            "Text fills in detail and atmosphere.",
            "`sketch_weight` controls how strictly to follow the sketch.",
        ],
    },
    "ecommerce": {
        "basic":    "Product name/description + Pick scene types",
        "advanced": "Product name/description + Product image (optional) + Choose scenes to generate",
        "tips": [
            "Upload a transparent-background product photo to swap backgrounds automatically.",
            "Without an upload, the AI synthesizes the product from the description.",
            "Pick multiple scenes to batch-generate in one click.",
            "Hero / white-background shots are best for marketplace listings.",
            "Scene / lifestyle shots are best for detail pages.",
        ],
    },
}

_PROMPT_TEMPLATES_EN: list[dict] = [
    {
        "id": "portrait",
        "name": "Portrait",
        "name_en": "Portrait",
        "description": "Character portraits, headshots, half-body shots.",
        "categories": ["text2img"],
        "template": "A {age} {gender}, {features}, wearing {clothing}, {pose}, {background}, {lighting}, {style} photography, ultra-detailed",
        "example": "A young woman with flowing long hair, wearing a white dress, looking back over her shoulder with a smile, lavender field background, golden backlight at sunset, photorealistic photography, ultra-detailed",
    },
    {
        "id": "landscape",
        "name": "Landscape",
        "name_en": "Landscape",
        "description": "Natural scenery, mountains and waters, starry sky.",
        "categories": ["text2img"],
        "template": "{scene}, {time}, {weather}, {details}, {style} style, {quality}",
        "example": "Snow-capped mountains reflected in a lake, sunrise, drifting mist, distant peaks, wildflowers in the foreground, landscape photography, 8K ultra HD",
    },
    {
        "id": "ecommerce",
        "name": "E-commerce poster",
        "name_en": "E-commerce",
        "description": "Product shots, marketing posters, merchandise display.",
        "categories": ["text2img", "background"],
        "template": "{product}, {placement}, {background}, {lighting}, commercial product photography, {quality}",
        "example": "An elegant perfume bottle placed at a 45° angle on a marble surface, soft pink gradient backdrop, gentle side light with rim highlights, commercial product photography, premium high-resolution",
    },
    {
        "id": "chinese_ink",
        "name": "Chinese ink",
        "name_en": "Chinese Ink",
        "description": "Traditional Chinese ink wash paintings, Guofeng illustration.",
        "categories": ["text2img"],
        "template": "{subject}, {scene}, traditional Chinese ink wash style, {ink_style}, {composition}, Xuan paper texture",
        "example": "An ancient temple in the mountains, swirling clouds among pine trees, traditional Chinese ink wash, rich tonal range from dry to wet, generous negative space, Xuan paper texture",
    },
    {
        "id": "3d_render",
        "name": "3D render",
        "name_en": "3D Render",
        "description": "3D models, product renders, C4D-style imagery.",
        "categories": ["text2img"],
        "template": "{subject}, 3D render style, {material}, {lighting}, {background}, {quality}",
        "example": "A cute cartoon bear mascot, 3D render style, smooth plastic material, soft ambient lighting, solid color backdrop, Blender render, 8K HD",
    },
    {
        "id": "illustration",
        "name": "Illustration",
        "name_en": "Illustration",
        "description": "Flat illustration, concept design, children's book art.",
        "categories": ["text2img", "sketch"],
        "template": "{subject}, {scene}, {illustration_style} illustration style, {colors} palette, {quality}",
        "example": "A little girl holding a balloon walking down the street of a fairytale town, flanked by colorful candy houses, flat vector illustration, macaron color palette, crisp HD",
    },
    {
        "id": "cyberpunk",
        "name": "Cyberpunk",
        "name_en": "Cyberpunk",
        "description": "Future cities, neon lights, sci-fi scenes.",
        "categories": ["text2img"],
        "template": "{subject}, cyberpunk style, {scene}, neon lighting, {details}, {quality}",
        "example": "A future city street on a rainy night, cyberpunk style, giant holographic billboards casting blue-violet light, wet pavement reflecting neon, photorealistic render, 8K ultra HD",
    },
    {
        "id": "food",
        "name": "Food photography",
        "name_en": "Food Photography",
        "description": "Food, dining, food advertising.",
        "categories": ["text2img"],
        "template": "{food}, {plating}, {props}, {lighting}, food photography, {quality}",
        "example": "An elegant Japanese sashimi platter on a white ceramic plate, garnished with fresh wasabi and shiso leaves, natural side window light, food photography, mouth-watering HD",
    },
]

_NEGATIVE_PROMPT_PRESETS_EN: dict[str, str] = {
    "general":   "low quality, blurry, deformed, ugly, watermark, text, logo, lowres, bad quality",
    "portrait":  "deformed hands, extra fingers, distorted face, asymmetric eyes, blurry face, bad anatomy, deformed body proportions",
    "landscape": "people, text, watermark, deformed buildings, unnatural colors, oversaturated",
}

_PROMPT_TEMPLATES_I18N: dict[str, list[dict]] = {
    "zh": PROMPT_TEMPLATES,
    "en": _PROMPT_TEMPLATES_EN,
}
_MODE_FORMULAS_I18N: dict[str, dict[str, dict]] = {
    "zh": MODE_FORMULAS,
    "en": _MODE_FORMULAS_EN,
}
_NEGATIVE_PROMPT_PRESETS_I18N: dict[str, dict[str, str]] = {
    "zh": NEGATIVE_PROMPT_PRESETS,
    "en": _NEGATIVE_PROMPT_PRESETS_EN,
}


def _normalize_locale(locale: str | None) -> str:
    """Normalize an incoming locale string to one of the keys we ship.

    Falls back to ``zh`` (the project default) for unknown / empty input.
    Accepts ``zh-CN``, ``en-US``, etc. by stripping the regional suffix.
    """
    if not locale:
        return "zh"
    base = str(locale).split("-")[0].split("_")[0].lower()
    if base in ("zh", "en"):
        return base
    return "zh"


def _localize_composition(locale: str) -> dict:
    """Project COMPOSITION_KEYWORDS with localized label + desc.

    The keyword entries themselves are already shipped as bilingual
    {zh, en} pairs and are returned as-is (the UI renders both, separated
    by " / ", so the user can read whichever matches the active locale).
    """
    out: dict[str, dict] = {}
    for cat, data in COMPOSITION_KEYWORDS.items():
        label_map = _COMPOSITION_LABELS_I18N.get(cat, {})
        label = label_map.get(locale) or data.get("label", cat)
        kws = []
        for kw in data.get("keywords", []):
            new_kw = dict(kw)
            if locale == "en":
                en_desc = _COMPOSITION_DESC_EN.get(kw.get("zh", ""))
                if en_desc:
                    new_kw["desc"] = en_desc
            kws.append(new_kw)
        out[cat] = {"label": label, "keywords": kws}
    return out


def get_prompt_guide_data(locale: str | None = None) -> dict:
    """Return the full prompt guide data structure for the UI.

    Args:
        locale: Requested UI locale. Currently honours ``zh`` and ``en``;
            unknown values fall back to ``zh``. Pass-through to the language
            variants of templates / mode formulas / negative presets, while
            short keyword lists are returned as bilingual {zh,en} entries
            regardless (the UI renders both side-by-side).
    """
    loc = _normalize_locale(locale)
    return {
        "locale": loc,
        "templates": _PROMPT_TEMPLATES_I18N.get(loc, PROMPT_TEMPLATES),
        "style_keywords": _STYLE_KEYWORDS_I18N,
        "lighting_keywords": _LIGHTING_KEYWORDS_I18N,
        "composition_keywords": _localize_composition(loc),
        "negative_presets": _NEGATIVE_PROMPT_PRESETS_I18N.get(loc, NEGATIVE_PROMPT_PRESETS),
        "mode_formulas": _MODE_FORMULAS_I18N.get(loc, MODE_FORMULAS),
    }


# ---------------------------------------------------------------------------
# E-commerce suite prompt generation
# ---------------------------------------------------------------------------

_ECOMMERCE_SCENE_PROMPTS: dict[str, str] = {
    "hero": (
        "{product}，正面45度角展示，纯净渐变背景，"
        "柔和的摄影棚灯光，高光勾勒轮廓，"
        "商业产品摄影，高清锐利，8K"
    ),
    "bg_white": (
        "{product}，纯白背景，居中摆放，"
        "均匀柔光无阴影，电商上架标准白底图，"
        "产品摄影，超高清"
    ),
    "bg_scene": (
        "{product}的使用场景，{product}放置在精心布置的桌面上，"
        "搭配与产品调性匹配的装饰物，柔和自然光线，"
        "生活美学场景摄影，高质感"
    ),
    "bg_lifestyle": (
        "温馨的居家生活场景，{product}自然融入日常生活画面中，"
        "人物正在使用产品，暖色调柔和光线，"
        "生活方式摄影，治愈感，高清"
    ),
    "detail": (
        "{product}的微距特写细节，展示材质纹理和工艺细节，"
        "浅景深虚化背景，侧光强调质感，"
        "产品细节摄影，8K超清"
    ),
    "banner": (
        "电商促销横幅设计，{product}置于画面左侧，"
        "右侧留白放文案，渐变背景配活力色彩，"
        "现代平面设计风格，16:9横版，高清"
    ),
}


def generate_ecommerce_prompts(
    product_name: str,
    base_prompt: str = "",
    scenes: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Generate a list of (scene_id, prompt) tuples for e-commerce suite."""
    product = base_prompt.strip() or product_name.strip() or "产品"
    target_scenes = scenes or list(_ECOMMERCE_SCENE_PROMPTS.keys())
    results: list[tuple[str, str]] = []
    for sid in target_scenes:
        template = _ECOMMERCE_SCENE_PROMPTS.get(sid)
        if not template:
            continue
        results.append((sid, template.format(product=product)))
    return results
