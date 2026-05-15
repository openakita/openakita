"""Prompt optimization engine for happyhorse-video.

Ported from ``plugins/seedance-video/prompt_optimizer.py``. The
templates and the system prompt have been rewritten so the LLM produces
prompts tuned for HappyHorse 1.0 (native audio-sync, multi-language
lipsync) and Wan 2.6/2.7 (frame-driven multimodal). The MODE_FORMULAS
table now covers all 12 modes the plugin supports (6 video-synth + 5
digital-human + 1 long-video) instead of seedance's original 6.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


OPTIMIZE_SYSTEM_PROMPT = """你是「快乐马工作室（HappyHorse Studio）」的提示词专家，对接的视频后端是阿里云百炼上的 HappyHorse 1.0、Wan 2.6/2.7、s2v、videoretalk、animate 系列模型。请根据用户的简短描述，生成一段专业、可直接落到 DashScope 的视频提示词。

## 提示词格式规范（推荐）
[风格]风格，[时长]秒，[比例]，[氛围]
0-3 秒：[镜头运动]，[画面描述]
3-6 秒：...
...
【声音】配乐风格 + 音效（HappyHorse 原生音视频同步时建议含台词）
【参考】@图片1 用途，@视频1 用途

## 镜头语言速查
推镜头/拉镜头/摇镜头/移镜头/跟镜头/环绕镜头/升降镜头/希区柯克变焦/一镜到底/手持晃动/超级广角/无人机航拍

## 氛围关键词
光影：逆光、侧光、丁达尔效应、伦勃朗光、体积光、霓虹反射
色调：暖色调、冷色调、赛博朋克、复古胶片、黑白、莫兰迪、油画色
质感：电影级、纪录片风格、CG 质感、写实、像素风、水墨、油画感

## HappyHorse 1.0 专属能力（model 选 happyhorse-1.0-* 时优先利用）
- 原生 24fps + 音视频同步：可在 prompt 中直接写「角色说：台词内容」，模型会自带口型与人声
- 多语种唇形：支持中 / 英 / 日 / 韩 / 法 / 西 / 阿 7 语种唇形对齐，可指定「英语台词」「日语台词」等
- 多角色互动：r2v 接受多张参考人脸，prompt 中显式区分「角色 A」「角色 B」并指定动作
- 镜头建议词：包含 cinematic / dramatic camera / handheld / drone shot / dolly zoom 时模型表现更佳

## Wan 2.7 专属能力（model 选 wan2.7-* 时优先利用）
- 首尾帧（first-and-last-frame）：prompt 描写过渡过程
- 视频续写（video-continuation）：prompt 描写新内容方向 + 与原视频镜头/光线一致

## 注意事项
- 提示词建议 80-220 字，不超过 350 字
- 使用时间轴格式让镜头有节奏感
- HappyHorse 模式时声音设计要给出明确台词；非 HappyHorse 模式只描述配乐与音效
- 镜头语言让画面更专业，避免空泛形容词
"""

OPTIMIZE_USER_TEMPLATE = """## 用户输入
{user_prompt}

## 当前参数
模式: {mode}, 模型: {model_id}, 时长: {duration}秒, 比例: {ratio}, 分辨率: {resolution}
已上传素材: {asset_summary}

## 优化级别: {level}
{level_instruction}

## 模式公式提示
{mode_formula}

