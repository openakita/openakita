"""DashScope (Alibaba Bailian) TTS providers.

Two providers exposed:

- :class:`Qwen3TTSFlashProvider` — wraps ``qwen3-tts-flash`` (the
  current Bailian-recommended primary model: low latency, multi-voice,
  multi-emotion).  Default for China-region deployments.
- :class:`CosyVoiceProvider` — legacy ``cosyvoice-v1`` retained for
  voice-cloning workflows that still depend on it.

Both providers expect ``config = {"api_key": "<DASHSCOPE_API_KEY>", ...}``.
The api_key is **not** read from the environment here — plugins are
responsible for sourcing it (typically from ``_tm.get_config(...)``).
"""

from __future__ import annotations

import base64
import logging
import uuid
from pathlib import Path
from typing import Any

from .base import BaseTTSProvider, TTSError, TTSResult, estimate_duration_sec

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"


class _DashScopeBase(BaseTTSProvider):
    """Common HTTP/auth helpers for DashScope TTS endpoints."""

    requires_api_key = True
    requires_internet = True

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")

    @property
    def timeout(self) -> float:
        return float(self.config.get("timeout", 120.0))

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise TTSError(
                "DashScope API key not configured.",
                retryable=False,
                provider=self.provider_id,
                kind="auth",
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


class Qwen3TTSFlashProvider(_DashScopeBase):
    """qwen3-tts-flash — Bailian's current default TTS model.

    Voice, emotion and language can be passed via kwargs.  The API
    follows the DashScope sync TTS contract (``output.audio`` base64).
    """

    provider_id = "qwen3_tts_flash"
    display_name = "通义千问 TTS Flash (qwen3-tts-flash)"
    DEFAULT_VOICE = "Cherry"

    async def synthesize(
        self,
        *,
        text: str,
        voice: str | None = None,
        output_dir: Path,
        rate: str = "+0%",  # noqa: ARG002 — not used by qwen3-tts-flash
        pitch: str = "+0Hz",  # noqa: ARG002
        emotion: str | None = None,
        language: str = "Auto",
        sample_rate: int = 24000,
        **kwargs: Any,  # noqa: ARG002
    ) -> TTSResult:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TTSError(
                "httpx is required for DashScope providers.",
                retryable=False,
                provider=self.provider_id,
                kind="missing_dependency",
            ) from exc

        voice = voice or self.DEFAULT_VOICE
        body: dict[str, Any] = {
            "model": "qwen3-tts-flash",
            "input": {"text": text, "voice": voice, "language_type": language},
            "parameters": {"sample_rate": sample_rate, "format": "mp3"},
        }
        if emotion:
            body["input"]["emotion"] = emotion

        url = self.base_url + "/api/v1/services/aigc/multimodal-generation/generation"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise TTSError(
                f"qwen3-tts-flash network error: {exc}",
                retryable=True,
                provider=self.provider_id,
                kind="network",
            ) from exc

        if resp.status_code >= 400:
            kind = "auth" if resp.status_code in (401, 403) else "vendor_error"
            raise TTSError(
                f"qwen3-tts-flash HTTP {resp.status_code}: {resp.text[:200]}",
                retryable=resp.status_code in (429, 500, 502, 503, 504),
                provider=self.provider_id,
                kind=kind,
            )
        data = resp.json()
        audio_b64 = (data.get("output") or {}).get("audio")
        if not audio_b64:
            raise TTSError(
                "qwen3-tts-flash returned no audio payload.",
                retryable=False,
                provider=self.provider_id,
                kind="vendor_error",
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{uuid.uuid4().hex[:12]}.mp3"
        out.write_bytes(base64.b64decode(audio_b64))
        return TTSResult(
            provider=self.provider_id,
            audio_path=out,
            duration_sec=estimate_duration_sec(text),
            voice=voice,
            raw=data,
        )


class CosyVoiceProvider(_DashScopeBase):
    """cosyvoice-v1 — legacy DashScope TTS, retained for voice cloning."""

    provider_id = "cosyvoice"
    display_name = "通义 CosyVoice v1"
    DEFAULT_VOICE = "longwan"

    async def synthesize(
        self,
        *,
        text: str,
        voice: str | None = None,
        output_dir: Path,
        rate: str = "+0%",  # noqa: ARG002
        pitch: str = "+0Hz",  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> TTSResult:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TTSError(
                "httpx is required for DashScope providers.",
                retryable=False,
                provider=self.provider_id,
                kind="missing_dependency",
            ) from exc

        voice = voice or self.DEFAULT_VOICE
        body = {
            "model": "cosyvoice-v1",
            "input": {"text": text},
            "parameters": {"voice": voice, "format": "mp3"},
        }
        url = self.base_url + "/api/v1/services/audio/tts/audio_synthesis"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise TTSError(
                f"cosyvoice network error: {exc}",
                retryable=True,
                provider=self.provider_id,
                kind="network",
            ) from exc

        if resp.status_code >= 400:
            kind = "auth" if resp.status_code in (401, 403) else "vendor_error"
            raise TTSError(
                f"cosyvoice HTTP {resp.status_code}: {resp.text[:200]}",
                retryable=resp.status_code in (429, 500, 502, 503, 504),
                provider=self.provider_id,
                kind=kind,
            )
        data = resp.json()
        audio_b64 = (data.get("output") or {}).get("audio")
        if not audio_b64:
            raise TTSError(
                "cosyvoice returned no audio payload.",
                retryable=False,
                provider=self.provider_id,
                kind="vendor_error",
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{uuid.uuid4().hex[:12]}.mp3"
        out.write_bytes(base64.b64decode(audio_b64))
        return TTSResult(
            provider=self.provider_id,
            audio_path=out,
            duration_sec=estimate_duration_sec(text),
            voice=voice,
            raw=data,
        )
