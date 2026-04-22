"""EdgeTTSProvider — free fallback using Microsoft Edge browser's TTS engine.

No API key required.  Depends on the optional ``edge-tts`` pip package; if
the package is missing, ``is_available()`` returns False and callers should
fall through to another provider.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from .base import BaseTTSProvider, TTSError, TTSResult, estimate_duration_sec

logger = logging.getLogger(__name__)


class EdgeTTSProvider(BaseTTSProvider):
    provider_id = "edge"
    display_name = "Microsoft Edge TTS (free)"
    requires_api_key = False
    requires_internet = True

    DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        try:
            import edge_tts  # noqa: F401

            self._has_pkg = True
        except ImportError:
            self._has_pkg = False

    def is_available(self) -> bool:
        return self._has_pkg and not self.config.get("disabled")

    async def synthesize(
        self,
        *,
        text: str,
        voice: str | None = None,
        output_dir: Path,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        **kwargs: Any,  # noqa: ARG002
    ) -> TTSResult:
        if not self._has_pkg:
            raise TTSError(
                "edge-tts not installed; pip install edge-tts to enable.",
                retryable=False,
                provider=self.provider_id,
                kind="missing_dependency",
            )
        import edge_tts  # type: ignore[import-not-found]

        voice = voice or self.DEFAULT_VOICE
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{uuid.uuid4().hex[:12]}.mp3"
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            await comm.save(str(out))
        except Exception as exc:  # edge-tts raises a grab-bag of types
            raise TTSError(
                f"edge-tts synth failed: {exc}",
                retryable=True,
                provider=self.provider_id,
                kind="vendor_error",
            ) from exc
        return TTSResult(
            provider=self.provider_id,
            audio_path=out,
            duration_sec=estimate_duration_sec(text),
            voice=voice,
            raw={"rate": rate, "pitch": pitch},
        )
