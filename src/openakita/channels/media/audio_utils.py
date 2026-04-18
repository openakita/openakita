"""
Audio format utilities — handles non-standard audio formats such as QQ/WeChat SILK v3.

QQ/WeChat voice files typically have the .amr extension, but the actual encoding is
Tencent's proprietary SILK v3, which standard ffmpeg cannot decode. This module
automatically detects and converts such files before passing them to Whisper.

Conversion pipeline:
  SILK (.amr/.silk/.slk) → pilk.decode → raw PCM → wave module → .wav → Whisper
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# SILK v3 file magic bytes — may start with '\x02' prefix (QQ) or directly with '#!SILK'
_SILK_MAGIC = b"#!SILK"
_SILK_MAGIC_QQ = b"\x02#!SILK"

# SILK default sample rate (QQ voice typically uses 24000 Hz)
_SILK_SAMPLE_RATE = 24000
# Whisper requires 16000 Hz mono 16-bit PCM
_TARGET_SAMPLE_RATE = 16000


def is_silk_file(file_path: str | Path) -> bool:
    """Check whether a file is in SILK v3 format (reads first 10 bytes to check magic bytes)."""
    try:
        with open(file_path, "rb") as f:
            head = f.read(10)
        return head.startswith(_SILK_MAGIC) or head.startswith(_SILK_MAGIC_QQ)
    except Exception:
        return False


def _silk_to_wav_pilk(silk_path: str, wav_path: str) -> bool:
    """
    Convert SILK to WAV using the pilk library.

    pilk.decode() outputs raw PCM (16-bit LE mono), which is then wrapped into .wav
    using the wave module.
    """
    try:
        import pilk  # type: ignore[import-untyped]
    except ImportError as e:
        from openakita.tools._import_helper import import_or_hint

        hint = import_or_hint("pilk")
        logger.warning(f"SILK decoding unavailable: {hint}")
        logger.warning(f"pilk ImportError details: {e}", exc_info=True)
        return False

    import wave

    # pilk.decode outputs a raw PCM file
    pcm_path = wav_path + ".pcm"
    try:
        # pilk.decode(silk_input, pcm_output, sample_rate) returns duration_ms
        duration_ms = pilk.decode(silk_path, pcm_path, _SILK_SAMPLE_RATE)
        logger.info(
            f"SILK decoded: {Path(silk_path).name} → PCM ({duration_ms}ms, {_SILK_SAMPLE_RATE}Hz)"
        )

        # Convert PCM to WAV (16-bit LE mono)
        with open(pcm_path, "rb") as pcm_f:
            pcm_data = pcm_f.read()

        with wave.open(wav_path, "wb") as wav_f:
            wav_f.setnchannels(1)
            wav_f.setsampwidth(2)  # 16-bit
            wav_f.setframerate(_SILK_SAMPLE_RATE)
            wav_f.writeframes(pcm_data)

        logger.info(f"WAV written: {Path(wav_path).name} ({len(pcm_data)} bytes PCM)")
        return True

    except Exception as e:
        logger.error(f"SILK → WAV conversion failed: {e}")
        return False
    finally:
        # Clean up temporary PCM file
        try:
            if os.path.exists(pcm_path):
                os.remove(pcm_path)
        except OSError:
            pass


def _ffmpeg_to_wav(src_path: str, wav_path: str) -> bool:
    """Convert non-standard audio formats to 16kHz mono WAV via ffmpeg."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not available for audio conversion")
        return False

    cmd = [
        "ffmpeg",
        "-i",
        src_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-sample_fmt",
        "s16",
        "-y",
        wav_path,
    ]
    try:
        extra: dict = {}
        if os.name == "nt":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.run(cmd, capture_output=True, timeout=30, check=True, **extra)
        logger.info(f"Audio converted via ffmpeg: {Path(src_path).name} → {Path(wav_path).name}")
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"ffmpeg conversion timed out for {Path(src_path).name}")
        return False
    except subprocess.CalledProcessError as e:
        logger.error(
            f"ffmpeg conversion failed for {Path(src_path).name}: {e.stderr[:300] if e.stderr else e}"
        )
        return False
    except Exception as e:
        logger.error(f"ffmpeg conversion error: {e}")
        return False


