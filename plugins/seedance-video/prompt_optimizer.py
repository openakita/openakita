"""Prompt optimization engine — LLM-powered refinement of user prompts into
professional Seedance time-axis format with camera language and sound design."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

OPTIMIZE_SYSTEM_PROMPT = """你是 Seedance 2.0 提示词专家。根据用户的简短描述，生成专业的视频生成提示词。

## 提示词格式规范
[风格]风格，[时长]秒，[比例]，[氛围]
0-3秒：[镜头运动]，[画面描述]
3-6秒：...
...
【声音】配乐风格 + 音效
【参考】@图片1 用途，@图片2 用途

## 镜头语言速查
推镜头/拉镜头/摇镜头/移镜头/跟镜头/环绕镜头/升降镜头/希区柯克变焦/一镜到底/手持晃动

## 氛围关键词
光影: 逆光、侧光、丁达尔效应、伦勃朗光、体积光
色调: 暖色调、冷色调、赛博朋克、复古胶片、黑白
质感: 电影级、纪录片风格、油画感、水墨感、动画、写实

## 注意事项
- 提示词建议 80-200 字，不超过 300 字
- 使用时间轴格式让镜头有节奏感
- 声音设计让视频更有氛围
- 镜头语言让画面更专业
"""

OPTIMIZE_USER_TEMPLATE = """## 用户输入
{user_prompt}

## 当前参数
模式: {mode}, 时长: {duration}秒, 比例: {ratio}
已上传素材: {asset_summary}

## 优化级别: {level}
{level_instruction}

