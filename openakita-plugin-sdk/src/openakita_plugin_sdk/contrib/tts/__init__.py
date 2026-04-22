"""openakita_plugin_sdk.contrib.tts — shared TTS provider library.

Why this exists
---------------
Before SDK 0.6.0 every plugin that needed TTS reached into
``plugins/avatar-speaker/providers.py`` via ``_load_sibling`` (an anti-pattern
that physically coupled tts-studio / video-translator / ppt-to-video / dub-it
to a sibling plugin's load order). When avatar-speaker failed to load — for
any reason — the entire downstream stack went dark.

This package extracts the TTS layer into a stable, vendor-agnostic
collection of providers that any plugin can import without depending on
another plugin being loaded.

Design rules
------------
- ``BaseTTSProvider(config: dict)`` — providers are constructed with an
  *opaque config dict* (typically ``{"api_key": "..."}``). API keys are
  **never** read from ``os.environ`` inside a provider. Callers (plugins)
  decide where to source the key from (``_tm.get_config(...)`` per the
  avatar-speaker / tongyi-image convention, with env as bootstrap fallback).
- ``synthesize(text, voice, output_dir, **kwargs) -> TTSResult`` — every
  provider returns the same dataclass.
- China-friendly default ordering: ``qwen3_tts_flash > cosyvoice > openai >
  edge``. This is what ``select_provider("auto", region="cn")`` returns.
- ``registry.list_providers()`` enumerates available providers based on the
  caller-supplied configuration map; missing API keys silently skip the
  provider rather than raising — letting the caller pick a fallback.
"""

from __future__ import annotations

from .base import (
    BaseTTSProvider,
    TTSError,
    TTSResult,
    estimate_duration_sec,
)
from .dashscope import (
    CosyVoiceProvider,
    Qwen3TTSFlashProvider,
)
from .edge import EdgeTTSProvider
from .openai import OpenAITTSProvider
from .registry import (
    PROVIDER_PRIORITY_CHINA,
    PROVIDER_PRIORITY_GLOBAL,
    available_providers,
    build_provider,
    list_provider_ids,
    select_provider,
)
from .voices import (
    VOICE_CATALOG,
    Voice,
    list_voices,
    voice_by_id,
)

__all__ = [
    "PROVIDER_PRIORITY_CHINA",
    "PROVIDER_PRIORITY_GLOBAL",
    "VOICE_CATALOG",
    "BaseTTSProvider",
    "CosyVoiceProvider",
    "EdgeTTSProvider",
    "OpenAITTSProvider",
    "Qwen3TTSFlashProvider",
    "TTSError",
    "TTSResult",
    "Voice",
    "available_providers",
    "build_provider",
    "estimate_duration_sec",
    "list_provider_ids",
    "list_voices",
    "select_provider",
    "voice_by_id",
]