def ensure_whisper_compatible(audio_path: str) -> str:
    """
    Ensure the audio file can be processed by Whisper (ffmpeg).

    - SILK format → converted to WAV via pilk
    - opus/ogg/amr/webm/wma/aac → converted to WAV via ffmpeg
    - wav/mp3/flac and other standard formats → returned as-is

    Args:
        audio_path: Path to the original audio file

    Returns:
        Path to an audio file that Whisper can process (may be a converted WAV)
    """
    # 1. SILK format special handling (pilk conversion)
    if is_silk_file(audio_path):
        logger.info(f"Detected SILK format: {Path(audio_path).name}, converting to WAV...")

        src = Path(audio_path)
        wav_path = str(src.with_suffix(".wav"))

        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
            logger.info(f"Using cached WAV: {wav_path}")
            return wav_path

        if _silk_to_wav_pilk(str(src), wav_path):
            return wav_path

        logger.warning(
            f"SILK conversion failed for {src.name}. "
            "Falling back to original file (may fail with ffmpeg)."
        )
        return audio_path

    # 2. Non-standard formats → ffmpeg conversion to WAV (e.g. Feishu Opus, DingTalk OGG)
    src = Path(audio_path)
    suffix = src.suffix.lower()
    need_convert = {".opus", ".ogg", ".amr", ".webm", ".wma", ".aac"}
    if suffix in need_convert:
        wav_path = str(src.with_suffix(".wav"))
        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
            logger.info(f"Using cached WAV: {wav_path}")
            return wav_path

        if _ffmpeg_to_wav(str(src), wav_path):
            return wav_path

        logger.warning(f"ffmpeg conversion failed for {src.name}, returning original")
        return audio_path

    # 3. Standard formats like wav/mp3/flac are returned as-is
    return audio_path


def load_wav_as_numpy(wav_path: str, target_sr: int = 16000):
    """Load a WAV file directly as a Whisper-compatible float32 numpy array, without ffmpeg.

    When Whisper.transcribe() receives a numpy array, it skips the internal load_audio()
    step (and thus skips ffmpeg).

    Args:
        wav_path: Path to the WAV file
        target_sr: Target sample rate (Whisper defaults to 16000 Hz)

    Returns:
        numpy float32 array (mono, [-1, 1]), or None if loading fails
    """
    import wave

    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy not available, cannot load WAV directly")
        return None

    try:
        with wave.open(wav_path, "rb") as wf:
            sr = wf.getframerate()
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())

        if sample_width != 2:
            logger.debug(f"WAV sample_width={sample_width}, expected 2 (16-bit)")
            return None

        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        if n_channels > 1:
            audio = audio.reshape(-1, n_channels).mean(axis=1)

        if sr != target_sr:
            n_samples = int(len(audio) * target_sr / sr)
            audio = np.interp(
                np.linspace(0, len(audio), n_samples, endpoint=False),
                np.arange(len(audio)),
                audio,
            ).astype(np.float32)

        logger.debug(
            f"WAV loaded as numpy: {Path(wav_path).name}, sr={sr}→{target_sr}, samples={len(audio)}"
        )
        return audio
    except Exception as e:
        logger.warning(f"Failed to load WAV as numpy: {e}")
        return None


def ensure_llm_compatible(audio_path: str, target_format: str = "wav") -> str:
    """
    Ensure the audio file can be processed by LLM native audio input.

    LLM audio input typically requires:
    - OpenAI: wav, pcm16, mp3
    - Gemini: wav, mp3, flac, ogg
    - DashScope: wav, mp3

    Processing:
    - SILK → WAV (same logic as Whisper compatibility)
    - OGG/Opus → WAV (via ffmpeg)
    - AMR → WAV (via ffmpeg)
    - Other standard formats are returned as-is

    Args:
        audio_path: Path to the original audio file
        target_format: Target format (default "wav")

    Returns:
        Path to an audio file in an LLM-compatible format
    """
    import shutil
    import subprocess

    src = Path(audio_path)
    suffix = src.suffix.lower()

    # SILK format special handling
    if is_silk_file(audio_path):
        return ensure_whisper_compatible(audio_path)

    # Already in a target format, return as-is
    llm_native_formats = {".wav", ".mp3", ".flac", ".m4a"}
    if suffix in llm_native_formats:
        return audio_path

    # Formats that require ffmpeg conversion
    need_convert = {".ogg", ".opus", ".amr", ".webm", ".wma", ".aac"}
    if suffix not in need_convert:
        return audio_path

    out_path = str(src.with_suffix(f".{target_format}"))
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        logger.info(f"Using cached LLM-compatible audio: {out_path}")
        return out_path

    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not available for audio conversion")
        return audio_path

    cmd = [
        "ffmpeg",
        "-i",
        str(src),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-sample_fmt",
        "s16",
        "-y",
        out_path,
    ]
    try:
        extra: dict = {}
        if os.name == "nt":
            extra["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.run(cmd, capture_output=True, timeout=30, check=True, **extra)
        logger.info(f"Audio converted for LLM: {src.name} → {Path(out_path).name}")
        return out_path
    except Exception as e:
        logger.error(f"Audio conversion failed: {e}")
        return audio_path
