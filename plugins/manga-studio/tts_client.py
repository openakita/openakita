"""manga-studio TTS — Edge-TTS (free) and CosyVoice-v2 (paid via DashScope).

The two engines share one ``synth`` entry point: callers pass a voice
id, the client routes by engine. Edge ids look like ``zh-CN-XiaoyiNeural``;
CosyVoice ids look like ``longxiaochun``. ``manga_models.VOICES_BY_ID`` is
the single source of truth for the routing table.

Both engines:

- Are lazy-imported at first use (Pixelle C4 — never block plugin load
  on an optional vendor SDK).
- Surface a missing dependency as ``VendorError(kind=dependency)`` with
  the exact ``pip install`` command the user can run.
- Return ``{"bytes": <mp3-bytes>, "duration_sec": <float>}`` so the
  pipeline can wire the audio path into the FFmpeg mux step regardless
  of which engine produced it.

Edge-TTS specifics (mirrors avatar-studio/avatar_tts_edge.py — Pixelle
N1.7 — anti-ban hardening):

- ``Semaphore(3)`` caps concurrent synth calls.
- 0.3-0.6 s jitter between attempts.
- Retry on ``WSServerHandshakeError`` / ``NoAudioReceived``.

CosyVoice specifics: streamed via the dashscope SDK's WebSocket wrapper.
The SDK only ships in CN PyPI; we surface the pin
``dashscope>=1.20.0`` so users on minor versions don't hit the missing
``audio.tts_v2`` namespace.
"""

from __future__ import annotations

import asyncio
import logging
import random
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

from manga_inline.vendor_client import VendorError
from manga_models import VOICES_BY_ID

logger = logging.getLogger(__name__)


# ─── Engine routing ──────────────────────────────────────────────────────


def resolve_engine(voice_id: str) -> str:
    """Pick the TTS engine for ``voice_id``.

    Returns ``"edge"`` or ``"cosyvoice"``. Falls back to ``"edge"`` for
    unknown voice ids — Edge-TTS is free, so a misconfigured ``voice_id``
    doesn't burn the user's DashScope balance.
    """
    spec = VOICES_BY_ID.get(voice_id)
    if spec is not None:
        return spec.engine
    # Heuristic for ids that are no longer in the registry (e.g. user
    # cloned a CosyVoice and the registry was reloaded). Anything that
    # starts with cn-en-locale style prefix goes to Edge; everything else
    # to CosyVoice.
    if voice_id.startswith(("zh-", "en-")):
        return "edge"
    return "cosyvoice"


# ─── Edge-TTS path (free) ────────────────────────────────────────────────


_EDGE_SEMAPHORE = asyncio.Semaphore(3)


async def _synth_edge(
    *,
    text: str,
    voice_id: str,
    output_path: Path,
    speed: float = 1.0,
    retry_count: int = 3,
) -> dict[str, Any]:
    try:
        import edge_tts  # type: ignore[import-untyped]
    except ImportError as exc:
        raise VendorError(
            "Edge-TTS is not installed. Reinstall or repair the manga-studio plugin "
            "dependencies from OpenAkita Setup Center instead of installing into host Python.\n"
            "(it's a free Microsoft TTS package — no API key needed.)",
            kind="dependency",
            retryable=False,
        ) from exc

    rate = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    async with _EDGE_SEMAPHORE:
        await asyncio.sleep(random.uniform(0.3, 0.6))
        last_exc: Exception | None = None
        for attempt in range(retry_count):
            try:
                communicate = edge_tts.Communicate(text, voice_id, rate=rate)
                await communicate.save(str(out))
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001 - we re-raise after classifying
                last_exc = exc
                exc_name = type(exc).__name__
                if exc_name in ("WSServerHandshakeError", "NoAudioReceived"):
                    if attempt < retry_count - 1:
                        logger.warning(
                            "edge-tts attempt %d failed: %s; retrying...",
                            attempt + 1,
                            exc,
                        )
                        await asyncio.sleep(2.0 * (attempt + 1))
                        continue
                raise
        if last_exc is not None:
            raise last_exc

    duration = _probe_audio_duration(out)
    return {
        "bytes": out.read_bytes(),
        "duration_sec": duration,
        "engine": "edge",
        "voice_id": voice_id,
        "path": str(out),
    }


# ─── CosyVoice-v2 path (paid via DashScope) ──────────────────────────────


