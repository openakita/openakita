"""StubASRProvider — returns a single placeholder chunk for testing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ASRChunk, ASRResult, BaseASRProvider


class StubASRProvider(BaseASRProvider):
    """Always-available no-op ASR — useful for tests and dev environments
    where neither Bailian nor whisper are installed."""

    provider_id = "stub"
    display_name = "Stub ASR (always returns one placeholder chunk)"
    requires_api_key = False
    requires_internet = False

    async def transcribe(
        self,
        source: Path,
        *,
        language: str = "auto",
        **kwargs: Any,  # noqa: ARG002
    ) -> ASRResult:
        return ASRResult(
            provider=self.provider_id,
            chunks=[
                ASRChunk(
                    start=0.0,
                    end=1.0,
                    text=f"[stub transcript for {source.name}]",
                    confidence=0.0,
                )
            ],
            language=language,
            duration_sec=1.0,
            raw={"note": "stub provider — replace with real ASR before production"},
        )
