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
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        hint = import_or_hint("pilk")
        logger.warning(f"SILK 解码不可用: {hint}")
        return False

    import wave

    # pilk.decode 输出 raw PCM 文件
    pcm_path = wav_path + ".pcm"
    try:
        # pilk.decode(silk_input, pcm_output, sample_rate) -> duration_ms
        duration_ms = pilk.decode(silk_path, pcm_path, _SILK_SAMPLE_RATE)
        logger.info(
            f"SILK decoded: {Path(silk_path).name} → PCM "
            f"({duration_ms}ms, {_SILK_SAMPLE_RATE}Hz)"
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


def ensure_whisper_compatible(audio_path: str) -> str:
    """
    确保音频文件可被 Whisper (ffmpeg) 处理。

    - 如果是 SILK 格式，自动转换为 WAV 并返回 WAV 路径
    - 如果不是 SILK 格式，原样返回

    Args:
        audio_path: 原始音频文件路径

    Returns:
        可被 Whisper 处理的音频文件路径（可能是转换后的 WAV）
    """
    if not is_silk_file(audio_path):
        return audio_path

    logger.info(f"Detected SILK format: {Path(audio_path).name}, converting to WAV...")

    # 生成 WAV 输出路径（与源文件同目录，避免跨盘）
    src = Path(audio_path)
    wav_path = str(src.with_suffix(".wav"))

    # 如果已转换过且文件存在，直接返回
    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
        logger.info(f"Using cached WAV: {wav_path}")
        return wav_path

    if _silk_to_wav_pilk(str(src), wav_path):
        return wav_path

    # 转换失败，返回原路径（让 Whisper/ffmpeg 尝试，虽然大概率还是会失败）
    logger.warning(
        f"SILK conversion failed for {src.name}. "
        "Falling back to original file (may fail with ffmpeg)."
    )
    return audio_path