async def _synth_cosyvoice(
    *,
    text: str,
    voice_id: str,
    output_path: Path,
    api_key: str,
    audio_format: str = "mp3",
) -> dict[str, Any]:
    if not api_key:
        raise VendorError(
            "CosyVoice-v2 needs DashScope api_key (Settings → dashscope_api_key)",
            kind="auth",
            status=401,
        )
    try:
        import dashscope  # type: ignore[import-untyped]
        from dashscope.audio.tts_v2 import (  # type: ignore[import-untyped]
            AudioFormat,
            SpeechSynthesizer,
        )
    except ImportError as exc:
        raise VendorError(
            "CosyVoice-v2 needs the dashscope SDK. Reinstall or repair the manga-studio "
            "plugin dependencies from OpenAkita Setup Center instead of installing into host Python.\n"
            "(only the CosyVoice-v2 path needs this — Edge-TTS works "
            "without it.)",
            kind="dependency",
            retryable=False,
        ) from exc

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # The DashScope SDK's TTS path is sync-blocking (WebSocket inside),
    # so we drop into a thread to keep the asyncio loop responsive.
    fmt_enum = (
        AudioFormat.MP3_22050HZ_MONO_256KBPS
        if audio_format == "mp3"
        else AudioFormat.WAV_22050HZ_MONO_16BIT
    )

    def _run() -> bytes:
        # The SDK reads the api key from a module-level attribute. We set
        # it per call to avoid leaking it across plugin reloads.
        dashscope.api_key = api_key
        synth = SpeechSynthesizer(
            model="cosyvoice-v2",
            voice=f"{voice_id}_v2",  # _v2 is the canonical CosyVoice id
            format=fmt_enum,
        )
        audio_data = synth.call(text)
        if not isinstance(audio_data, (bytes, bytearray)) or not audio_data:
            raise VendorError(
                "CosyVoice-v2 returned empty audio",
                kind="server",
                retryable=True,
            )
        return bytes(audio_data)

    try:
        audio_bytes = await asyncio.to_thread(_run)
    except VendorError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify upstream errors
        # The SDK can raise anything from ConnectionError to its own
        # custom Exception subclasses. We bin the message into auth /
        # quota / network and let ErrorCoach surface a bilingual hint.
        msg = str(exc).lower()
        if "401" in msg or "unauthor" in msg or "invalid api" in msg:
            kind = "auth"
        elif "quota" in msg or "balance" in msg or "insufficient" in msg:
            kind = "quota"
        elif "timeout" in msg:
            kind = "timeout"
        elif "network" in msg or "connection" in msg:
            kind = "network"
        else:
            kind = "server"
        raise VendorError(f"cosyvoice-v2 synth failed: {exc}", kind=kind) from exc

    out.write_bytes(audio_bytes)
    duration = _probe_audio_duration(out)
    return {
        "bytes": audio_bytes,
        "duration_sec": duration,
        "engine": "cosyvoice",
        "voice_id": voice_id,
        "path": str(out),
    }


# ─── Public client ───────────────────────────────────────────────────────


class MangaTTSClient:
    """One TTS facade for both engines.

    Args:
        read_settings: Pixelle A10 hot-reload — reads
            ``dashscope_api_key`` for the CosyVoice path. Edge-TTS
            doesn't need any settings.
    """

    def __init__(
        self,
        *,
        read_settings: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._read_settings = read_settings

    def _current_settings(self) -> dict[str, Any]:
        if self._read_settings is None:
            return {}
        try:
            return self._read_settings() or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("manga-studio: read_settings raised %s", exc)
            return {}

    async def synth(
        self,
        *,
        text: str,
        voice_id: str,
        output_path: str | Path,
        speed: float = 1.0,
        audio_format: str = "mp3",
    ) -> dict[str, Any]:
        """Synthesise ``text`` into ``output_path`` using the engine
        appropriate for ``voice_id``. Returns
        ``{bytes, duration_sec, engine, voice_id, path}``."""
        if not text:
            raise ValueError("text must not be empty")
        if not voice_id:
            raise ValueError("voice_id must not be empty")
        out = Path(output_path)
        engine = resolve_engine(voice_id)
        if engine == "cosyvoice":
            api_key = str(self._current_settings().get("dashscope_api_key") or "").strip()
            return await _synth_cosyvoice(
                text=text,
                voice_id=voice_id,
                output_path=out,
                api_key=api_key,
                audio_format=audio_format,
            )
        return await _synth_edge(
            text=text,
            voice_id=voice_id,
            output_path=out,
            speed=speed,
        )


# ─── Audio duration helpers ──────────────────────────────────────────────
#
# Two-stage probe: prefer mutagen for accuracy, fall back to a manual
# RIFF parse for WAV. mp3 without mutagen is unreliable so we just
# return a 5 s placeholder — the pipeline's downstream FFmpeg step will
# remux the audio with a fresh duration probe anyway.


def _probe_audio_duration(path: Path) -> float:
    try:
        from mutagen import File as MutagenFile  # type: ignore[import-untyped]

        info = MutagenFile(str(path))
        if info is not None and getattr(info, "info", None):
            return float(info.info.length)
    except Exception:  # noqa: BLE001 - duration is a hint, not a hard requirement
        pass
    try:
        data = path.read_bytes()
        if data[:4] == b"RIFF" and len(data) > 44:
            byte_rate = struct.unpack_from("<I", data, 28)[0]
            if byte_rate > 0:
                data_size = len(data) - 44
                return data_size / byte_rate
    except Exception:  # noqa: BLE001
        pass
    return 5.0