请生成适合 Seedance 2.0 的专业提示词。"""

LEVEL_INSTRUCTIONS = {
    "light": "轻度润色：保留原意，优化措辞和结构，补充镜头语言。",
    "professional": "专业重写：生成完整的时间轴格式提示词，包含具体的镜头语言和声音设计。",
    "storyboard": "分镜脚本：输出完整的分镜+提示词+声音设计，每个镜头细致描述。",
}


async def optimize_prompt(
    brain: Any,
    user_prompt: str,
    mode: str = "t2v",
    duration: int = 5,
    ratio: str = "16:9",
    asset_summary: str = "无",
    level: str = "professional",
) -> str:
    """Call the host LLM to refine a user prompt into Seedance format."""
    level_instruction = LEVEL_INSTRUCTIONS.get(level, LEVEL_INSTRUCTIONS["professional"])

    user_msg = OPTIMIZE_USER_TEMPLATE.format(
        user_prompt=user_prompt,
        mode=mode,
        duration=duration,
        ratio=ratio,
        asset_summary=asset_summary,
        level=level,
        level_instruction=level_instruction,
    )

    try:
        if hasattr(brain, "think"):
            result = await brain.think(
                user_message=user_msg,
                system_prompt=OPTIMIZE_SYSTEM_PROMPT,
            )
            return result.get("content", "") if isinstance(result, dict) else str(result)
        elif hasattr(brain, "chat"):
            result = await brain.chat(
                messages=[
                    {"role": "system", "content": OPTIMIZE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ]
            )
            return result.get("content", "") if isinstance(result, dict) else str(result)
        else:
            logger.warning("Brain object has no think() or chat() method")
            return user_prompt
    except Exception as e:
        logger.error("Prompt optimization failed: %s", e)
        return user_prompt


# ── Built-in prompt templates ──

PROMPT_TEMPLATES = [
    {
        "id": "narrative_story",
        "name": "叙事故事",
        "name_en": "Narrative Story",
        "description": "适合有情节发展的故事视频",
        "modes": ["t2v", "i2v", "multimodal"],
        "template": "电影级画质，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：{shot1}，{scene1}\n{t1}-{t2}秒：{shot2}，{scene2}\n{t2}-{duration}秒：{shot3}，{scene3}\n【声音】{music} + {sfx}",
        "params": {"atmosphere": "温暖治愈", "shot1": "中景", "shot2": "特写", "shot3": "远景"},
    },
    {
        "id": "product_showcase",
        "name": "产品展示",
        "name_en": "Product Showcase",
        "description": "产品旋转、细节展示、功能演示",
        "modes": ["t2v", "i2v", "multimodal"],
        "template": "商业广告风格，{duration}秒，{ratio}，干净明亮\n0-{t1}秒：慢速环绕镜头，产品全景展示\n{t1}-{t2}秒：推镜头至特写，展示细节和材质\n{t2}-{duration}秒：拉镜头，产品在场景中使用",
        "params": {},
    },
    {
        "id": "character_action",
        "name": "角色动作",
        "name_en": "Character Action",
        "description": "角色的动态动作场景",
        "modes": ["t2v", "i2v", "multimodal"],
        "template": "动作电影风格，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：{shot1}，角色准备动作\n{t1}-{t2}秒：跟镜头，角色执行核心动作\n{t2}-{duration}秒：慢动作特写，定格瞬间\n【声音】紧张鼓点 + 动作音效",
        "params": {"atmosphere": "紧张刺激"},
    },
    {
        "id": "landscape_travel",
        "name": "风景旅拍",
        "name_en": "Landscape Travel",
        "description": "自然风光、城市风景、旅行记录",
        "modes": ["t2v", "i2v"],
        "template": "纪录片风格，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：升镜头/航拍，壮阔全景\n{t1}-{t2}秒：移镜头，穿过场景细节\n{t2}-{duration}秒：延时摄影，光线变化\n【声音】自然环境音 + 轻音乐",
        "params": {"atmosphere": "宁静治愈"},
    },
    {
        "id": "emotional_conflict",
        "name": "情感冲突",
        "name_en": "Emotional Conflict",
        "description": "情感张力、戏剧冲突",
        "modes": ["t2v", "multimodal"],
        "template": "电影质感，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：特写，角色面部表情变化\n{t1}-{t2}秒：摇镜头，展示周围环境压力\n{t2}-{duration}秒：希区柯克变焦，情感爆发\n【声音】弦乐渐强 + 心跳声",
        "params": {"atmosphere": "压抑紧张"},
    },
    {
        "id": "vlog_narrator",
        "name": "口播类",
        "name_en": "Vlog Narrator",
        "description": "口播、讲解、自媒体内容",
        "modes": ["t2v", "i2v"],
        "template": "自媒体风格，{duration}秒，{ratio}，轻松自然\n0-{t1}秒：中景正面，人物开始讲话\n{t1}-{t2}秒：画中画 + 图文插入\n{t2}-{duration}秒：回到人物镜头，总结\n【声音】清晰人声 + 轻快BGM",
        "params": {},
    },
    {
        "id": "music_sync",
        "name": "音乐卡点",
        "name_en": "Music Sync",
        "description": "音乐节奏同步的视觉效果",
        "modes": ["t2v", "multimodal"],
        "template": "MV风格，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：节奏预热，慢动作场景\n{t1}-{t2}秒：卡点切换，快速剪辑\n{t2}-{duration}秒：高潮释放，全景慢放\n【声音】{music}",
        "params": {"atmosphere": "酷炫动感", "music": "电子节拍"},
    },
    {
        "id": "video_extend",
        "name": "视频延长",
        "name_en": "Video Extension",
        "description": "延长已有视频的时长",
        "modes": ["extend"],
        "template": "延续前段视频风格，{duration}秒\n承接上段画面，{continuation_desc}\n保持镜头运动一致性",
        "params": {"continuation_desc": "继续当前动作和场景发展"},
    },
    {
        "id": "video_edit",
        "name": "视频编辑",
        "name_en": "Video Editing",
        "description": "修改已有视频中的元素",
        "modes": ["edit"],
        "template": "保持原视频整体结构不变\n替换/增加：{edit_target}\n替换时机：{edit_timing}\n保持不变：{keep_unchanged}",
        "params": {"edit_target": "", "edit_timing": "", "keep_unchanged": "背景和其他元素"},
    },
    {
        "id": "war_scene",
        "name": "战争场景",
        "name_en": "War Scene",
        "description": "战争、战斗、军事场景",
        "modes": ["t2v", "multimodal"],
        "template": "战争电影风格，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：远景航拍，战场全貌\n{t1}-{t2}秒：手持晃动，近距离战斗\n{t2}-{duration}秒：慢动作，爆炸/烟尘定格\n【声音】爆炸 + 枪声 + 战争配乐",
        "params": {"atmosphere": "紧张宏大"},
    },
    {
        "id": "mockumentary",
        "name": "伪纪录片",
        "name_en": "Mockumentary",
        "description": "仿纪录片风格叙事",
        "modes": ["t2v", "i2v"],
        "template": "纪录片画质，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：访谈式中景镜头\n{t1}-{t2}秒：B-roll 插入画面\n{t2}-{duration}秒：回到主体叙事\n【声音】旁白 + 环境音",
        "params": {"atmosphere": "真实质感"},
    },
    {
        "id": "space_tour",
        "name": "空间漫游",
        "name_en": "Space Tour",
        "description": "穿越空间的长镜头漫游",
        "modes": ["t2v", "multimodal"],
        "template": "CG质感，{duration}秒，{ratio}，{atmosphere}\n0-{duration}秒：一镜到底，从{start_point}缓缓穿越至{end_point}，镜头平滑移动，途经各种场景细节\n【声音】空灵电子音乐 + 环境混响",
        "params": {"atmosphere": "科幻奇幻", "start_point": "入口", "end_point": "深处"},
    },
    {
        "id": "product_motion",
        "name": "产品动效",
        "name_en": "Product Motion",
        "description": "产品动态展示、组装拆解",
        "modes": ["t2v", "i2v"],
        "template": "商业CG风格，{duration}秒，{ratio}，简洁干净白色背景\n0-{t1}秒：产品零件飞入组装\n{t1}-{t2}秒：环绕展示完整产品\n{t2}-{duration}秒：产品功能演示动画\n【声音】科技感音效 + 简洁配乐",
        "params": {},
    },
    {
        "id": "long_tracking",
        "name": "长镜头追踪",
        "name_en": "Long Tracking Shot",
        "description": "连续追踪拍摄",
        "modes": ["t2v", "i2v"],
        "template": "电影质感，{duration}秒，{ratio}，{atmosphere}\n0-{duration}秒：一镜到底跟镜头，追踪{subject}从{start}移动至{end}，途经多个场景变化\n【声音】连续环境音 + 渐进配乐",
        "params": {"atmosphere": "沉浸式", "subject": "主角", "start": "起点", "end": "终点"},
    },
    {
        "id": "character_battle",
        "name": "角色对战",
        "name_en": "Character Battle",
        "description": "两个角色的对抗场景",
        "modes": ["t2v", "multimodal"],
        "template": "动作片风格，{duration}秒，{ratio}，{atmosphere}\n0-{t1}秒：对峙远景，双方蓄势\n{t1}-{t2}秒：快速剪辑，交手过招\n{t2}-{duration}秒：慢动作定格，胜负已分\n【声音】紧张配乐 + 打击音效",
        "params": {"atmosphere": "热血激烈"},
    },
    {
        "id": "first_last_frame",
        "name": "首尾帧过渡",
        "name_en": "First-Last Frame Transition",
        "description": "首尾帧之间的 AI 过渡动画",
        "modes": ["i2v_end"],
        "template": "流畅过渡动画，{duration}秒，{ratio}\n从首帧画面自然过渡到尾帧画面\n过渡方式：{transition_style}\n镜头运动：{camera_movement}\n【声音】{sound_design}",
        "params": {"transition_style": "渐变", "camera_movement": "平移", "sound_design": "氛围音"},
    },
]


CAMERA_KEYWORDS = [
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
]

ATMOSPHERE_KEYWORDS = {
    "light": ["逆光", "侧光", "丁达尔效应", "伦勃朗光", "体积光", "柔光", "硬光"],
    "color": ["暖色调", "冷色调", "赛博朋克", "复古胶片", "黑白", "霓虹", "日落色调"],
    "texture": ["电影级", "纪录片风格", "油画感", "水墨感", "CG质感", "像素风", "写实"],
    "mood": ["温馨", "紧张", "悬疑", "欢快", "忧伤", "史诗", "治愈", "恐怖", "浪漫"],
}

MODE_FORMULAS = {
    "t2v": "主体+运动 / 背景+运动 / 镜头+运动",
    "i2v": "参考图片特征 + 画面变化描述 + 镜头运动",
    "i2v_end": "首帧到尾帧的过渡描述 + 中间画面变化 + 镜头运动",
    "multimodal": "参考「图片N」中的特征 + 参考「视频N」的运镜 + 音色→角色说:台词",
    "edit": "保持不变的部分 + 替换/增加/删除的元素 + 时机和位置",
    "extend": "延续描述 + 新内容方向 + 过渡方式",
}
