"""manga-studio data layer — visual styles, voices, pricing, cost, error hints.

Pure data + pure functions. No I/O, no SDK import. Imported by the
client / pipeline / plugin layers and by tests with zero side effects.

Design notes
------------
- ``VISUAL_STYLES`` is the single source of truth for the manga / anime
  art directions the user can pick. Each ``VisualStyleSpec`` carries its
  own positive + negative prompt fragments; ``prompt_assembler`` (Phase
  2.5) consumes them when composing the per-panel image prompt so we
  never have to keep prompt fragments in two places.
- ``PRICE_TABLE`` keeps the *officially documented* unit prices for the
  three direct-API vendors (Volcengine Seedance, DashScope wan2.7-image,
  DashScope cosyvoice-v2) in one place. Tests freeze the table so a
  remote price drift never silently shifts the displayed cost.
- ``estimate_cost`` returns a ``CostPreview`` that is JSON-serialisable
  end-to-end. Money is shown as ``¥{:.2f}`` — no "milk-tea translation".
- ``ERROR_HINTS`` covers every ``ERROR_KIND_*`` constant exported by
  ``manga_inline.vendor_client`` plus three manga-only kinds
  (``moderation_face`` / ``content_violation`` / ``dependency``). Pixelle
  C2 — generic "Generation Failed" is banned.
- All copy is bilingual (zh / en). The UI picks the language by reading
  ``navigator.language`` so users on en-US still get a useful hint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, TypedDict

# ─── Visual styles ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class VisualStyleSpec:
    """One manga / anime art direction (selectable in the Studio tab)."""

    id: str
    label_zh: str
    label_en: str
    description_zh: str
    description_en: str
    prompt_fragment: str  # appended to every panel prompt
    negative_prompt: str  # appended to the negative prompt
    sample_url: str = ""  # optional preview image (relative to UI dist/)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label_zh,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "description_zh": self.description_zh,
            "description_en": self.description_en,
            "prompt_fragment": self.prompt_fragment,
            "negative_prompt": self.negative_prompt,
            "sample_url": self.sample_url,
        }


VISUAL_STYLES: tuple[VisualStyleSpec, ...] = (
    VisualStyleSpec(
        id="shonen",
        label_zh="少年热血",
        label_en="Shonen",
        description_zh="经典少年漫风格：动感线条、强对比、爆发感",
        description_en="Classic shonen — bold linework, high contrast, kinetic",
        prompt_fragment=(
            "shonen manga style, bold ink lines, dynamic action poses, "
            "high contrast cel shading, screentone shading, vibrant"
        ),
        negative_prompt=("photorealistic, soft watercolor, low contrast, blurry"),
    ),
    VisualStyleSpec(
        id="shoujo",
        label_zh="少女唯美",
        label_en="Shoujo",
        description_zh="少女漫风格：柔和光影、闪光、细腻情绪",
        description_en="Shoujo — soft lighting, sparkles, delicate emotion",
        prompt_fragment=(
            "shoujo manga style, sparkles, soft pastel palette, large "
            "expressive eyes, flowing hair, delicate emotion"
        ),
        negative_prompt="dark gritty, harsh shadows, photorealistic",
    ),
    VisualStyleSpec(
        id="seinen",
        label_zh="青年写实",
        label_en="Seinen",
        description_zh="青年漫写实风：细致面部、阴影丰富、成熟主题",
        description_en="Seinen — detailed faces, rich shadows, mature themes",
        prompt_fragment=(
            "seinen manga style, detailed realistic faces, rich crosshatching, "
            "moody atmosphere, mature themes, cinematic framing"
        ),
        negative_prompt="cartoonish, chibi, cute, oversimplified",
    ),
    VisualStyleSpec(
        id="cyberpunk",
        label_zh="赛博朋克",
        label_en="Cyberpunk",
        description_zh="赛博朋克：霓虹灯、雨夜、机械义体",
        description_en="Cyberpunk — neon, rain-soaked nights, cybernetics",
        prompt_fragment=(
            "cyberpunk manga, neon-lit cityscape, rain-soaked streets, "
            "cybernetic implants, holographic ads, blade-runner palette"
        ),
        negative_prompt="rural, sunny, medieval, low-tech",
    ),
    VisualStyleSpec(
        id="chibi",
        label_zh="Q 版可爱",
        label_en="Chibi",
        description_zh="Q 版风格：大头小身、夸张表情、明亮色彩",
        description_en="Chibi — oversized heads, exaggerated expressions",
        prompt_fragment=(
            "chibi style, super-deformed proportions, oversized expressive eyes, "
            "tiny body, bright candy palette, cute"
        ),
        negative_prompt="realistic proportions, dark, gritty, mature",
    ),
    VisualStyleSpec(
        id="watercolor",
        label_zh="水彩绘本",
        label_en="Watercolor",
        description_zh="水彩绘本风：柔和晕染、纸纹、童话感",
        description_en="Watercolor — soft washes, paper texture, fairy-tale",
        prompt_fragment=(
            "watercolor illustration, soft color washes, visible paper texture, "
            "delicate ink outlines, storybook atmosphere"
        ),
        negative_prompt="cel shading, sharp lineart, photorealistic, 3d render",
    ),
    VisualStyleSpec(
        id="ink_wash",
        label_zh="水墨国风",
        label_en="Ink Wash",
        description_zh="水墨国风：留白、笔触、东方意境",
        description_en="Chinese ink wash — minimal, brush-stroked, Eastern",
        prompt_fragment=(
            "Chinese ink wash painting, sumi-e brush strokes, generous negative "
            "space, calligraphic linework, traditional Chinese aesthetic"
        ),
        negative_prompt="vivid colors, western style, cluttered, photorealistic",
    ),
    VisualStyleSpec(
        id="ghibli",
        label_zh="吉卜力风",
        label_en="Studio-Ghibli",
        description_zh="吉卜力田园：柔光、自然背景、温暖治愈",
        description_en="Ghibli-style — soft light, lush nature, warm",
        prompt_fragment=(
            "studio-ghibli inspired, hand-painted backgrounds, soft warm "
            "lighting, lush natural scenery, gentle facial features"
        ),
        negative_prompt="dark gritty, cyberpunk, photorealistic",
    ),
    VisualStyleSpec(
        id="noir",
        label_zh="黑白悬疑",
        label_en="Noir",
        description_zh="黑白悬疑：高对比、阴影戏剧、单色调",
        description_en="Noir — high contrast, dramatic shadow, monochrome",
        prompt_fragment=(
            "black and white noir manga, extreme chiaroscuro, dramatic "
            "shadow, rain, cigarette smoke, monochrome ink"
        ),
        negative_prompt="vibrant colors, cute, sunny, chibi",
    ),
    VisualStyleSpec(
        id="webtoon",
        label_zh="韩漫彩色",
        label_en="Webtoon",
        description_zh="韩漫彩色风：竖屏阅读、清晰平涂、明亮干净",
        description_en="Korean webtoon — vertical, clean flat color, bright",
        prompt_fragment=(
            "korean webtoon style, clean flat color, sharp outlines, modern "
            "fashion, bright daytime lighting, vertical-scroll composition"
        ),
        negative_prompt="screentone, crosshatching, dark, moody",
    ),
)

VISUAL_STYLES_BY_ID: dict[str, VisualStyleSpec] = {s.id: s for s in VISUAL_STYLES}


# ─── Aspect / duration / character role / backend ─────────────────────────

# 9:16 first — most short-drama platforms (抖音 / TikTok / 快手) are
# vertical-only. 16:9 / 1:1 / 4:5 are kept for Bilibili / YouTube / IG
# Reels respectively.
RATIOS: tuple[str, ...] = ("9:16", "1:1", "16:9", "4:5")

# Per-episode duration options (seconds). Seedance's max single segment
# is 15s — anything longer is concatenated client-side from N segments.
DURATION_OPTIONS: tuple[int, ...] = (15, 30, 60, 90, 120, 180)

# How long each panel runs on screen. Drives the storyboard split so the
# pipeline can keep total_duration ≈ n_panels × seconds_per_panel.
SECONDS_PER_PANEL_OPTIONS: tuple[int, ...] = (3, 4, 5, 6, 8, 10)

CHARACTER_ROLES: tuple[str, ...] = ("main", "support", "narrator", "villain")

BACKENDS: tuple[str, ...] = ("direct", "runninghub", "comfyui_local")

# CNY total above which the pipeline pauses for explicit user
# confirmation (POST /episodes returns 402 Payment Required with the
# CostPreview attached, the UI shows <CostExceedModal>).
DEFAULT_COST_THRESHOLD_CNY: float = 5.00


# ─── Voices (Edge-TTS free + CosyVoice-v2 paid) ─────────────────────────


@dataclass(frozen=True)
class VoiceSpec:
    """One TTS voice option, free / paid / cloned."""

    id: str  # voice id passed to the engine
    engine: Literal["edge", "cosyvoice"]
    label_zh: str
    label_en: str
    gender: Literal["female", "male", "neutral"]
    style_zh: str
    style_en: str
    is_free: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "engine": self.engine,
            "label": self.label_zh,
            "label_zh": self.label_zh,
            "label_en": self.label_en,
            "gender": self.gender,
            "style": self.style_zh,
            "style_zh": self.style_zh,
            "style_en": self.style_en,
            "is_free": self.is_free,
        }


# Edge-TTS — free, four cn-CN voices that cover the four character role
# archetypes (main / support / narrator / villain). The full Edge-TTS
# catalogue is ~500 voices; we surface the four most useful for drama.
EDGE_TTS_VOICES: tuple[VoiceSpec, ...] = (
    VoiceSpec(
        id="zh-CN-XiaoyiNeural",
        engine="edge",
        label_zh="晓伊（少女主角）",
        label_en="Xiaoyi (young heroine)",
        gender="female",
        style_zh="温柔活泼",
        style_en="warm, lively",
    ),
    VoiceSpec(
        id="zh-CN-YunjianNeural",
        engine="edge",
        label_zh="云健（少年主角）",
        label_en="Yunjian (young hero)",
        gender="male",
        style_zh="清亮坚定",
        style_en="bright, decisive",
    ),
    VoiceSpec(
        id="zh-CN-XiaoxiaoNeural",
        engine="edge",
        label_zh="晓晓（旁白）",
        label_en="Xiaoxiao (narrator)",
        gender="female",
        style_zh="知性叙事",
        style_en="intellectual, narrative",
    ),
    VoiceSpec(
        id="zh-CN-YunxiNeural",
        engine="edge",
        label_zh="云希（反派低音）",
        label_en="Yunxi (villain bass)",
        gender="male",
        style_zh="低沉冷峻",
        style_en="deep, cold",
    ),
)

# CosyVoice-v2 — paid via DashScope. We offer the same 12 system voices
# avatar-studio surfaces because both share the same upstream catalogue.
COSYVOICE_VOICES: tuple[VoiceSpec, ...] = (
    VoiceSpec(
        "longxiaochun",
        "cosyvoice",
        "龙小淳",
        "Long Xiaochun",
        "female",
        "知性温暖",
        "intellectual",
        is_free=False,
    ),
    VoiceSpec(
        "longxiaobai",
        "cosyvoice",
        "龙小白",
        "Long Xiaobai",
        "female",
        "清亮活泼",
        "bright",
        is_free=False,
    ),
    VoiceSpec(
        "longxiaocheng",
        "cosyvoice",
        "龙小诚",
        "Long Xiaocheng",
        "male",
        "沉稳磁性",
        "calm",
        is_free=False,
    ),
    VoiceSpec(
        "longxiaoxia",
        "cosyvoice",
        "龙小夏",
        "Long Xiaoxia",
        "female",
        "甜美可爱",
        "sweet",
        is_free=False,
    ),
    VoiceSpec(
        "longxiaoshi",
        "cosyvoice",
        "龙小诗",
        "Long Xiaoshi",
        "female",
        "诗意优雅",
        "elegant",
        is_free=False,
    ),
    VoiceSpec(
        "longxiaoxi",
        "cosyvoice",
        "龙小溪",
        "Long Xiaoxi",
        "female",
        "灵动柔软",
        "gentle",
        is_free=False,
    ),
    VoiceSpec(
        "longxiaoxuan",
        "cosyvoice",
        "龙小璇",
        "Long Xiaoxuan",
        "female",
        "成熟稳重",
        "mature",
        is_free=False,
    ),
    VoiceSpec(
        "longwan", "cosyvoice", "龙婉", "Long Wan", "female", "温柔治愈", "warm", is_free=False
    ),
    VoiceSpec(
        "longhan", "cosyvoice", "龙寒", "Long Han", "male", "冷峻深沉", "deep", is_free=False
    ),
    VoiceSpec(
        "longhua", "cosyvoice", "龙华", "Long Hua", "male", "朝气阳光", "energetic", is_free=False
    ),
    VoiceSpec(
        "longxiaohui",
        "cosyvoice",
        "龙小卉",
        "Long Xiaohui",
        "female",
        "邻家少女",
        "youthful",
        is_free=False,
    ),
    VoiceSpec(
        "longmiao", "cosyvoice", "龙妙", "Long Miao", "female", "知性主播", "news", is_free=False
    ),
)

ALL_VOICES: tuple[VoiceSpec, ...] = EDGE_TTS_VOICES + COSYVOICE_VOICES
VOICES_BY_ID: dict[str, VoiceSpec] = {v.id: v for v in ALL_VOICES}


# ─── Price table (manga-studio direct backend) ────────────────────────────

# Pricing reference (October 2025):
# - Volcengine Seedance 1.0 Lite I2V: ¥0.40 / video-second (480P).
#     https://www.volcengine.com/docs/82379/1389626
# - Volcengine Seedance 1.0 Lite T2V: ¥0.50 / video-second.
# - DashScope wan2.7-image (text-to-image): ¥0.20 / image (1024×1024).
#     https://help.aliyun.com/zh/model-studio/wan-image-api
# - DashScope wan2.7-image-pro: ¥0.50 / image.
# - DashScope cosyvoice-v2 (TTS): ¥0.20 / 10k chars.
# - DashScope qwen-vl-max (script writer): ¥0.02/1k input, ¥0.06/1k output.
# - Edge-TTS: free.
# Tests freeze a copy of this table so a remote drift never silently
# changes the displayed cost.
PRICE_TABLE: dict[str, dict[str, float]] = {
    "seedance-1.0-lite-i2v": {"480P_per_sec": 0.40, "720P_per_sec": 0.65},
    "seedance-1.0-lite-t2v": {"480P_per_sec": 0.50, "720P_per_sec": 0.75},
    "wan2.7-image": {"per_image": 0.20},
    "wan2.7-image-pro": {"per_image": 0.50},
    "cosyvoice-v2": {"per_10k_chars": 0.20},
    "qwen-vl-max": {"per_1k_input_token": 0.02, "per_1k_output_token": 0.06},
    "edge-tts": {"per_10k_chars": 0.0},
}


# ─── Cost preview types ───────────────────────────────────────────────────


class CostItem(TypedDict):
    name: str
    units: float
    unit_label: str
    unit_price: float
    subtotal: float
    note: str


class CostPreview(TypedDict):
    total: float
    currency: str
    items: list[CostItem]
    exceeds_threshold: bool
    threshold: float
    formatted_total: str


def _round(x: float | Decimal, places: int = 2) -> float:
    quant = Decimal(10) ** -places
    return float(Decimal(str(x)).quantize(quant, rounding=ROUND_HALF_UP))


def _fmt(x: float | Decimal) -> str:
    return f"¥{_round(x):.2f}"


def estimate_cost(
    *,
    n_panels: int,
    total_duration_sec: int,
    story_chars: int,
    image_model: str = "wan2.7-image",
    video_model: str = "seedance-1.0-lite-i2v",
    resolution: str = "480P",
    tts_engine: str = "edge",
    use_qwen_for_script: bool = True,
    qwen_token_estimate: int = 1500,
    threshold: float = DEFAULT_COST_THRESHOLD_CNY,
) -> CostPreview:
    """Estimate the CNY cost of one manga drama episode.

    Args:
        n_panels:           number of storyboard panels to render.
        total_duration_sec: total episode video length in seconds.
        story_chars:        number of characters in the dialogue / narration.
        image_model:        ``wan2.7-image`` or ``wan2.7-image-pro``.
        video_model:        Seedance variant key from ``PRICE_TABLE``.
        resolution:         ``480P`` or ``720P``.
        tts_engine:         ``edge`` (free) or ``cosyvoice``.
        use_qwen_for_script: include a qwen-vl line item for script generation.
        qwen_token_estimate: estimated total input+output tokens for qwen.
        threshold:          CNY ceiling above which the UI must show
                             ``<CostExceedModal>``.
    """
    items: list[CostItem] = []

    # ── Step 1: script (LLM, optional) ────────────────────────────
    if use_qwen_for_script and qwen_token_estimate > 0:
        units_k = max(1, qwen_token_estimate) / 1000.0
        per_in = PRICE_TABLE["qwen-vl-max"]["per_1k_input_token"]
        per_out = PRICE_TABLE["qwen-vl-max"]["per_1k_output_token"]
        # Typical prompt:completion ratio for storyboard JSON is ~3:7.
        blended = per_in * 0.3 + per_out * 0.7
        items.append(
            CostItem(
                name="qwen-vl-max (script)",
                units=round(units_k, 4),
                unit_label="千 token",
                unit_price=round(blended, 4),
                subtotal=_round(units_k * blended, places=4),
                note="拆剧本 + 分镜",
            )
        )

    # ── Step 2-3: panel images ─────────────────────────────────────
    img_price = PRICE_TABLE[image_model]["per_image"]
    items.append(
        CostItem(
            name=image_model,
            units=float(n_panels),
            unit_label="张",
            unit_price=img_price,
            subtotal=_round(n_panels * img_price),
            note=f"{n_panels} 张漫画分镜",
        )
    )

    # ── Step 4: image-to-video animation ───────────────────────────
    res_key = "720P_per_sec" if resolution.upper() == "720P" else "480P_per_sec"
    if video_model not in PRICE_TABLE:
        raise ValueError(f"unknown video_model: {video_model!r}")
    video_price = PRICE_TABLE[video_model][res_key]
    items.append(
        CostItem(
            name=f"{video_model} {resolution}",
            units=float(total_duration_sec),
            unit_label="秒",
            unit_price=video_price,
            subtotal=_round(total_duration_sec * video_price),
            note=f"{n_panels} 段图生视频拼接",
        )
    )

    # ── Step 5: TTS ────────────────────────────────────────────────
    if tts_engine == "cosyvoice":
        units_w = max(1, story_chars) / 10000.0
        per = PRICE_TABLE["cosyvoice-v2"]["per_10k_chars"]
        items.append(
            CostItem(
                name="cosyvoice-v2 TTS",
                units=round(units_w, 4),
                unit_label="万字",
                unit_price=per,
                subtotal=_round(units_w * per, places=4),
                note=f"约 {story_chars} 字台词",
            )
        )
    else:  # edge — free, but we still surface the line item
        items.append(
            CostItem(
                name="edge-tts (free)",
                units=float(max(1, story_chars)),
                unit_label="字",
                unit_price=0.0,
                subtotal=0.0,
                note="微软免费 TTS — 共 0 元",
            )
        )

    raw_total = sum(it["units"] * it["unit_price"] for it in items)
    total = _round(raw_total)
    return CostPreview(
        total=total,
        currency="CNY",
        items=items,
        exceeds_threshold=total > threshold,
        threshold=threshold,
        formatted_total=_fmt(total),
    )


# ─── Error hints (Pixelle C2 — bilingual, actionable, 9 kinds) ────────────
#
# Keys here mirror the constants exported by ``manga_inline.vendor_client``
# (ERROR_KIND_*) plus three manga-only kinds:
#
# - ``moderation_face`` — Seedance refused a frame because it carried a
#                         human face (notoriously strict). The pipeline
#                         falls back from I2V to T2V; the UI shows a hint
#                         pointing at the offending panel.
# - ``content_violation`` — DashScope's data inspection refused the
#                           prompt or the reference image (NSFW, IP, etc).
# - ``dependency`` — a non-vendor blocker (FFmpeg missing, OSS not
#                    configured, Pillow not installed for image resize).


class ErrorHint(TypedDict):
    title_zh: str
    title_en: str
    hints_zh: list[str]
    hints_en: list[str]


ERROR_HINTS: dict[str, ErrorHint] = {
    "network": {
        "title_zh": "网络异常",
        "title_en": "Network error",
        "hints_zh": [
            "请检查网络连接",
            "若使用代理请确认 ark.cn-beijing.volces.com / dashscope.aliyuncs.com 可达",
            "稍后会自动重试 3 次",
        ],
        "hints_en": [
            "Check the network connection",
            "If using a proxy, verify Ark / DashScope endpoints are reachable",
            "Will auto-retry up to 3 times",
        ],
    },
    "timeout": {
        "title_zh": "请求超时",
        "title_en": "Timeout",
        "hints_zh": [
            "图生视频通常需要 30-180 秒，可在「任务」页查看实时进度",
            "若 5 分钟仍未完成，请重试或在「设置」调高超时阈值",
        ],
        "hints_en": [
            "Image-to-video typically takes 30-180s; check Tasks for live progress",
            "If still pending after 5min, retry or raise timeout in Settings",
        ],
    },
    "rate_limit": {
        "title_zh": "并发受限",
        "title_en": "Rate limited",
        "hints_zh": [
            "Seedance / DashScope 异步任务并发上限默认为 1-3，请等待当前任务完成",
            "可在「设置」减小 panel_concurrency 数",
        ],
        "hints_en": [
            "Seedance / DashScope concurrency limit is 1-3; wait for current job",
            "Lower panel_concurrency in Settings",
        ],
    },
    "auth": {
        "title_zh": "鉴权失败",
        "title_en": "Auth failed",
        "hints_zh": [
            "请到「设置 → API Key」重新填写",
            "确认 ARK_API_KEY / DASHSCOPE_API_KEY 与所选地域匹配",
        ],
        "hints_en": [
            "Re-enter the API Key in Settings",
            "Ensure ARK / DASHSCOPE keys match the selected region",
        ],
    },
    "not_found": {
        "title_zh": "任务不存在",
        "title_en": "Task not found",
        "hints_zh": [
            "Seedance / DashScope task_id 有效期 24 小时，可能已过期",
            "重新提交即可生成新任务",
        ],
        "hints_en": [
            "task_id expires after 24h",
            "Resubmit to generate a new task",
        ],
    },
    "moderation": {
        "title_zh": "内容审核未通过",
        "title_en": "Content moderation",
        "hints_zh": [
            "提示词或参考图被识别为敏感，请更换素材或修改剧情描述",
            "常见原因：暴力 / 政治 / 色情 / 商标 / 名人肖像",
        ],
        "hints_en": [
            "Prompt or reference was flagged sensitive; replace the asset",
            "Common: violence, politics, NSFW, brands, celebrity likeness",
        ],
    },
    "moderation_face": {
        "title_zh": "Seedance 人脸合规拒绝",
        "title_en": "Seedance face moderation",
        "hints_zh": [
            "图生视频时人脸过于清晰被拒，已自动降级为「文生视频」",
            "建议：将参考图改为侧脸 / 局部 / 远景，或选择 Q 版风格",
        ],
        "hints_en": [
            "Frame's face was rejected; auto-fell back to text-to-video",
            "Tip: use side / partial / distant shots, or pick chibi style",
        ],
    },
    "content_violation": {
        "title_zh": "提示词违规",
        "title_en": "Prompt violation",
        "hints_zh": [
            "请删除涉及版权、真人、敏感主题的关键词",
            "可在「设置」开启「严格模式」由 LLM 预审",
        ],
        "hints_en": [
            "Remove copyrighted / celebrity / sensitive keywords from the prompt",
            "Enable Strict Mode in Settings to let the LLM pre-screen",
        ],
    },
    "quota": {
        "title_zh": "余额不足",
        "title_en": "Quota exceeded",
        "hints_zh": [
            "请到火山引擎 / 阿里云百炼控制台充值",
            "或在「设置」切换到其他 API Key",
        ],
        "hints_en": [
            "Top up at Volcengine / Bailian console",
            "Or switch the API Key in Settings",
        ],
    },
    "dependency": {
        "title_zh": "本地依赖缺失",
        "title_en": "Local dependency missing",
        "hints_zh": [
            "FFmpeg 未安装或版本过低（需 ≥ 4.4），请在「设置 → 系统」一键安装",
            "OSS 未配置时图生视频无法 fetch 参考图，请填写 OSS 凭据",
        ],
        "hints_en": [
            "FFmpeg missing or too old (need ≥ 4.4); install via Settings → System",
            "OSS unconfigured → vendors can't fetch reference image; fill OSS creds",
        ],
    },
    "unknown": {
        "title_zh": "未知错误",
        "title_en": "Unknown error",
        "hints_zh": [
            "请将任务 id 发给开发者",
            "或截图任务详情页的 metadata json",
        ],
        "hints_en": [
            "Report the task id to the developer",
            "Or screenshot the metadata json from Task details",
        ],
    },
}


def hint_for(error_kind: str | None) -> ErrorHint:
    """Look up bilingual hints for an ``error_kind``; falls back to ``unknown``."""
    if not error_kind:
        return ERROR_HINTS["unknown"]
    return ERROR_HINTS.get(error_kind, ERROR_HINTS["unknown"])


# ─── Public catalog payload (for GET /catalog) ────────────────────────────


@dataclass(frozen=True)
class CatalogPayload:
    """Snapshot returned by GET /catalog so the UI gets one round-trip seed."""

    visual_styles: list[dict[str, object]] = field(default_factory=list)
    ratios: list[str] = field(default_factory=list)
    duration_options: list[int] = field(default_factory=list)
    seconds_per_panel_options: list[int] = field(default_factory=list)
    character_roles: list[str] = field(default_factory=list)
    backends: list[str] = field(default_factory=list)
    voices: list[dict[str, object]] = field(default_factory=list)
    cost_threshold: float = DEFAULT_COST_THRESHOLD_CNY


def build_catalog() -> CatalogPayload:
    """Materialise the static UI catalog (styles + voices + option lists)."""
    return CatalogPayload(
        visual_styles=[s.to_dict() for s in VISUAL_STYLES],
        ratios=list(RATIOS),
        duration_options=list(DURATION_OPTIONS),
        seconds_per_panel_options=list(SECONDS_PER_PANEL_OPTIONS),
        character_roles=list(CHARACTER_ROLES),
        backends=list(BACKENDS),
        voices=[v.to_dict() for v in ALL_VOICES],
        cost_threshold=DEFAULT_COST_THRESHOLD_CNY,
    )
