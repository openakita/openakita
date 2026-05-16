"""Edge-TTS engine — free Microsoft TTS with 12 Chinese voices.

Mirrors the pattern from
``plugins/avatar-studio/avatar_tts_edge.py`` (which itself follows
Pixelle-Video's ``tts_util.py``):

- ``Semaphore(3)`` to avoid Microsoft's per-IP rate-limit ban.
- Random inter-request delay (0.3-0.6 s) so concurrent submits aren't
  hammering the WS handshake at the same instant.
- Retry on ``WSServerHandshakeError`` / ``NoAudioReceived`` with linear
  backoff.
- ``mutagen`` for MP3 duration; falls back to a WAV byte-rate calc, or
  finally a 5-second placeholder so the cost preview never blows up
  with a None.

The voice catalog mirrors :data:`happyhorse_models.EDGE_VOICES` so
the UI can render the same labels regardless of which list it pulls
from. Both lists carry the same ``id`` keys; this module adds the
optional ``zh-CN-XiaomoNeural`` / ``zh-CN-XiaoruiNeural`` etc. styles
for completeness.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Edge-TTS voice catalog — kept locally so this module is import-safe even
# if the data layer is loaded later. The two lists must agree on the ``id``
# values (the public Microsoft Edge voice id passed to ``edge_tts``).
EDGE_VOICES: list[dict[str, str]] = [
    {"id": "zh-CN-YunxiNeural", "label": "云希（男-阳光）", "gender": "male"},
    {"id": "zh-CN-YunjianNeural", "label": "云健（男-沉稳）", "gender": "male"},
    {"id": "zh-CN-YunxiaNeural", "label": "云夏（男-少年）", "gender": "male"},
    {"id": "zh-CN-YunyangNeural", "label": "云扬（男-新闻）", "gender": "male"},
    {"id": "zh-CN-XiaoxiaoNeural", "label": "晓晓（女-温柔）", "gender": "female"},
    {"id": "zh-CN-XiaoyiNeural", "label": "晓伊（女-甜美）", "gender": "female"},
    {"id": "zh-CN-XiaochenNeural", "label": "晓辰（女-知性）", "gender": "female"},
    {"id": "zh-CN-XiaohanNeural", "label": "晓涵（女-沉静）", "gender": "female"},
    {"id": "zh-CN-XiaomoNeural", "label": "晓墨（女-柔美）", "gender": "female"},
    {"id": "zh-CN-XiaoqiuNeural", "label": "晓秋（女-优雅）", "gender": "female"},
    {"id": "zh-CN-XiaoruiNeural", "label": "晓睿（女-甜美）", "gender": "female"},
    {"id": "zh-CN-XiaoshuangNeural", "label": "晓双（女-童声）", "gender": "female"},
    # Regional / Cantonese / Taiwan — kept for the `Voices` Tab when the
    # user's audience is non-mainland.
    {"id": "zh-CN-liaoning-XiaobeiNeural", "label": "晓贝（辽宁口音）", "gender": "female"},
    {"id": "zh-CN-shaanxi-XiaoniNeural", "label": "晓妮（陕西口音）", "gender": "female"},
    {"id": "zh-HK-HiuMaanNeural", "label": "曉曼（粤语-温柔）", "gender": "female"},
    {"id": "zh-HK-WanLungNeural", "label": "雲龍（粤语-沉稳）", "gender": "male"},
    {"id": "zh-TW-HsiaoChenNeural", "label": "曉臻（台湾）", "gender": "female"},
    {"id": "zh-TW-YunJheNeural", "label": "雲哲（台湾）", "gender": "male"},
]

EDGE_VOICES_BY_ID: dict[str, dict[str, str]] = {v["id"]: v for v in EDGE_VOICES}

# Microsoft Edge's WS endpoint will start dropping requests over ~3 in
# parallel from a single IP. Keep this conservative even though the
# plugin itself only ever runs one TTS at a time — long-video chains
# can fan out into multiple synth calls.
_SEMAPHORE = asyncio.Semaphore(3)

DEFAULT_VOICE_ID = "zh-CN-YunxiNeural"
DEFAULT_RETRY_COUNT = 3


class EdgeTtsDependencyError(RuntimeError):
    """Raised when ``edge-tts`` is not installed.

    The plugin layer maps this to the ``dependency`` ``error_kind`` so
    the UI surfaces "Install via Settings → Python deps" rather than a
    confusing generic error.
    """


def list_voices() -> list[dict[str, str]]:
    """Return the public Edge voice catalog (used by ``GET /catalog``)."""
    return [dict(v) for v in EDGE_VOICES]


def is_edge_voice(voice_id: str) -> bool:
    return voice_id in EDGE_VOICES_BY_ID


async def synth_voice(
    text: str,
    voice: str,
    output_path: str | Path,
    *,
    speed: float = 1.0,
    retry_count: int = DEFAULT_RETRY_COUNT,
) -> dict[str, Any]:
    """Synthesise speech via edge-tts and write to ``output_path``.

    Returns ``{"bytes": <bytes>, "duration_sec": <float>}`` matching the
    cosyvoice path so the pipeline can swap engines without branching.

    Raises:
        EdgeTtsDependencyError: ``edge-tts`` is not installed.
        Exception: any underlying edge-tts error after retries are exhausted.
    """
    try:
        import edge_tts
    except ImportError as e:
        raise EdgeTtsDependencyError(
            "edge-tts is not installed. Settings → Python 依赖 → 一键安装，"
            "或在 OpenAkita 运行的 Python 环境中执行 `pip install edge-tts>=7`"
        ) from e

    if not text or not text.strip():
        raise ValueError("text must be non-empty for Edge-TTS")

    if not is_edge_voice(voice):
        logger.warning(
            "edge-tts: unknown voice %r, falling back to %s",
            voice,
            DEFAULT_VOICE_ID,
        )
        voice = DEFAULT_VOICE_ID

    rate = f"+{int((speed - 1) * 100)}%" if speed >= 1 else f"{int((speed - 1) * 100)}%"
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    async with _SEMAPHORE:
        # Random spread so concurrent submits don't all open WS at once.
        await asyncio.sleep(random.uniform(0.3, 0.6))
        last_exc: Exception | None = None
        for attempt in range(max(1, retry_count)):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await communicate.save(str(out))
                break
            except Exception as exc:  # noqa: BLE001 — edge_tts uses a wide
                # exception surface (NoAudioReceived / WSServerHandshakeError /
                # aiohttp.ClientError) we deliberately catch broadly.
                last_exc = exc
                exc_name = type(exc).__name__
                if exc_name in {"WSServerHandshakeError", "NoAudioReceived"} and attempt < retry_count - 1:
                    logger.warning(
                        "edge-tts attempt %d for voice=%s failed: %s — retrying",
                        attempt + 1,
                        voice,
                        exc,
                    )
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                raise
        else:
            if last_exc is not None:
                raise last_exc

    # NOTE: the caller (happyhorse_pipeline._step_tts_synth) only reads
    # ``duration_sec`` / ``format`` and uses the on-disk path directly
    # for the subsequent OSS upload, so we no longer read the full file
    # back into memory. This drops one 5-10 MB allocation per call and
    # avoids a wasted round-trip through the page cache on long videos.
    duration = _get_duration(out)
    return {
        "path": str(out),
        "duration_sec": duration,
        "format": "mp3",
    }


def _get_duration(path: Path) -> float:
    """Best-effort audio length in seconds. mutagen → wav header → 5 s."""
    try:
        from mutagen.mp3 import MP3

        return float(MP3(str(path)).info.length)
    except Exception:  # noqa: BLE001
        pass
    try:
        import struct

        data = path.read_bytes()
        if data[:4] == b"RIFF" and len(data) > 44:
            byte_rate = struct.unpack_from("<I", data, 28)[0]
            if byte_rate > 0:
                data_size = len(data) - 44
                return data_size / byte_rate
    except Exception:  # noqa: BLE001
        pass
    # 5 s placeholder lets the cost preview render *something* — the
    # pipeline writes the real duration back later from the actual file.
    return 5.0
