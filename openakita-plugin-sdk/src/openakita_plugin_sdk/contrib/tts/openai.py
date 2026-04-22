"""OpenAITTSProvider — wraps OpenAI's ``tts-1`` / ``tts-1-hd`` endpoints."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from .base import BaseTTSProvider, TTSError, TTSResult, estimate_duration_sec

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://api.openai.com"
_VALID_MODELS = ("tts-1", "tts-1-hd")


class OpenAITTSProvider(BaseTTSProvider):
    provider_id = "openai"
    display_name = "OpenAI TTS (tts-1 / tts-1-hd)"
    requires_api_key = True
    requires_internet = True

    DEFAULT_VOICE = "alloy"

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")

    @property
    def model(self) -> str:
        m = str(self.config.get("model") or "tts-1")
        if m not in _VALID_MODELS:
            logger.warning("Unknown OpenAI TTS model %r, falling back to tts-1", m)
            return "tts-1"
        return m

    @property
    def timeout(self) -> float:
        return float(self.config.get("timeout", 120.0))

    async def synthesize(
        self,
        *,
        text: str,
        voice: str | None = None,
        output_dir: Path,
        rate: str = "+0%",  # noqa: ARG002 — OpenAI TTS lacks rate/pitch knobs
        pitch: str = "+0Hz",  # noqa: ARG002
        response_format: str = "mp3",
        **kwargs: Any,  # noqa: ARG002
    ) -> TTSResult:
        if not self.api_key:
            raise TTSError(
                "OpenAI API key not configured.",
                retryable=False,
                provider=self.provider_id,
                kind="auth",
            )
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise TTSError(
                "httpx is required for OpenAI provider.",
                retryable=False,
                provider=self.provider_id,
                kind="missing_dependency",
            ) from exc

        voice = voice or self.DEFAULT_VOICE
        url = self.base_url + "/v1/audio/speech"
        body = {
            "model": self.model,
            "input": text,
            "voice": voice,
            "response_format": response_format,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise TTSError(
                f"openai network error: {exc}",
                retryable=True,
                provider=self.provider_id,
                kind="network",
            ) from exc

        if resp.status_code >= 400:
            kind = "auth" if resp.status_code in (401, 403) else "vendor_error"
            raise TTSError(
                f"openai HTTP {resp.status_code}: {resp.text[:200]}",
                retryable=resp.status_code in (429, 500, 502, 503, 504),
                provider=self.provider_id,
                kind=kind,
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{uuid.uuid4().hex[:12]}.{response_format}"
        out.write_bytes(resp.content)
        return TTSResult(
            provider=self.provider_id,
            audio_path=out,
            duration_sec=estimate_duration_sec(text),
            voice=voice,
            raw={"size": len(resp.content), "model": self.model},
        )