请生成一段适合该模型 / 模式的专业提示词。"""

LEVEL_INSTRUCTIONS = {
    "light": "轻度润色：保留原意，优化措辞和结构，补充镜头语言。",
    "professional": "专业重写：生成完整的时间轴格式提示词，包含具体的镜头语言和声音设计。",
    "storyboard": "分镜脚本：输出完整的分镜+提示词+声音设计，每个镜头细致描述。",
}


class PromptOptimizeError(Exception):
    """Raised when prompt optimization fails."""


async def optimize_prompt(
    brain: Any,
    user_prompt: str,
    *,
    mode: str = "t2v",
    model_id: str = "",
    duration: int = 5,
    ratio: str = "16:9",
    resolution: str = "720P",
    asset_summary: str = "无",
    level: str = "professional",
) -> str:
    """Call the host LLM to refine a user prompt into HappyHorse format.

    Raises :class:`PromptOptimizeError` on failure instead of silently
    returning the original prompt, so the caller can surface the error
    in the UI.
    """
    level_instruction = LEVEL_INSTRUCTIONS.get(
        level, LEVEL_INSTRUCTIONS["professional"]
    )
    mode_formula = MODE_FORMULAS.get(mode, MODE_FORMULAS["t2v"])

    user_msg = OPTIMIZE_USER_TEMPLATE.format(
        user_prompt=user_prompt,
        mode=mode,
        model_id=model_id or "(default)",
        duration=duration,
        ratio=ratio,
        resolution=resolution,
        asset_summary=asset_summary,
        level=level,
        level_instruction=level_instruction,
        mode_formula=mode_formula,
    )

    if hasattr(brain, "think_lightweight"):
        try:
            result = await brain.think_lightweight(
                prompt=user_msg, system=OPTIMIZE_SYSTEM_PROMPT
            )
            text = (
                getattr(result, "content", "")
                or (result.get("content", "") if isinstance(result, dict) else str(result))
            )
            if text and text.strip():
                return text
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "think_lightweight failed, falling back to think: %s", e
            )

    try:
        if hasattr(brain, "think"):
            result = await brain.think(
                prompt=user_msg, system=OPTIMIZE_SYSTEM_PROMPT
            )
            text = (
                getattr(result, "content", "")
                or (result.get("content", "") if isinstance(result, dict) else str(result))
            )
            if not text or not text.strip():
                raise PromptOptimizeError("LLM 返回了空内容")
            return text
        if hasattr(brain, "chat"):
            result = await brain.chat(
                messages=[
                    {"role": "system", "content": OPTIMIZE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ]
            )
            text = result.get("content", "") if isinstance(result, dict) else str(result)
            if not text.strip():
                raise PromptOptimizeError("LLM 返回了空内容")
            return text
        raise PromptOptimizeError("Brain 对象没有 think() 或 chat() 方法")
    except PromptOptimizeError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Prompt optimization failed: %s", e)
        raise PromptOptimizeError(f"LLM 调用失败: {e}") from e


# ─── Built-in prompt templates (16) ────────────────────────────────────

PROMPT_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "narrative_story",
        "name": "叙事故事",
        "name_en": "Narrative Story",
        "description": "适合有情节发展的故事视频（HappyHorse 原生音视频同步）",
        "modes": ["t2v", "i2v", "r2v"],
        "template": (
            "电影级画质，{duration}秒，{ratio}，{atmosphere}\n"
            "0-{t1}秒：{shot1}，{scene1}\n"
            "{t1}-{t2}秒：{shot2}，{scene2}\n"
            "{t2}-{duration}秒：{shot3}，{scene3}\n"
            "【声音】{music} + {sfx}\n"
            "【台词】角色 A：「{line_a}」  角色 B：「{line_b}」"
        ),
        "params": {
            "atmosphere": "温暖治愈",
            "shot1": "中景",
            "shot2": "特写",
            "shot3": "远景",
            "music": "轻柔钢琴",
            "sfx": "环境音",
            "line_a": "你终于回来了",
            "line_b": "我一直在等你",
        },
    },
    {
        "id": "product_showcase",
        "name": "产品展示",
        "name_en": "Product Showcase",
        "description": "产品旋转、细节展示、功能演示",
        "modes": ["t2v", "i2v"],
        "template": (
            "商业广告风格，{duration}秒，{ratio}，干净明亮\n"
            "0-{t1}秒：慢速环绕镜头，产品全景展示\n"
            "{t1}-{t2}秒：推镜头至特写，展示细节和材质\n"
            "{t2}-{duration}秒：拉镜头，产品在场景中使用\n"
            "【声音】科技感配乐 + 轻微机械音"
        ),
        "params": {},
    },
    {
        "id": "character_action",
        "name": "角色动作",
        "name_en": "Character Action",
        "description": "角色的动态动作场景（适合 happyhorse-1.0-r2v）",
        "modes": ["t2v", "r2v", "pose_drive"],
        "template": (
            "动作电影风格，{duration}秒，{ratio}，{atmosphere}\n"
            "0-{t1}秒：{shot1}，角色准备动作\n"
            "{t1}-{t2}秒：跟镜头，角色执行核心动作\n"
            "{t2}-{duration}秒：慢动作特写，定格瞬间\n"
            "【声音】紧张鼓点 + 动作音效"
        ),
        "params": {"atmosphere": "紧张刺激", "shot1": "全景"},
    },
    {
        "id": "landscape_travel",
        "name": "风景旅拍",
        "name_en": "Landscape Travel",
        "description": "自然风光、城市风景、旅行记录",
        "modes": ["t2v", "i2v", "long_video"],
        "template": (
            "纪录片风格，{duration}秒，{ratio}，{atmosphere}\n"
            "0-{t1}秒：升镜头/航拍，壮阔全景\n"
            "{t1}-{t2}秒：移镜头，穿过场景细节\n"
            "{t2}-{duration}秒：延时摄影，光线变化\n"
            "【声音】自然环境音 + 轻音乐"
        ),
        "params": {"atmosphere": "宁静治愈"},
    },
    {
        "id": "emotional_conflict",
        "name": "情感冲突",
        "name_en": "Emotional Conflict",
        "description": "情感张力、戏剧冲突",
        "modes": ["t2v", "r2v"],
        "template": (
            "电影质感，{duration}秒，{ratio}，{atmosphere}\n"
            "0-{t1}秒：特写，角色面部表情变化\n"
            "{t1}-{t2}秒：摇镜头，展示周围环境压力\n"
            "{t2}-{duration}秒：希区柯克变焦，情感爆发\n"
            "【声音】弦乐渐强 + 心跳声"
        ),
        "params": {"atmosphere": "压抑紧张"},
    },
    {
        "id": "vlog_narrator",
        "name": "口播讲解",
        "name_en": "Vlog Narrator",
        "description": "口播、讲解、自媒体内容（适合 photo_speak / video_relip）",
        "modes": ["t2v", "photo_speak", "video_relip"],
        "template": (
            "自媒体风格，{duration}秒，{ratio}，轻松自然\n"
            "0-{t1}秒：中景正面，人物开始讲话\n"
            "{t1}-{t2}秒：画中画 + 图文插入\n"
            "{t2}-{duration}秒：回到人物镜头，总结\n"
            "【声音】清晰人声 + 轻快 BGM\n"
            "【台词】「{narration}」"
        ),
        "params": {"narration": "今天给大家分享一个非常实用的技巧。"},
    },
    {
        "id": "music_sync",
        "name": "音乐卡点",
        "name_en": "Music Sync",
        "description": "音乐节奏同步的视觉效果",
        "modes": ["t2v", "i2v"],
        "template": (
            "MV 风格，{duration}秒，{ratio}，{atmosphere}\n"
            "0-{t1}秒：节奏预热，慢动作场景\n"
            "{t1}-{t2}秒：卡点切换，快速剪辑\n"
            "{t2}-{duration}秒：高潮释放，全景慢放\n"
            "【声音】{music}"
        ),
        "params": {"atmosphere": "酷炫动感", "music": "电子节拍"},
    },
    {
        "id": "video_extend",
        "name": "视频延长",
        "name_en": "Video Extension",
        "description": "延长已有视频的时长（wan2.7-i2v video-continuation）",
        "modes": ["video_extend"],
        "template": (
            "延续前段视频风格，{duration}秒\n"
            "承接上段画面，{continuation_desc}\n"
            "保持镜头运动一致性、光线一致性"
        ),
        "params": {"continuation_desc": "继续当前动作和场景发展"},
    },
    {
        "id": "video_edit",
        "name": "视频编辑",
        "name_en": "Video Editing",
        "description": "修改已有视频中的元素（happyhorse-1.0-video-edit）",
        "modes": ["video_edit"],
        "template": (
            "保持原视频整体结构不变\n"
            "替换/增加：{edit_target}\n"
            "替换时机：{edit_timing}\n"
            "保持不变：{keep_unchanged}"
        ),
        "params": {
            "edit_target": "",
            "edit_timing": "",
            "keep_unchanged": "背景和其他元素",
        },
    },
    {
        "id": "first_last_frame",
        "name": "首尾帧过渡",
        "name_en": "First-Last Frame Transition",
        "description": "首尾帧之间的 AI 过渡动画（wan2.7-i2v first-and-last-frame）",
        "modes": ["i2v_end"],
        "template": (
            "流畅过渡动画，{duration}秒，{ratio}\n"
            "从首帧画面自然过渡到尾帧画面\n"
            "过渡方式：{transition_style}\n"
            "镜头运动：{camera_movement}\n"
            "【声音】{sound_design}"
        ),
        "params": {
            "transition_style": "渐变",
            "camera_movement": "平移",
            "sound_design": "氛围音",
        },
    },
    {
        "id": "photo_speak",
        "name": "照片说话",
        "name_en": "Photo Speak",
        "description": "用一张人脸照片 + 一段音频驱动嘴形（wan2.2-s2v）",
        "modes": ["photo_speak"],
        "template": (
            "保持原图人脸特征不变，{duration}秒\n"
            "嘴形与音频严格同步，自然眨眼，{expression}\n"
            "背景 / 光线保持原图风格"
        ),
        "params": {"expression": "微笑、放松、专业"},
    },
    {
        "id": "video_relip",
        "name": "视频换嘴",
        "name_en": "Video Relip",
        "description": "用新音频替换原视频角色的口型（videoretalk）",
        "modes": ["video_relip"],
        "template": (
            "保持原视频镜头与人物动作不变，{duration}秒\n"
            "嘴形与新音频严格同步\n"
            "如有需要，可微调面部肌肉表情：{micro_expression}"
        ),
        "params": {"micro_expression": "自然、不夸张"},
    },
    {
        "id": "video_reface",
        "name": "视频换人",
        "name_en": "Video Reface",
        "description": "用一张人脸图替换原视频中的角色（wan2.2-animate-mix）",
        "modes": ["video_reface"],
        "template": (
            "保持原视频镜头与动作流畅，{duration}秒\n"
            "用参考人脸替换原角色，光照与肤色融合自然\n"
            "{atmosphere}"
        ),
        "params": {"atmosphere": "保留原视频氛围与色调"},
    },
    {
        "id": "pose_drive",
        "name": "图生动作",
        "name_en": "Pose Drive",
        "description": "用一段视频的姿态驱动一张静态图（wan2.2-animate-move）",
        "modes": ["pose_drive"],
        "template": (
            "把图片中的角色驱动起来，{duration}秒\n"
            "动作完全跟随参考视频，{style}\n"
            "保留原图角色的服装、面部特征"
        ),
        "params": {"style": "节奏自然、肢体协调"},
    },
    {
        "id": "avatar_compose",
        "name": "形象合成",
        "name_en": "Avatar Compose",
        "description": "多图融合 → s2v（wan2.7-image + wan2.2-s2v）",
        "modes": ["avatar_compose"],
        "template": (
            "把多张人脸 / 服装 / 场景参考图融合成一个新形象，{duration}秒\n"
            "再驱动该形象说出指定台词，嘴形对齐\n"
            "【台词】「{line}」"
        ),
        "params": {"line": "大家好，我是新的虚拟形象。"},
    },
    {
        "id": "long_tracking",
        "name": "长镜头追踪",
        "name_en": "Long Tracking Shot",
        "description": "连续追踪拍摄，可拆分镜（long_video）",
        "modes": ["t2v", "i2v", "long_video"],
        "template": (
            "电影质感，{duration}秒，{ratio}，{atmosphere}\n"
            "0-{duration}秒：一镜到底跟镜头，追踪{subject}从{start}移动至{end}，途经多个场景变化\n"
            "【声音】连续环境音 + 渐进配乐"
        ),
        "params": {
            "atmosphere": "沉浸式",
            "subject": "主角",
            "start": "起点",
            "end": "终点",
        },
    },
]


CAMERA_KEYWORDS: list[dict[str, str]] = [
    {"zh": "推镜头", "en": "Push in", "desc": "向前靠近主体"},
    {"zh": "拉镜头", "en": "Pull out", "desc": "向后远离主体"},
    {"zh": "摇镜头", "en": "Pan", "desc": "水平旋转拍摄"},
    {"zh": "移镜头", "en": "Dolly/Tracking", "desc": "平移跟随"},
    {"zh": "跟镜头", "en": "Follow", "desc": "追踪主体运动"},
    {"zh": "环绕镜头", "en": "Orbit", "desc": "绕主体旋转"},
    {"zh": "升降镜头", "en": "Crane/Jib", "desc": "垂直升降"},
    {"zh": "希区柯克变焦", "en": "Dolly Zoom", "desc": "同时推拉+变焦，恐惧/不安感"},
    {"zh": "一镜到底", "en": "One-take", "desc": "长镜头无剪辑"},
    {"zh": "手持晃动", "en": "Handheld", "desc": "手持相机的真实质感"},
    {"zh": "无人机航拍", "en": "Drone shot", "desc": "高空俯瞰，史诗感"},
    {"zh": "超广角", "en": "Ultra-wide", "desc": "夸张的纵深与边缘弯曲"},
]

ATMOSPHERE_KEYWORDS: dict[str, list[str]] = {
    "light": [
        "逆光",
        "侧光",
        "丁达尔效应",
        "伦勃朗光",
        "体积光",
        "柔光",
        "硬光",
        "霓虹反射",
    ],
    "color": [
        "暖色调",
        "冷色调",
        "赛博朋克",
        "复古胶片",
        "黑白",
        "霓虹",
        "日落色调",
        "莫兰迪色",
        "油画色",
    ],
    "texture": [
        "电影级",
        "纪录片风格",
        "油画感",
        "水墨感",
        "CG质感",
        "像素风",
        "写实",
        "皮克斯风",
    ],
    "mood": [
        "温馨",
        "紧张",
        "悬疑",
        "欢快",
        "忧伤",
        "史诗",
        "治愈",
        "恐怖",
        "浪漫",
        "孤独",
        "梦幻",
    ],
}

# 12 模式公式（plan §四 要求 expanded from seedance 的 6 模式）
MODE_FORMULAS: dict[str, str] = {
    "t2v": "主体+运动 / 背景+运动 / 镜头+运动 / 声音设计（HappyHorse 模型可直接含台词）",
    "i2v": "首帧图片特征 + 画面变化描述 + 镜头运动 + 时长",
    "i2v_end": "首帧 + 尾帧画面差异 + 中间过渡画面变化 + 镜头运动",
    "video_extend": "延续描述 + 新内容方向 + 与原视频镜头/光线一致性",
    "r2v": "多角色描述（角色 A、角色 B...）+ 互动动作 + 镜头运动 + 声音/台词",
    "video_edit": "保持不变的部分 + 替换/增加/删除元素 + 时机和位置",
    "photo_speak": "保持原图人脸 / 自然眨眼 / 嘴形对齐音频 / 微表情建议",
    "video_relip": "保持原视频镜头与动作 / 嘴形对齐新音频 / 微表情建议",
    "video_reface": "原视频动作不变 / 替换为参考人脸 / 光照肤色融合",
    "pose_drive": "图片角色驱动 / 跟随参考视频姿态 / 保留服装与面部",
    "avatar_compose": "多图融合形象 / 衍生 s2v / 台词与嘴形",
    "long_video": "故事大纲 / 分镜数量 / 每段时长 / 转场方式 / 整体风格统一",
}


__all__ = [
    "OPTIMIZE_SYSTEM_PROMPT",
    "OPTIMIZE_USER_TEMPLATE",
    "LEVEL_INSTRUCTIONS",
    "PromptOptimizeError",
    "optimize_prompt",
    "PROMPT_TEMPLATES",
    "CAMERA_KEYWORDS",
    "ATMOSPHERE_KEYWORDS",
    "MODE_FORMULAS",
]
