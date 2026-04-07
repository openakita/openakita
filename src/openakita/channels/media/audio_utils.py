"""
音频格式工具 —— 处理 QQ/微信 SILK v3 等非标准音频格式。

QQ/微信的语音文件扩展名通常是 .amr，但实际编码是腾讯私有的 SILK v3，
标准 ffmpeg 无法解码。本模块在调用 Whisper 之前自动检测并转换。

转换链路:
  SILK (.amr/.silk/.slk) → pilk.decode → raw PCM → wave 模块 → .wav → Whisper
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# SILK v3 文件魔数 —— 可能以 '\x02' 前缀开头（QQ），也可能直接以 '#!SILK' 开头
_SILK_MAGIC = b"#!SILK"
_SILK_MAGIC_QQ = b"\x02#!SILK"

# SILK 默认采样率（QQ 语音一般是 24000 Hz）
_SILK_SAMPLE_RATE = 24000
# Whisper 要求 16000 Hz 单声道 16-bit PCM
_TARGET_SAMPLE_RATE = 16000


def is_silk_file(file_path: str | Path) -> bool:
    """检测文件是否为 SILK v3 格式（读取前 10 字节检查魔数）"""
    try:
        with open(file_path, "rb") as f:
            head = f.read(10)
        return head.startswith(_SILK_MAGIC) or head.startswith(_SILK_MAGIC_QQ)
    except Exception:
        return False


def _silk_to_wav_pilk(silk_path: str, wav_path: str) -> bool:
    """
    使用 pilk 库将 SILK 转换为 WAV。

    pilk.decode() 输出 raw PCM (16-bit LE mono)，再用 wave 模块包装成 .wav。
    """
    try:
        import pilk  # type: ignore[import-untyped]
    except ImportError as e:
        from openakita.tools._import_helper import import_or_hint

        hint = import_or_hint("pilk")
        logger.warning(f"SILK 解码不可用: {hint}")
        logger.warning(f"pilk ImportError 详情: {e}", exc_info=True)
        return False

    import wave

    # pilk.decode 输出 raw PCM 文件
    pcm_path = wav_path + ".pcm"
    try:
        # pilk.decode(silk_input, pcm_output, sample_rate) -> duration_ms
        duration_ms = pilk.decode(silk_path, pcm_path, _SILK_SAMPLE_RATE)
        logger.info(
            f"SILK decoded: {Path(silk_path).name} → PCM ({duration_ms}ms, {_SILK_SAMPLE_RATE}Hz)"
        )

        # PCM → WAV (16-bit LE mono)
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
        # 清理临时 PCM 文件
        try:
            if os.path.exists(pcm_path):
                os.remove(pcm_path)
        except OSError:
            pass


def _ffmpeg_to_wav(src_path: str, wav_path: str) -> bool:
    """通过 ffmpeg 将非标准音频格式转换为 16kHz mono WAV。"""
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
    确保音频文件可被 Whisper (ffmpeg) 处理。

    - SILK 格式 → pilk 转换为 WAV
    - opus/ogg/amr/webm/wma/aac → ffmpeg 转换为 WAV
    - wav/mp3/flac 等标准格式 → 原样返回

    Args:
        audio_path: 原始音频文件路径

    Returns:
        可被 Whisper 处理的音频文件路径（可能是转换后的 WAV）
    """
    # 1. SILK 格式特殊处理（pilk 转换）
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

    # 2. 非标准格式 → ffmpeg 转 WAV（飞书 Opus、钉钉 OGG 等）
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

    # 3. wav/mp3/flac 等标准格式原样返回
    return audio_path


def load_wav_as_numpy(wav_path: str, target_sr: int = 16000):
    """直接加载 WAV 为 Whisper 兼容的 float32 numpy 数组，无需 ffmpeg。

    Whisper.transcribe() 接受 numpy 数组时跳过内部 load_audio()（即跳过 ffmpeg）。

    Args:
        wav_path: WAV 文件路径
        target_sr: 目标采样率（Whisper 默认 16000Hz）

    Returns:
        numpy float32 数组（单声道, [-1, 1]），如果加载失败返回 None
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
    确保音频文件可被 LLM 原生音频输入处理。

    LLM 音频输入通常要求:
    - OpenAI: wav, pcm16, mp3
    - Gemini: wav, mp3, flac, ogg
    - DashScope: wav, mp3

    处理:
    - SILK → WAV（与 Whisper 兼容逻辑相同）
    - OGG/Opus → WAV（通过 ffmpeg）
    - AMR → WAV（通过 ffmpeg）
    - 其他标准格式原样返回

    Args:
        audio_path: 原始音频文件路径
        target_format: 目标格式 (默认 "wav")

    Returns:
        LLM 兼容格式的音频文件路径
    """
    import shutil
    import subprocess

    src = Path(audio_path)
    suffix = src.suffix.lower()

    # SILK 格式特殊处理
    if is_silk_file(audio_path):
        return ensure_whisper_compatible(audio_path)

    # 已经是目标格式，直接返回
    llm_native_formats = {".wav", ".mp3", ".flac", ".m4a"}
    if suffix in llm_native_formats:
        return audio_path

    # 需要通过 ffmpeg 转换的格式
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
