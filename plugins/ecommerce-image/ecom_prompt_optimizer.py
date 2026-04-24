"""Prompt optimization — template library and Brain-powered enhancement.

Three optimization levels (image and video share the same labels):
  - light: minor polish, keep user intent
  - professional: rewrite for high-quality output
  - creative: full creative rewrite

Two domains:
  - kind="image" -> ``optimize_prompt`` (商品图、详情页、海报)
  - kind="video" -> ``optimize_video_prompt`` (Seedance 时间轴格式)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates by category
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS = {
    "light": (
        "你是电商图片生成提示词优化助手。对用户提示词进行轻度优化：\n"
        "1. 保持用户核心描述完全不变\n"
        "2. 补充画面细节：光影方向（侧光/逆光/环境光）、材质质感（金属/玻璃/磨砂）、景深层次\n"
        "3. 添加电商级画质关键词：超高清、商业摄影、锐利细节、色彩准确\n"
        "4. 若涉及产品图，强调「产品主体清晰完整、边缘锐利、无裁切」\n"
        "5. 若涉及编辑/替换，强调「仅修改指定区域，其余部分保持原图完全一致」\n"
        "直接输出优化后的提示词，不要解释。"
    ),
    "professional": (
        "你是资深电商视觉设计师和 AI 提示词工程专家。将用户描述优化为专业级提示词：\n"
        "1. 分析用户意图，提取核心需求和场景类型\n"
        "2. 按结构组织提示词（按重要性排序，AI模型对靠前的词权重更高）：\n"
        "   - 画面类型与用途（电商主图/详情图/海报/白底图）\n"
        "   - 产品主体描述（名称、外观、材质、颜色、状态）\n"
        "   - 构图与取景（居中/三分构图/45度/俯拍/特写/全景）\n"
        "   - 场景与背景（纯白/渐变/生活场景/影棚）\n"
        "   - 光影设计（柔光箱/伦勃朗光/逆光轮廓/自然窗光）\n"
        "   - 风格与色调（写实商拍/极简/日系/赛博朋克）\n"
        "   - 画质要求（8K超清/商业广告级/锐利对焦/浅景深）\n"
        "3. 产品主体永远是画面焦点，禁止被遮挡或模糊\n"
        "4. 使用专业摄影和设计术语，避免模糊形容词\n"
        "5. 对于编辑类任务：必须强调「仅修改指定区域，保持原图的构图、色调、光影、其他元素完全不变」\n"
        "6. 对于海报类任务：明确文字区域预留位置、层次结构、视觉动线\n"
        "直接输出优化后的提示词，不要解释。"
    ),
    "creative": (
        "你是具有丰富想象力的创意总监和 AI 艺术家。将基本描述转化为高转化视觉创意提示词：\n"
        "1. 在保持产品核心特征和可识别性的前提下大胆发挥\n"
        "2. 运用视觉手法增强冲击力：微距特写/夸张透视/光影对比/粒子爆发/悬浮效果\n"
        "3. 创造独特氛围：将产品置于意想不到却合理的场景中\n"
        "4. 融入前沿视觉趋势：玻璃拟态/酸性设计/极光渐变/3D超现实\n"
        "5. 产品必须是视觉焦点，创意服务于商业转化而非纯艺术\n"
        "6. 确保生成图片适合电商平台展示，不含争议元素\n"
        "直接输出创意提示词，不要解释。"
    ),
}

CATEGORY_ENHANCERS = {
    "image": (
        "电商商品图，商业级产品摄影，产品主体占画面85%以上，"
        "边缘锐利清晰，色彩还原准确，无水印无文字叠加，"
        "适合淘宝天猫800x800主图标准，"
    ),
    "image_edit": (
        "图片编辑任务，仅修改用户指定的部分，"
        "严格保持原图的构图、色调、光影、透视、其他所有元素完全不变，"
        "修改区域与原图自然融合，无PS痕迹，"
    ),
    "image_replace": (
        "元素替换任务，仅替换用户指定的元素，"
        "严格保持产品主体的位置、大小、角度、光影完全不变，"
        "替换后的元素与原图环境自然融合，光照方向和色温一致，"
    ),
    "image_translate": (
        "图片文字翻译任务，准确翻译图中所有可见文字为目标语言，"
        "严格保持原图的版式布局、字体风格、字号比例、颜色、"
        "背景设计和所有非文字元素完全不变，"
        "翻译后的文字在新语言中通顺自然、无语法错误，"
    ),
    "detail": (
        "电商详情页图片，信息层次清晰，"
        "顶部为产品展示/卖点标题区，中部为功能图解/参数说明，"
        "底部为使用场景/信任背书，宽度750px标准，"
    ),
    "poster": (
        "电商营销海报，视觉冲击力强，品牌调性一致，"
        "构图预留文字区域（上方或下方1/3），"
        "主视觉突出产品，配色鲜明醒目，适合手机屏幕浏览，"
    ),
    "video": "电商短视频画面，动态感强，高质量画面，",
}

STYLE_MODIFIERS = {
    "realistic": "写实商业摄影风格，影棚专业布光，浅景深虚化背景，真实材质质感，色彩准确，",
    "anime": "精致动漫插画风格，色彩鲜明饱和，线条流畅清晰，日系电商风格，",
    "3d_render": "3D渲染风格，精致建模，全局光照，PBR材质，C4D/Blender渲染质感，",
    "flat": "扁平设计风格，简洁几何图形，鲜明色块对比，现代感排版，",
    "watercolor": "水彩艺术风格，柔和晕染过渡，自然纸张纹理，文艺清新，",
    "minimalist": "极简主义风格，大面积留白，精炼核心元素，高级感，呼吸感，",
}


# ---------------------------------------------------------------------------
# Main optimize function
# ---------------------------------------------------------------------------

async def optimize_prompt(
    brain: Any,
    prompt: str,
    *,
    level: str = "professional",
    category: str = "",
    style: str = "",
) -> str:
    """Optimize a prompt using the Brain. Returns the enhanced prompt string."""
    if not prompt.strip():
        return prompt

    system = SYSTEM_PROMPTS.get(level, SYSTEM_PROMPTS["professional"])

    prefix = ""
    if category and category in CATEGORY_ENHANCERS:
        prefix += CATEGORY_ENHANCERS[category]
    if style and style in STYLE_MODIFIERS:
        prefix += STYLE_MODIFIERS[style]

    user_msg = prompt
    if prefix:
        user_msg = f"[参考风格关键词: {prefix.rstrip('，')}]\n\n用户原始描述：{prompt}"

    try:
        if hasattr(brain, "think_lightweight"):
            result = await brain.think_lightweight(prompt=user_msg, system=system)
        elif hasattr(brain, "think"):
            result = await brain.think(prompt=user_msg, system=system)
        else:
            logger.warning("Brain has no think method, returning enhanced prompt")
            return f"{prefix}{prompt}" if prefix else prompt

        if isinstance(result, str):
            optimized = result.strip()
        elif isinstance(result, dict):
            optimized = result.get("content", "").strip()
        else:
            optimized = getattr(result, "content", "").strip() or str(result).strip()

        if not optimized:
            return f"{prefix}{prompt}" if prefix else prompt
        return optimized

    except Exception as e:
        logger.warning("Prompt optimization failed: %s", e)
        return f"{prefix}{prompt}" if prefix else prompt


async def enhance_for_batch(
    brain: Any,
    base_prompt: str,
    count: int = 4,
    *,
    category: str = "",
) -> list[str]:
    """Generate multiple prompt variations for batch generation."""
    system = (
        f"你是提示词变体生成器。根据基础描述，生成 {count} 个不同变体的提示词。\n"
        f"每个变体保持核心主题但改变：角度、光影、配色、场景、构图中的一两项。\n"
        f"输出格式：每行一个提示词，不要编号，不要解释。"
    )
    try:
        if hasattr(brain, "think_lightweight"):
            result = await brain.think_lightweight(prompt=base_prompt, system=system)
        else:
            result = await brain.think(prompt=base_prompt, system=system)

        text = result if isinstance(result, str) else getattr(result, "content", str(result))
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        return lines[:count] if len(lines) >= count else lines + [base_prompt] * (count - len(lines))
    except Exception:
        return [base_prompt] * count


# ===========================================================================
# VIDEO prompt optimization (Seedance time-axis style, e-commerce flavored)
# ===========================================================================

VIDEO_OPTIMIZE_SYSTEM_PROMPT = """你是电商短视频提示词专家，擅长为 Seedance 视频模型撰写高转化率提示词。

