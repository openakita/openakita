"""openakita_plugin_sdk.contrib.asr — shared ASR provider library.

Mirrors the design of ``contrib.tts`` for speech-to-text:

- Cloud primary: ``DashScopeParaformerProvider`` (Bailian Paraformer).
- Local fallback: ``WhisperLocalProvider`` (whisper.cpp CLI).
- Stubbed scaffold: ``StubASRProvider`` for tests / dev without deps.

Reused by: highlight-cutter, subtitle-maker, transcribe-archive,
video-translator, dub-it. None of those plugins should reach into a
sibling plugin for ASR ever again.
"""

from __future__ import annotations

from .base import (
    ASRChunk,
    ASRError,
    ASRResult,
    BaseASRProvider,
)
from .dashscope_paraformer import DashScopeParaformerProvider
from .registry import (
    PROVIDER_PRIORITY_CHINA,
    PROVIDER_PRIORITY_GLOBAL,
    available_providers,
    build_provider,
    list_provider_ids,
    select_provider,
)
from .stub import StubASRProvider
from .whisper_local import WhisperLocalProvider

__all__ = [
    "PROVIDER_PRIORITY_CHINA",
    "PROVIDER_PRIORITY_GLOBAL",
    "ASRChunk",
    "ASRError",
    "ASRResult",
    "BaseASRProvider",
    "DashScopeParaformerProvider",
    "StubASRProvider",
    "WhisperLocalProvider",
    "available_providers",
    "build_provider",
    "list_provider_ids",
    "select_provider",
]
