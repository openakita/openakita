"""Unified voice catalog across all contrib.tts providers.

The catalog is intentionally curated (not auto-generated) — it is the
single source of truth that plugin UIs can render in a "voice picker"
dropdown without having to call each provider's listing API.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Voice:
    """A single voice option exposed in plugin UIs."""

    id: str
    label: str
    provider: str
    language: str  # primary language code, e.g. "zh-CN", "en-US"
    gender: str = "unknown"  # "male" / "female" / "neutral"
    style: str = ""  # short marketing copy: e.g. "warm", "news", "energetic"


VOICE_CATALOG: tuple[Voice, ...] = (
    # ── qwen3-tts-flash (Bailian primary) ─────────────────────────────
    Voice("Cherry", "Cherry (女, 自然甜美)", "qwen3_tts_flash", "zh-CN", "female", "warm"),
    Voice("Ethan", "Ethan (男, 温润磁性)", "qwen3_tts_flash", "zh-CN", "male", "calm"),
    Voice("Chelsie", "Chelsie (女, 活泼少女)", "qwen3_tts_flash", "zh-CN", "female", "energetic"),
    Voice("Serena", "Serena (女, 知性主播)", "qwen3_tts_flash", "zh-CN", "female", "news"),
    Voice("Dylan", "Dylan (男, 商务沉稳)", "qwen3_tts_flash", "zh-CN", "male", "business"),
    Voice("Ava", "Ava (女, 国际播报)", "qwen3_tts_flash", "en-US", "female", "news"),
    Voice("Noah", "Noah (男, 国际旁白)", "qwen3_tts_flash", "en-US", "male", "narration"),
    # ── CosyVoice (legacy / cloning) ──────────────────────────────────
    Voice("longwan", "龙婉 (女, CosyVoice 温柔)", "cosyvoice", "zh-CN", "female", "warm"),
    Voice("longxiaobai", "龙小白 (女, CosyVoice 清亮)", "cosyvoice", "zh-CN", "female", "bright"),
    Voice("longcheng", "龙橙 (男, CosyVoice 朝气)", "cosyvoice", "zh-CN", "male", "energetic"),
    Voice("longshu", "龙书 (男, CosyVoice 沉稳)", "cosyvoice", "zh-CN", "male", "calm"),
    # ── Edge TTS (free) ───────────────────────────────────────────────
    Voice("zh-CN-XiaoxiaoNeural", "晓晓 (女, Edge 温暖)", "edge", "zh-CN", "female", "warm"),
    Voice("zh-CN-YunxiNeural", "云希 (男, Edge 阳光)", "edge", "zh-CN", "male", "sunny"),
    Voice("zh-CN-XiaoyiNeural", "晓伊 (女, Edge 沉稳)", "edge", "zh-CN", "female", "calm"),
    Voice("zh-CN-YunyangNeural", "云扬 (男, Edge 新闻)", "edge", "zh-CN", "male", "news"),
    Voice("zh-CN-XiaohanNeural", "晓涵 (女, Edge 知性)", "edge", "zh-CN", "female", "intellectual"),
    Voice("zh-CN-XiaomengNeural", "晓梦 (女, Edge 治愈)", "edge", "zh-CN", "female", "healing"),
    Voice("zh-CN-XiaomoNeural", "晓墨 (女, Edge 故事)", "edge", "zh-CN", "female", "narrative"),
    Voice("zh-CN-YunjianNeural", "云健 (男, Edge 体育)", "edge", "zh-CN", "male", "sport"),
    Voice("en-US-AriaNeural", "Aria (女, Edge 国际)", "edge", "en-US", "female", "warm"),
    Voice("en-US-GuyNeural", "Guy (男, Edge 国际)", "edge", "en-US", "male", "calm"),
    # ── OpenAI TTS ────────────────────────────────────────────────────
    Voice("alloy", "Alloy (中性, OpenAI)", "openai", "en-US", "neutral", "calm"),
    Voice("echo", "Echo (男, OpenAI 旁白)", "openai", "en-US", "male", "narration"),
    Voice("fable", "Fable (男, OpenAI 故事)", "openai", "en-US", "male", "narrative"),
    Voice("onyx", "Onyx (男, OpenAI 深沉)", "openai", "en-US", "male", "deep"),
    Voice("nova", "Nova (女, OpenAI 活泼)", "openai", "en-US", "female", "energetic"),
    Voice("shimmer", "Shimmer (女, OpenAI 柔和)", "openai", "en-US", "female", "soft"),
)


def list_voices(*, provider: str | None = None, language: str | None = None) -> list[Voice]:
    """Return voices filtered by provider and/or language code prefix."""
    out: list[Voice] = []
    for v in VOICE_CATALOG:
        if provider and v.provider != provider:
            continue
        if language and not v.language.startswith(language):
            continue
        out.append(v)
    return out


def voice_by_id(voice_id: str) -> Voice | None:
    """Look up a single voice by its provider-specific id."""
    for v in VOICE_CATALOG:
        if v.id == voice_id:
            return v
    return None
