"""avatar-speaker — TTS multi-provider routing.

Providers:
- ``EdgeTTSProvider`` — uses Microsoft Edge browser's free TTS via ``edge-tts``
   (no API key, ~30 voices for Chinese/English).
- ``DashScopeCosyVoiceProvider`` — Alibaba CosyVoice (paid, supports voice cloning).
- ``OpenAITTSProvider`` — OpenAI ``tts-1`` / ``tts-1-hd`` (paid, 6 voices).
- ``StubLocalProvider`` — silent WAV stub for dev/demo without any deps.

Avatar (digital-human) is **scaffolded only**: ``DigitalHumanStubAvatar``
exposes a ``render(audio_path, image_path) -> Path`` interface with a NotImplemented
default so we keep the contract clear for future providers (HeyGen, SadTalker, …).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import struct
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openakita_plugin_sdk.contrib import BaseVendorClient, VendorError

logger = logging.getLogger(__name__)


@dataclass
class TTSResult:
    provider: str
    audio_path: Path
    duration_sec: float
    voice: str
    raw: dict[str, Any]


# ── voices ────────────────────────────────────────────────────────────


PRESET_VOICES_ZH = [
    {"id": "zh-CN-XiaoxiaoNeural",  "label": "晓晓 (女, 温暖)", "provider": "edge"},
    {"id": "zh-CN-YunxiNeural",      "label": "云希 (男, 阳光)", "provider": "edge"},
    {"id": "zh-CN-XiaoyiNeural",     "label": "晓伊 (女, 沉稳)", "provider": "edge"},
    {"id": "zh-CN-YunyangNeural",    "label": "云扬 (男, 新闻)", "provider": "edge"},
    {"id": "longwan",                 "label": "龙婉 (女, CosyVoice)", "provider": "dashscope"},
    {"id": "alloy",                   "label": "Alloy (中性, OpenAI)", "provider": "openai"},
]


# ── EdgeTTS (free) ───────────────────────────────────────────────────


class EdgeTTSProvider:
    name = "edge-tts"

    def __init__(self) -> None:
        try:
            import edge_tts  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False

    @classmethod
    def from_env(cls) -> "EdgeTTSProvider | None":
        p = cls()
        return p if p._available else None

    async def synthesize(
        self, *, text: str, voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%", pitch: str = "+0Hz",
        output_dir: Path,
    ) -> TTSResult:
        if not self._available:
            raise VendorError("edge-tts not installed (pip install edge-tts)")
        import edge_tts
        out = output_dir / f"{uuid.uuid4().hex[:12]}.mp3"
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await comm.save(str(out))
        # rough duration: edge-tts ~ 4 chars/sec for Chinese
        dur = max(1.0, len(text) / 4.0)
        return TTSResult(provider=self.name, audio_path=out,
                         duration_sec=dur, voice=voice,
                         raw={"rate": rate, "pitch": pitch})

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        return False


# ── DashScope CosyVoice ───────────────────────────────────────────────


class DashScopeCosyVoiceProvider(BaseVendorClient):
    name = "dashscope-cosyvoice"

    def __init__(self, *, api_key: str,
                 base_url: str = "https://dashscope.aliyuncs.com",
                 timeout: float = 120.0) -> None:
        super().__init__(base_url=base_url, timeout=timeout)
        self._api_key = api_key

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @classmethod
    def from_env(cls) -> "DashScopeCosyVoiceProvider | None":
        key = os.environ.get("DASHSCOPE_API_KEY")
        return cls(api_key=key) if key else None

    async def synthesize(
        self, *, text: str, voice: str = "longwan",
        rate: str = "+0%", pitch: str = "+0Hz",
        output_dir: Path,
    ) -> TTSResult:
        body = {"model": "cosyvoice-v1",
                "input": {"text": text},
                "parameters": {"voice": voice, "format": "mp3"}}
        try:
            data = await self.post_json(
                "/api/v1/services/audio/tts/audio_synthesis", body,
            )
        except VendorError:
            raise
        audio_b64 = (data.get("output") or {}).get("audio")
        if not audio_b64:
            raise VendorError("CosyVoice returned no audio", retryable=False)
        import base64
        out = output_dir / f"{uuid.uuid4().hex[:12]}.mp3"
        out.write_bytes(base64.b64decode(audio_b64))
        return TTSResult(provider=self.name, audio_path=out,
                         duration_sec=max(1.0, len(text) / 4.0), voice=voice,
                         raw=data)

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        return False


# ── OpenAI TTS ────────────────────────────────────────────────────────


class OpenAITTSProvider(BaseVendorClient):
    name = "openai-tts"

    def __init__(self, *, api_key: str,
                 base_url: str = "https://api.openai.com",
                 timeout: float = 120.0) -> None:
        super().__init__(base_url=base_url, timeout=timeout)
        self._api_key = api_key

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @classmethod
    def from_env(cls) -> "OpenAITTSProvider | None":
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
        return cls(api_key=key) if key else None

    async def synthesize(
        self, *, text: str, voice: str = "alloy",
        rate: str = "+0%", pitch: str = "+0Hz",
        output_dir: Path,
    ) -> TTSResult:
        import httpx
        url = self.base_url.rstrip("/") + "/v1/audio/speech"
        body = {"model": "tts-1", "input": text, "voice": voice, "response_format": "mp3"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=self.auth_headers(), json=body)
        except httpx.HTTPError as e:
            raise VendorError(f"openai network error: {e}", retryable=True) from e
        if resp.status_code >= 400:
            raise VendorError(f"openai HTTP {resp.status_code}: {resp.text[:200]}",
                              status=resp.status_code,
                              retryable=resp.status_code in (429, 500, 502, 503, 504))
        out = output_dir / f"{uuid.uuid4().hex[:12]}.mp3"
        out.write_bytes(resp.content)
        return TTSResult(provider=self.name, audio_path=out,
                         duration_sec=max(1.0, len(text) / 4.0), voice=voice,
                         raw={"size": len(resp.content)})

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        return False


# ── stub (silent wav) ─────────────────────────────────────────────────


class StubLocalProvider:
    name = "stub-silent"

    async def synthesize(
        self, *, text: str, voice: str = "stub",
        rate: str = "+0%", pitch: str = "+0Hz",
        output_dir: Path,
    ) -> TTSResult:
        # Generate a 1-second silent WAV
        out = output_dir / f"{uuid.uuid4().hex[:12]}.wav"
        sample_rate = 22050
        n_samples = int(sample_rate * max(1.0, min(10.0, len(text) / 4.0)))
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * n_samples)
        return TTSResult(provider=self.name, audio_path=out,
                         duration_sec=n_samples / sample_rate,
                         voice=voice,
                         raw={"note": "stub silent wav, no real TTS"})

    async def cancel_task(self, task_id: str) -> bool:  # noqa: ARG002
        return False


# ── digital-human (scaffold) ──────────────────────────────────────────


class DigitalHumanAvatar:
    """Avatar scaffold — render(audio, portrait) → talking-head video.

    Subclasses implement :meth:`render`.  The base raises NotImplementedError
    so plugins fail fast (and ErrorCoach can render a clean message).
    """

    name = "abstract-avatar"

    async def render(self, *, audio_path: Path, portrait_path: Path,
                     output_dir: Path) -> Path:
        raise NotImplementedError(
            f"{type(self).__name__}.render() not implemented yet — "
            "数字人合成在 P3 backlog (HeyGen / SadTalker / D-ID 集成)。"
            "目前只支持音频生成，请先关闭【数字人形象】。",
        )


class StubAvatar(DigitalHumanAvatar):
    """No-op avatar — copies the portrait as a single-frame mp4 stub."""

    name = "stub-avatar"

    async def render(self, *, audio_path: Path, portrait_path: Path,
                     output_dir: Path) -> Path:
        out = output_dir / f"{uuid.uuid4().hex[:12]}.txt"
        out.write_text(
            f"Stub avatar render\naudio: {audio_path}\nportrait: {portrait_path}\n"
            "(实际数字人合成在 P3 实现 - HeyGen / SadTalker)",
            encoding="utf-8",
        )
        return out


# ── chooser ───────────────────────────────────────────────────────────


def select_tts_provider(preferred: str = "auto") -> Any:
    if preferred == "edge":
        p = EdgeTTSProvider.from_env()
        if not p:
            raise VendorError("edge-tts not installed (pip install edge-tts)",
                              retryable=False)
        return p
    if preferred == "dashscope":
        p = DashScopeCosyVoiceProvider.from_env()
        if not p:
            raise VendorError("DASHSCOPE_API_KEY is not set", retryable=False)
        return p
    if preferred == "openai":
        p = OpenAITTSProvider.from_env()
        if not p:
            raise VendorError("OPENAI_API_KEY is not set", retryable=False)
        return p
    if preferred == "stub":
        return StubLocalProvider()
    return (
        EdgeTTSProvider.from_env()
        or DashScopeCosyVoiceProvider.from_env()
        or OpenAITTSProvider.from_env()
        or StubLocalProvider()
    )


def select_avatar(preferred: str = "stub") -> DigitalHumanAvatar | None:
    """Return an avatar implementation, or ``None`` to skip avatar rendering."""
    if preferred in ("none", "off", ""):
        return None
    if preferred == "stub":
        return StubAvatar()
    return DigitalHumanAvatar()  # raises on render(), letting ErrorCoach handle
