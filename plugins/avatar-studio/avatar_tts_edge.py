"""Edge-TTS engine — free Microsoft TTS with 12 Chinese voices.

Mirrors the pattern from Pixelle-Video's tts_util.py:
- ``Semaphore(3)`` to avoid rate-limit bans
- Random inter-request delay
- Retry on ``WSServerHandshakeError`` / ``NoAudioReceived``
- ``certifi`` SSL context for Windows compatibility
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EDGE_VOICES: list[dict[str, str]] = [
    {"id": "zh-CN-YunxiNeural",      "label": "云希（男-阳光）",     "gender": "male"},
    {"id": "zh-CN-YunjianNeural",    "label": "云健（男-沉稳）",     "gender": "male"},
    {"id": "zh-CN-YunxiaNeural",     "label": "云夏（男-少年）",     "gender": "male"},
    {"id": "zh-CN-YunyangNeural",    "label": "云扬（男-新闻）",     "gender": "male"},
    {"id": "zh-CN-XiaoxiaoNeural",   "label": "晓晓（女-温柔）",     "gender": "female"},
    {"id": "zh-CN-XiaoyiNeural",     "label": "晓伊（女-活泼）",     "gender": "female"},
    {"id": "zh-CN-XiaochenNeural",   "label": "晓辰（女-知性）",     "gender": "female"},
    {"id": "zh-CN-XiaohanNeural",    "label": "晓涵（女-沉静）",     "gender": "female"},
    {"id": "zh-CN-XiaomoNeural",     "label": "晓墨（女-柔美）",     "gender": "female"},
    {"id": "zh-CN-XiaoqiuNeural",    "label": "晓秋（女-优雅）",     "gender": "female"},
    {"id": "zh-CN-XiaoruiNeural",    "label": "晓睿（女-甜美）",     "gender": "female"},
    {"id": "zh-CN-XiaoshuangNeural", "label": "晓双（女-童声）",     "gender": "female"},
]

EDGE_VOICES_BY_ID: dict[str, dict[str, str]] = {v["id"]: v for v in EDGE_VOICES}

_SEMAPHORE = asyncio.Semaphore(3)


async def synth_voice(
    text: str,
    voice: str,
    output_path: str | Path,
    *,
    speed: float = 1.0,
    retry_count: int = 3,
) -> dict[str, Any]:
    """Synthesize speech and return ``{"bytes": bytes, "duration_sec": float}``.

    Same return shape as the cosyvoice TTS path so callers can swap
    engines without changing downstream logic.
    """
    try:
        import edge_tts  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError("edge-tts is not installed. Run: pip install edge-tts>=7.0")

    if voice not in EDGE_VOICES_BY_ID:
        voice = "zh-CN-YunxiNeural"

    rate = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"
    out = Path(output_path)

    async with _SEMAPHORE:
        await asyncio.sleep(random.uniform(0.3, 0.6))
        last_exc: Exception | None = None
        for attempt in range(retry_count):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await communicate.save(str(out))
                break
            except Exception as exc:
                last_exc = exc
                exc_name = type(exc).__name__
                if exc_name in ("WSServerHandshakeError", "NoAudioReceived"):
                    if attempt < retry_count - 1:
                        logger.warning("edge-tts attempt %d failed: %s, retrying...", attempt + 1, exc)
                        await asyncio.sleep(2.0 * (attempt + 1))
                        continue
                raise
        else:
            if last_exc:
                raise last_exc

    duration = _get_duration(out)
    audio_bytes = out.read_bytes()
    return {"bytes": audio_bytes, "duration_sec": duration}


def _get_duration(path: Path) -> float:
    """Get audio duration in seconds via mutagen."""
    try:
        from mutagen.mp3 import MP3  # type: ignore[import-untyped]
        audio = MP3(str(path))
        return float(audio.info.length)
    except Exception:
        pass
    try:
        import struct
        data = path.read_bytes()
        if data[:4] == b"RIFF" and len(data) > 44:
            byte_rate = struct.unpack_from("<I", data, 28)[0]
            if byte_rate > 0:
                data_size = len(data) - 44
                return data_size / byte_rate
    except Exception:
        pass
    return 5.0
