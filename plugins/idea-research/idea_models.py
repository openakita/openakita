"""idea-research data models.

Implements §5 (4 MODES), §13.1.A/B (12 PERSONAS + system_prompt
template), §6.4 (``TrendItem`` dataclass), §6.5 (``RANKER_WEIGHTS``),
§7.3 (PROMPTS), §15 (11 ERROR_HINTS) and the cost estimator referenced
by the ``/cost-preview`` route.

All values here are pure data — no I/O, no SDK access — so the module
can be imported from tests, the pipeline and the UI cost-preview path
without side effects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

PLUGIN_ID = "idea-research"
PLUGIN_VERSION = "0.0.1"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Mode:
    """A user-facing operation mode.

    Each mode maps 1:1 to a row of §5 in the plan and is the unit that
    the UI / tools / routes refer to when scheduling a background task.
    """

    id: str
    label_zh: str
    label_en: str
    default_input: dict[str, Any] = field(default_factory=dict)
    estimated_tokens: int = 0


@dataclass(frozen=True)
class Persona:
    """An LLM persona used by ``breakdown_url`` and ``script_remix``."""

    id: str
    name: str
    description: str
    system_prompt: str
    audience: str = ""
    tone: str = ""
    tags: list[str] = field(default_factory=list)


PlatformLiteral = Literal[
    "bilibili",
    "youtube",
    "douyin",
    "xhs",
    "ks",
    "weibo",
    "other",
]
EngineLiteral = Literal["a", "b"]
DataQualityLiteral = Literal["high", "low"]


@dataclass
class TrendItem:
    """Unified item shape produced by every collector (§6.4)."""

    id: str
    platform: PlatformLiteral
    external_id: str
    external_url: str
    title: str = ""
    author: str = ""
    author_url: str | None = None
    cover_url: str | None = None
    duration_seconds: int | None = None
    description: str | None = None
    like_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    view_count: int | None = None
    publish_at: int = 0
    fetched_at: int = 0
    engine_used: EngineLiteral = "a"
    collector_name: str = ""
    raw_payload_json: str = "{}"
    score: float = 0.0
    keywords_matched: list[str] = field(default_factory=list)
    hook_type_guess: str | None = None
    data_quality: DataQualityLiteral = "high"
    mdrm_hits: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# §5 MODES — 4 entries
# ---------------------------------------------------------------------------


MODES: list[Mode] = [
    Mode(
        id="radar_pull",
        label_zh="雷达拉榜",
        label_en="Radar pull",
        default_input={
            "platforms": ["bilibili"],
            "keywords": [],
            "time_window": "24h",
            "engine": "auto",
            "limit": 20,
            "mdrm_weighting": True,
        },
        estimated_tokens=0,
    ),
    Mode(
        id="breakdown_url",
        label_zh="单条拆解",
        label_en="Breakdown URL",
        default_input={
            "url": "",
            "persona": "小红书运营专家",
            "enable_comments": True,
            "asr_backend": "auto",
            "frame_strategy": "hybrid",
            "write_to_mdrm": True,
        },
        estimated_tokens=10_000,
    ),
    Mode(
        id="compare_accounts",
        label_zh="对标分析",
        label_en="Compare accounts",
        default_input={
            "account_urls": [],
            "window": "30d",
            "max_videos_per_account": 20,
        },
        estimated_tokens=8_000,
    ),
    Mode(
        id="script_remix",
        label_zh="脚本改写",
        label_en="Script remix",
        default_input={
            "trend_item_id": "",
            "my_persona": "小红书运营专家",
            "my_brand_keywords": [],
            "target_duration_seconds": 60,
            "num_variants": 3,
            "target_platform": "douyin",
            "use_mdrm_hints": True,
        },
        estimated_tokens=4_000,
    ),
]
MODES_BY_ID: dict[str, Mode] = {m.id: m for m in MODES}


def get_mode(mode_id: str) -> Mode:
    """Return the matching ``Mode`` or raise ``KeyError``."""

    try:
        return MODES_BY_ID[mode_id]
    except KeyError as exc:
        raise KeyError(f"Unknown mode {mode_id!r}; valid: {sorted(MODES_BY_ID)}") from exc


# ---------------------------------------------------------------------------
# §13.1.A / §13.1.B — 12 built-in PERSONAS
# ---------------------------------------------------------------------------


_PERSONA_TEMPLATE = (
    "你扮演 {name}（{audience}的{description}）。\n\n"
    "你最擅长：\n"
    "- {speciality_1}\n"
    "- {speciality_2}\n"
    "- {speciality_3}\n\n"
    "回答时务必：\n"
    "1. 用 {audience_short} 熟悉的话语和场景类比\n"
    "2. 避免 {avoid_words}\n"
    "3. 拆解时先指出「为什么火」再给「我能怎么用」\n"
    "4. 脚本生成必须给出具体可拍的 b_roll 提示和镜头时长\n\n"
    "输出语气：{tone}。"
)


def _persona(
    pid: str,
    name: str,
    description: str,
    audience: str,
    audience_short: str,
    avoid_words: str,
    tone: str,
    specialities: tuple[str, str, str],
    tags: tuple[str, ...] = (),
) -> Persona:
    prompt = _PERSONA_TEMPLATE.format(
        name=name,
        description=description,
        audience=audience,
        audience_short=audience_short,
        avoid_words=avoid_words,
        tone=tone,
        speciality_1=specialities[0],
        speciality_2=specialities[1],
        speciality_3=specialities[2],
    )
    return Persona(
        id=pid,
        name=name,
        description=description,
        system_prompt=prompt,
        audience=audience,
        tone=tone,
        tags=list(tags),
    )


PERSONAS: list[Persona] = [
    _persona(
        "xhs_ops",
        "小红书运营专家",
        "笔记选题、封面文案、热点追踪",
        "25-35 女性、白领、宝妈",
        "宝妈白领",
        "行业黑话如「转化漏斗 / GMV」",
        "亲切利落，多用感叹号和「姐妹们」称呼",
        (
            "用「痛点+反差+利益承诺」三段式起标题",
            "把干货塞进「姐妹们今天发现一个」式开场",
            "借势小红书当天热搜话题钩子",
        ),
        ("xhs", "ops"),
    ),
    _persona(
        "douyin_director",
        "抖音爆款编导",
        "短视频钩子、节奏、转场",
        "18-30 全人群",
        "抖音原生用户",
        "晦涩术语，例如「叙事弧 / 三幕剧」要替换成「前 3 秒 / 中间 / 结尾」",
        "节奏快，多用短句和动词开头",
        (
            "黄金 3 秒钩子 7 类（悬念/痛点/反差/数据冲击/疑问/情绪/利益）",
            "0.5 秒一镜的快剪节奏",
            "BGM 卡点 + 字幕高光强化",
        ),
        ("douyin",),
    ),
    _persona(
        "bili_kol",
        "B站知识博主",
        "深度选题、长视频结构、弹幕互动",
        "18-35 学生、技术爱好者",
        "B 站观众",
        "营销话术，例如「赋能 / 闭环」要替换成「实测 / 流程图」",
        "理性专业，但保留少量梗与弹幕互动",
        (
            "5-15 分钟知识视频的章节化结构",
            "「先抛结论 → 再给推导 → 留弹幕互动钩子」",
            "高质量信息源引用与排版",
        ),
        ("bilibili",),
    ),
    _persona(
        "youtube_seo",
        "YouTube SEO 专家",
        "标题、缩略图、关键词、描述",
        "全球英语用户",
        "YouTube creators",
        "中文化营销词，必须英文化",
        "Concise, action-oriented, keyword-front-loaded",
        (
            "CTR-optimized title patterns",
            "Search-intent matching across description + tags",
            "A/B testable thumbnail prompts",
        ),
        ("youtube", "seo"),
    ),
    _persona(
        "wechat_emotion",
        "视频号情感博主",
        "共鸣文案、生活金句、家庭话题",
        "30-50 微信用户",
        "微信熟人圈",
        "网络梗、Z 世代缩写",
        "温暖真诚，多用反问与排比",
        (
            "家庭/婚恋/亲子场景的共鸣金句",
            "故事化开头与升华式结尾",
            "适合长辈转发的安全主题选择",
        ),
        ("wechat", "emotion"),
    ),
    _persona(
        "course_owner",
        "知识付费课程主理人",
        "痛点切入、价值阶梯、转化漏斗",
        "25-40 职场进阶人群",
        "职场进阶受众",
        "口语过散漫，必须保留专业骨架",
        "权威克制，强调结果与可复制性",
        (
            "AIDA / FAB 等成熟营销框架",
            "免费引流到付费课的内容阶梯设计",
            "打卡 + 复盘 + 案例库 三件套",
        ),
        ("course",),
    ),
    _persona(
        "ecom_anchor",
        "电商带货主播",
        "选品逻辑、话术、转化",
        "25-45 消费决策者",
        "直播间观众",
        "纯学术语言",
        "热情有节奏，强调「现在 / 限时 / 福利」",
        (
            "产品卖点的 3 秒抓人话术",
            "对比同类品的拆解维度（价格/参数/场景）",
            "限时机制 + 库存焦虑制造",
        ),
        ("ecommerce", "live"),
    ),
    _persona(
        "mom_baby",
        "母婴亲子博主",
        "育儿干货、产品测评、家庭场景",
        "25-40 妈妈群体",
        "新手 / 二胎妈妈",
        "高 GI 营销词、过度专家口吻",
        "理性又共情，先共鸣再给方案",
        (
            "0-3 / 3-6 / 6-12 月分阶段干货",
            "成分党测评话术（成分表 + 场景）",
            "二孩家庭与单孩家庭的差异化建议",
        ),
        ("mom", "baby"),
    ),
    _persona(
        "beauty_review",
        "美妆护肤测评师",
        "成分分析、对比测评、踩雷指南",
        "18-35 女性",
        "美妆爱好者",
        "粉饰式好评，必须给出明确缺点",
        "客观锐利，敢于点名踩雷",
        (
            "成分表逐条解读 + 浓度推算",
            "横评矩阵（同价位 3-5 款）",
            "「亲测踩雷」式反向种草",
        ),
        ("beauty",),
    ),
    _persona(
        "tech_review",
        "数码科技博主",
        "新品速评、参数对比、使用场景",
        "20-40 男性、科技爱好者",
        "数码玩家",
        "运营黑话「赋能/闭环/抓手」",
        "专业严谨但不装，敢于点名缺点",
        (
            "跑分对比矩阵",
            "「上手 24h 真实体验」式开场",
            "横评而不是单评（参考 What Gear 范式）",
        ),
        ("tech",),
    ),
    _persona(
        "food_explorer",
        "美食探店博主",
        "视觉化呈现、地标打卡、情绪渲染",
        "全人群、本地生活",
        "本地生活观众",
        "纯文字罗列、缺乏视觉化描写",
        "口齿生津，画面感强",
        (
            "「色 / 香 / 味 / 故事」四维拆解",
            "地标打卡型分镜与转场",
            "客单价 / 排队时长 / 适合人群明示",
        ),
        ("food",),
    ),
    _persona(
        "fin_pundit",
        "财经投资评论员",
        "时事解读、数据可视化、风险提示",
        "30-50 投资者、白领",
        "投资圈受众",
        "煽动性结论、未注明出处的数据",
        "克制专业，必带「风险提示」收尾",
        (
            "宏观 → 行业 → 标的 三层逻辑链",
            "可视化图表（K 线、利差、估值带）",
            "风险声明与免责语样板",
        ),
        ("finance",),
    ),
]
PERSONAS_BY_ID: dict[str, Persona] = {p.id: p for p in PERSONAS}
PERSONAS_BY_NAME: dict[str, Persona] = {p.name: p for p in PERSONAS}


def get_persona(name_or_id: str) -> Persona | None:
    """Lookup a persona by either ``id`` or human-readable ``name``."""

    return PERSONAS_BY_ID.get(name_or_id) or PERSONAS_BY_NAME.get(name_or_id)


# ---------------------------------------------------------------------------
# §7.3 PROMPTS
# ---------------------------------------------------------------------------


PROMPTS: dict[str, str] = {
    "STRUCTURE_PROMPT": (
        "你是爆款视频拆解专家。基于以下信息，输出严格 JSON：\n\n"
        "【视频元数据】\n标题：{title}\n作者：{author}\n时长：{duration}s\n"
        "平台：{platform}\n\n"
        "【ASR 转写片段】（数组）\n{transcript_segments_json}\n\n"
        "【视觉关键帧描述】（按时间序）\n{frames_descriptions_json}\n\n"
        "请输出严格 JSON，schema：\n"
        "{{\n"
        '  "hook": {{"type": "悬念｜痛点｜数据冲击｜反差｜疑问｜情绪｜利益承诺",'
        ' "text": "...", "time_range": [0, 8]}},\n'
        '  "body": [{{"topic": "...", "time_range": [8, 30],'
        ' "key_quote": "..."}}],\n'
        '  "cta": {{"text": "...", "time_range": [55, 60]}},\n'
        '  "keywords": [{{"word": "...", "freq": 3, "weight": 0.8}}],\n'
        '  "bgm": {{"fingerprint": "", "suggested_match": ""}}\n'
        "}}\n\n只输出 JSON，不要解释、不要 markdown 代码块。"
    ),
    "COMMENT_SUMMARY_PROMPT": (
        "你是评论区舆情分析师。基于以下 top 100 评论，输出严格 JSON：\n\n"
        "【评论列表】\n{comments_json}\n\n"
        "请输出严格 JSON：\n"
        "{{\n"
        '  "top_emotions": [{{"emotion": "正面｜负面｜疑问｜搞笑｜共鸣",'
        ' "ratio": 0.45, "examples": ["..."]}}],\n'
        '  "common_questions": ["..."],\n'
        '  "controversies": ["..."],\n'
        '  "audience_persona_guess": "..."\n'
        "}}\n只输出 JSON。"
    ),
    "PERSONA_TAKEAWAYS_PROMPT": (
        "你扮演 {persona}。基于以下完整拆解结果，写出 5 条「我能从这条爆款"
        "学到什么」的可执行 takeaways（每条 ≤ 60 字）：\n\n"
        "【拆解结果】\n{breakdown_json}\n\n"
        '输出严格 JSON：{{"persona_takeaways": ["...", "...", "...", "...",'
        ' "..."]}}'
    ),
    "SCRIPT_REMIX_PROMPT": (
        "你是 {my_persona}。请基于以下选题和我的品牌定位，生成"
        " {num_variants} 版可执行脚本：\n\n"
        "【选题钩子】{hook}\n【选题主体】{body_outline}\n"
        "【目标平台】{target_platform}\n【我的品牌关键词】{brand_keywords}\n"
        "【目标时长】{target_duration_seconds}s\n\n"
        "【MDRM 检索的相似历史成功 hook（top 3）】（仅当 use_mdrm_hints=true"
        " 时注入）\n{mdrm_inspirations_json}\n\n"
        "每版输出 schema：\n"
        "{{\n"
        '  "title": "...",\n  "hook_line": "前 3s 必读",\n'
        '  "body_outline": [{{"section": "...", "duration_s": 10,'
        ' "voiceover": "...", "b_roll_hint": "..."}}],\n'
        '  "cta_line": "...",\n  "hashtags": ["#..."],\n'
        '  "thumbnail_prompt": "..."\n'
        "}}\n"
        '返回严格 JSON：{{"variants": [...]}}'
    ),
}


# ---------------------------------------------------------------------------
# §6.5 RANKER_WEIGHTS  +  §6.5 score()
# ---------------------------------------------------------------------------


RANKER_WEIGHTS: dict[str, Any] = {
    "interaction_exp": 0.6,
    "time_decay_half_life_h": 24.0,
    "keyword_match_coeff": 0.5,
    "mdrm_hit_coeff": 0.2,
    "platform": {
        "bilibili": 1.0,
        "youtube": 0.9,
        "douyin": 1.1,
        "xhs": 1.0,
        "ks": 0.8,
        "weibo": 0.7,
        "other": 0.6,
    },
}


def compute_interaction_rate(
    *,
    like: int | None,
    comment: int | None,
    share: int | None,
    view: int | None,
) -> float:
    """Engagement = (likes + 3·comments + 5·shares) / max(view, 1)."""

    likes = max(int(like or 0), 0)
    comments = max(int(comment or 0), 0)
    shares = max(int(share or 0), 0)
    views = max(int(view or 0), 1)
    return (likes + 3 * comments + 5 * shares) / views


def compute_time_decay(
    *,
    fetched_at: int,
    publish_at: int,
    half_life_h: float | None = None,
) -> float:
    """Exponential decay weighted by hours since publish (capped to 1.0)."""

    half_life = float(half_life_h or RANKER_WEIGHTS["time_decay_half_life_h"])
    age_h = max((fetched_at - publish_at) / 3600.0, 0.0)
    return float(math.exp(-age_h / max(half_life, 0.1)))


def score_trend_item(
    item: TrendItem,
    keywords: list[str],
    *,
    weights: dict[str, Any] | None = None,
) -> float:
    """Apply the §6.5 ranking formula (without the MDRM hit boost)."""

    w = weights or RANKER_WEIGHTS
    rate = compute_interaction_rate(
        like=item.like_count,
        comment=item.comment_count,
        share=item.share_count,
        view=item.view_count,
    )
    decay = compute_time_decay(
        fetched_at=item.fetched_at,
        publish_at=item.publish_at,
    )
    matched = sum(1 for kw in keywords if kw and kw.lower() in (item.title or "").lower())
    keyword_factor = 1.0 + float(w["keyword_match_coeff"]) * matched
    platform_weight = float(w["platform"].get(item.platform, w["platform"]["other"]))
    base = (rate ** float(w["interaction_exp"])) * decay * keyword_factor * platform_weight
    mdrm_factor = 1.0 + float(w["mdrm_hit_coeff"]) * len(item.mdrm_hits)
    return float(base * mdrm_factor)


# ---------------------------------------------------------------------------
# §5 PRICE_TABLE  +  estimate_cost
# ---------------------------------------------------------------------------


PRICE_TABLE: dict[str, dict[str, float]] = {
    "qwen-vl-max": {"per_image": 0.02},
    "qwen-max": {"input_per_1k": 0.04, "output_per_1k": 0.12},
    "qwen-plus": {"input_per_1k": 0.0008, "output_per_1k": 0.002},
    "paraformer-v2": {"per_minute": 0.024},
    "faster-whisper-local": {"per_minute": 0.0},
}


def _llm_cost(model: str, *, in_tokens: int, out_tokens: int) -> float:
    table = PRICE_TABLE.get(model, {})
    in_price = float(table.get("input_per_1k", 0.0))
    out_price = float(table.get("output_per_1k", 0.0))
    return (in_tokens / 1000.0) * in_price + (out_tokens / 1000.0) * out_price


def _vlm_cost(num_frames: int) -> float:
    return num_frames * float(PRICE_TABLE["qwen-vl-max"]["per_image"])


def _asr_cost(duration_s: float, backend: str) -> float:
    if backend == "local":
        rate = float(PRICE_TABLE["faster-whisper-local"]["per_minute"])
    else:
        rate = float(PRICE_TABLE["paraformer-v2"]["per_minute"])
    return rate * (duration_s / 60.0)


def estimate_cost(mode: str, params: dict[str, Any]) -> dict[str, Any]:
    """Estimate the CNY cost of a job before running it.

    Returns a dict with ``cost_cny`` (rounded float) and ``breakdown``
    (per-component contribution); never raises — unknown modes just
    return zeros so the route can still respond 200.
    """

    breakdown: dict[str, float] = {}
    if mode == "radar_pull":
        breakdown["collect"] = 0.0
    elif mode == "breakdown_url":
        num_frames = int(params.get("num_frames_estimate", 30))
        duration_s = float(params.get("duration_seconds_estimate", 90.0))
        backend = str(params.get("asr_backend", "auto"))
        if backend == "auto":
            backend = "local" if duration_s <= 600 else "cloud"
        breakdown["asr"] = round(_asr_cost(duration_s, backend), 4)
        breakdown["vlm_frames"] = round(_vlm_cost(num_frames), 4)
        breakdown["structure_llm"] = round(
            _llm_cost("qwen-max", in_tokens=3000, out_tokens=1200),
            4,
        )
        breakdown["comments_llm"] = round(
            _llm_cost("qwen-plus", in_tokens=2400, out_tokens=600),
            4,
        )
        breakdown["persona_llm"] = round(
            _llm_cost("qwen-plus", in_tokens=1600, out_tokens=400),
            4,
        )
    elif mode == "compare_accounts":
        n_accounts = int(params.get("account_count", 3))
        breakdown["fetch"] = 0.0
        breakdown["aggregate_llm"] = round(
            _llm_cost(
                "qwen-max",
                in_tokens=2000 + 600 * n_accounts,
                out_tokens=900,
            ),
            4,
        )
    elif mode == "script_remix":
        n_variants = int(params.get("num_variants", 3))
        breakdown["script_llm"] = round(
            _llm_cost(
                "qwen-max",
                in_tokens=2000,
                out_tokens=900 * n_variants,
            ),
            4,
        )
    else:
        breakdown["unknown_mode"] = 0.0

    total = round(sum(breakdown.values()), 4)
    return {
        "cost_cny": total,
        "breakdown": breakdown,
        "mode": mode,
        "params": params,
    }


# ---------------------------------------------------------------------------
# §15 ERROR_HINTS — 11 categories
# ---------------------------------------------------------------------------


ERROR_HINTS: dict[str, dict[str, str]] = {
    "network": {
        "zh": "网络异常或目标平台无法访问，请检查网络/代理后重试",
        "en": "Network error or platform unreachable. Check network/proxy and retry.",
    },
    "timeout": {
        "zh": "请求超时，请稍后重试或减小数据量",
        "en": "Request timeout. Retry later or reduce batch size.",
    },
    "auth": {
        "zh": "鉴权失败，请检查 API Key 或重新导入 cookies",
        "en": "Auth failed. Check API key or re-import cookies.",
    },
    "quota": {
        "zh": "配额或余额不足，请充值或更换 key",
        "en": "Quota or balance exhausted. Top up or rotate key.",
    },
    "moderation": {
        "zh": "内容被平台审核拦截，请调整输入",
        "en": "Content blocked by moderation. Adjust input.",
    },
    "rate_limit": {
        "zh": "请求过于频繁，已自动 backoff，请稍候",
        "en": "Rate limited. Auto-backoff in progress.",
    },
    "dependency": {
        "zh": "缺少系统依赖（如 yt-dlp / ffmpeg / playwright），请按提示安装",
        "en": "Missing system dependency (e.g. yt-dlp / ffmpeg / playwright). Install per hint.",
    },
    "format": {
        "zh": "数据格式异常，请检查输入或联系反馈",
        "en": "Bad data format. Check input or report.",
    },
    "unknown": {
        "zh": "未知异常，详情见日志",
        "en": "Unknown error. See logs.",
    },
    "cookies_expired": {
        "zh": "cookies 已过期，请到 Settings → 数据源 重新导入",
        "en": "Cookies expired. Re-import in Settings → Data Sources.",
    },
    "crawler_blocked": {
        "zh": "平台风控触发，建议更换 cookies 或切回 API 引擎 A",
        "en": "Anti-bot triggered. Rotate cookies or fall back to Engine A.",
    },
}


def hint_for(error_kind: str) -> dict[str, str]:
    """Return the bilingual hint for an ``error_kind`` (fallback to unknown)."""

    return ERROR_HINTS.get(error_kind) or ERROR_HINTS["unknown"]


__all__ = [
    "ERROR_HINTS",
    "MODES",
    "MODES_BY_ID",
    "Mode",
    "PERSONAS",
    "PERSONAS_BY_ID",
    "PERSONAS_BY_NAME",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "PRICE_TABLE",
    "PROMPTS",
    "Persona",
    "RANKER_WEIGHTS",
    "TrendItem",
    "compute_interaction_rate",
    "compute_time_decay",
    "estimate_cost",
    "get_mode",
    "get_persona",
    "hint_for",
    "score_trend_item",
]