## 核心原则
1. 提示词必须是「画面描述」而非「脚本旁白」— 视频模型只理解视觉
2. 每句话都要能转化为一个具体画面
3. 镜头运动必须明确：推/拉/摇/移/跟/环绕/升降/手持晃动
4. 时间节奏紧凑，信息密度高

## 提示词结构（必须遵循）
[整体风格+色调+质感]，[时长]秒，[画幅]
0-{t1}秒：[开场画面] + [镜头运动] + [产品出现方式]
{t1}-{t2}秒：[核心内容] + [镜头运动] + [卖点视觉表现]
{t2}-{duration}秒：[收尾画面] + [镜头运动] + [产品最终呈现]

## 电商视频必备要素
- 3秒内必须出现产品或抓眼睛的视觉钩子
- 中段展示1-2个核心卖点的使用场景
- 产品材质/质感要有特写镜头
- 光影描述要具体：影棚灯/逆光/侧光/丁达尔效应
- 结尾留出转化引导空间

## 镜头语言速查
推/拉/摇/移/跟/环绕/升降/希区柯克变焦/一镜到底/手持晃动

## 约束
- 80-250字，不超过300字
- 直接输出最终提示词，不要解释或标注
- 不要输出JSON格式
"""

VIDEO_OPTIMIZE_USER_TEMPLATE = """## 用户输入
{user_prompt}

## 当前参数
模式: {mode}, 时长: {duration}秒, 比例: {ratio}
已上传素材: {asset_summary}

## 优化级别: {level}
{level_instruction}

请生成适合 Seedance 模型的电商短视频专业提示词。"""

VIDEO_LEVEL_INSTRUCTIONS = {
    "light": "轻度润色：保留原意，补充镜头和氛围词，结构化为时间轴。",
    "professional": "专业重写：完整时间轴 + 镜头语言 + 声音设计 + 卖点节奏。",
    "creative": "创意改写：在保留产品核心的前提下，加入隐喻 / 反差 / 视觉冲击，确保仍服务于商业转化。",
    "storyboard": "分镜脚本：每段镜头细致描述（动作 / 取景 / 节奏 / 音效），便于后期手工微调。",
}


CAMERA_KEYWORDS = [
    {"zh": "推镜头", "en": "Push in", "desc": "向前靠近主体"},
    {"zh": "拉镜头", "en": "Pull out", "desc": "向后远离主体"},
    {"zh": "摇镜头", "en": "Pan", "desc": "水平旋转拍摄"},
    {"zh": "移镜头", "en": "Dolly/Tracking", "desc": "平移跟随"},
    {"zh": "跟镜头", "en": "Follow", "desc": "追踪主体运动"},
    {"zh": "环绕镜头", "en": "Orbit", "desc": "绕产品旋转，电商最常用"},
    {"zh": "升降镜头", "en": "Crane/Jib", "desc": "垂直升降"},
    {"zh": "希区柯克变焦", "en": "Dolly Zoom", "desc": "推拉+变焦同时进行，戏剧化"},
    {"zh": "一镜到底", "en": "One-take", "desc": "长镜头无剪辑"},
    {"zh": "手持晃动", "en": "Handheld", "desc": "真实测评/Vlog 质感"},
]

ATMOSPHERE_KEYWORDS = {
    "light": ["逆光", "侧光", "丁达尔效应", "伦勃朗光", "体积光", "柔光", "硬光", "影棚灯"],
    "color": ["暖色调", "冷色调", "高饱和", "莫兰迪色", "黑金质感", "莫奈油画", "霓虹"],
    "texture": ["商业广告级", "电影级", "纪录片", "iPhone 实拍感", "CG 渲染", "胶片颗粒"],
    "mood": ["治愈", "热血", "紧凑", "高级感", "促销热闹", "极简清爽", "轻奢", "未来感"],
}

MODE_FORMULAS = {
    "t2v": "主体+动作 / 场景+氛围 / 镜头+运动 / 转化点",
    "i2v": "首图特征延续 + 镜头运动 + 画面变化 + 转化点",
    "i2v_end": "首尾帧之间的过渡 + 中间画面变化 + 镜头运动",
    "multimodal": "参考「图片N」中的特征 + 参考「视频N」的运镜 + 旁白/台词",
    "edit": "保留不变的部分 + 替换/增加/删除元素 + 时机和位置",
    "extend": "延续描述 + 新内容方向 + 过渡方式",
}


VIDEO_PROMPT_TEMPLATES = [
    {
        "id": "ecom_product_360",
        "name": "产品 360° 旋转",
        "name_en": "Product 360 Showcase",
        "description": "干净背景 + 环绕镜头 + 卖点叠加",
        "modes": ["t2v", "i2v"],
        "template": "商业广告风格，{duration}秒，{ratio}，简洁干净背景\n0-{t1}秒：环绕镜头，产品全景缓慢旋转\n{t1}-{t2}秒：推镜头特写，强调材质和细节\n{t2}-{duration}秒：拉镜头，产品落定，文字卖点出现\n【声音】轻快电子配乐 + 转场音效",
        "params": {},
    },
    {
        "id": "ecom_unboxing",
        "name": "开箱测评",
        "name_en": "Unboxing Review",
        "description": "美妆/数码/食品适用",
        "modes": ["t2v", "i2v", "multimodal"],
        "template": "Vlog 实拍风格，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：手持晃动，拆开包装，产品露出\n{t1}-{t2}秒：俯拍特写，展示质感和包装细节\n{t2}-{duration}秒：使用过程，呈现真实体验\n【声音】拆封音效 + 轻松BGM",
        "params": {"atmosphere": "温馨真实"},
    },
    {
        "id": "ecom_selling_point",
        "name": "卖点演示",
        "name_en": "Selling Point Demo",
        "description": "突出 1-2 个核心功能演示",
        "modes": ["t2v", "i2v"],
        "template": "商业 CG 风格，{duration}秒，{ratio}，干净影棚灯光\n0-{t1}秒：产品出现，旁白点出主卖点\n{t1}-{t2}秒：动效演示功能 ({selling_point})\n{t2}-{duration}秒：场景使用画面，强调结果\n【声音】科技音效 + 简短人声口播",
        "params": {"selling_point": "核心功能"},
    },
    {
        "id": "ecom_scene_immersion",
        "name": "场景代入",
        "name_en": "Scene Immersion",
        "description": "把产品放进真实生活场景",
        "modes": ["t2v", "i2v", "multimodal"],
        "template": "生活实拍风格，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：场景全景 ({scene})\n{t1}-{t2}秒：人物使用产品，自然光下细节\n{t2}-{duration}秒：人物满意表情 + 产品定格\n【声音】环境音 + 治愈系配乐",
        "params": {"atmosphere": "温暖治愈", "scene": "客厅 / 厨房 / 户外"},
    },
    {
        "id": "ecom_compare",
        "name": "对比测试",
        "name_en": "Before vs After",
        "description": "Before / After 对比，强转化",
        "modes": ["t2v", "i2v"],
        "template": "测评风格，{duration}秒，{ratio}，干净背景\n0-{t1}秒：左侧 Before 状态，痛点呈现\n{t1}-{t2}秒：使用产品过程，分屏对比\n{t2}-{duration}秒：右侧 After 效果，差异强调\n【声音】对比音效 + 升调BGM",
        "params": {},
    },
    {
        "id": "ecom_promo_burst",
        "name": "促销爆点",
        "name_en": "Promo Burst",
        "description": "618 / 双11 / 活动倒计时",
        "modes": ["t2v"],
        "template": "促销广告风格，{duration}秒，{ratio}，霓虹高饱和\n0-{t1}秒：标题动效 ({campaign}) 砸入画面\n{t1}-{t2}秒：产品阵列展示 + 折扣价格闪烁\n{t2}-{duration}秒：行动指令 ({cta}) + 倒计时\n【声音】嗨点鼓点 + 促销音效",
        "params": {"campaign": "限时大促", "cta": "立即抢购"},
    },
    {
        "id": "ecom_pov_user",
        "name": "用户视角 POV",
        "name_en": "User POV",
        "description": "第一视角拍摄使用过程",
        "modes": ["t2v", "i2v"],
        "template": "POV 第一视角，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：手部入画，拿起产品\n{t1}-{t2}秒：操作产品，细节特写\n{t2}-{duration}秒：成果展示，POV 看向使用结果\n【声音】环境音 + 真实操作音效",
        "params": {"atmosphere": "真实自然"},
    },
]


def _extract_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("content", "") or result.get("text", "") or str(result)
    return getattr(result, "content", "") or getattr(result, "text", "") or str(result)


async def optimize_video_prompt(
    brain: Any,
    user_prompt: str,
    *,
    mode: str = "t2v",
    duration: int = 5,
    ratio: str = "16:9",
    asset_summary: str = "无",
    level: str = "professional",
) -> str:
    """LLM-powered video prompt rewrite into Seedance time-axis format.

    Falls back to the user's original prompt on any error so the calling
    pipeline never aborts.
    """
    if not user_prompt or not user_prompt.strip():
        return user_prompt or ""

    if brain is None:
        logger.info("optimize_video_prompt: brain unavailable, returning original prompt")
        return user_prompt

    level_instruction = VIDEO_LEVEL_INSTRUCTIONS.get(level, VIDEO_LEVEL_INSTRUCTIONS["professional"])
    user_msg = VIDEO_OPTIMIZE_USER_TEMPLATE.format(
        user_prompt=user_prompt,
        mode=mode,
        duration=duration,
        ratio=ratio,
        asset_summary=asset_summary,
        level=level,
        level_instruction=level_instruction,
    )

    try:
        if hasattr(brain, "think_lightweight"):
            result = await brain.think_lightweight(
                prompt=user_msg, system=VIDEO_OPTIMIZE_SYSTEM_PROMPT, max_tokens=4096,
            )
        elif hasattr(brain, "think"):
            result = await brain.think(
                prompt=user_msg, system=VIDEO_OPTIMIZE_SYSTEM_PROMPT,
            )
        else:
            return user_prompt
        text = _extract_text(result).strip()
        return text or user_prompt
    except Exception as e:
        logger.warning("optimize_video_prompt failed (%s); falling back to original prompt", e)
        return user_prompt


def get_prompt_guide(kind: str = "video") -> dict:
    """Return reference data for the prompt assistant panel."""
    if kind == "video":
        return {
            "cameras": CAMERA_KEYWORDS,
            "atmosphere": ATMOSPHERE_KEYWORDS,
            "formulas": MODE_FORMULAS,
        }
    return {
        "cameras": [],
        "atmosphere": {},
        "formulas": {},
    }


def get_prompt_templates(kind: str = "video") -> list[dict]:
    if kind == "video":
        return VIDEO_PROMPT_TEMPLATES
    return []
