"""Tests for clip_ffmpeg_ops.py — silence detection + SRT generation + helpers."""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

import pytest

from clip_ffmpeg_ops import (
    FFmpegOps,
    _detect_silence_sync,
    _escape_subtitle_path,
    _srt_ts,
)


class TestSrtTimestamp:
    def test_zero(self):
        assert _srt_ts(0.0) == "00:00:00,000"

    def test_basic(self):
        assert _srt_ts(65.5) == "00:01:05,500"

    def test_hours(self):
        assert _srt_ts(3723.123) == "01:02:03,123"


class TestEscapeSubtitlePath:
    def test_windows_colon(self):
        result = _escape_subtitle_path("C:\\Users\\test\\sub.srt")
        assert "\\:" in result
        assert "\\" not in result or "\\:" in result

    def test_backslash_to_forward(self):
        result = _escape_subtitle_path("C:\\Users\\test\\sub.srt")
        assert "\\\\" not in result.replace("\\:", "XX")

    def test_single_quote(self):
        result = _escape_subtitle_path("/tmp/it's a test.srt")
        assert "\\'" in result


class TestSrtGeneration:
    def test_basic(self):
        sentences = [
            {"start": 0.0, "end": 2.5, "text": "Hello world"},
            {"start": 3.0, "end": 5.0, "text": "Second line"},
        ]
        srt = FFmpegOps.generate_srt(sentences)
        assert "1\n00:00:00,000 --> 00:00:02,500\nHello world" in srt
        assert "2\n00:00:03,000 --> 00:00:05,000\nSecond line" in srt

    def test_out_end_fix(self):
        sentences = [
            {"start": 10.0, "end": 10.0, "text": "Zero length"},
            {"start": 15.0, "end": 14.0, "text": "Reversed"},
        ]
        srt = FFmpegOps.generate_srt(sentences)
        assert "00:00:10,400" in srt
        assert "00:00:15,400" in srt

    def test_empty_text_skipped(self):
        sentences = [
            {"start": 0, "end": 1, "text": ""},
            {"start": 1, "end": 2, "text": "  "},
            {"start": 2, "end": 3, "text": "Valid"},
        ]
        srt = FFmpegOps.generate_srt(sentences)
        assert "1\n" in srt
        assert "2\n" not in srt

    def test_with_segment_filter(self):
        sentences = [
            {"start": 0, "end": 5, "text": "Before"},
            {"start": 10, "end": 15, "text": "Inside"},
            {"start": 20, "end": 25, "text": "After"},
        ]
        segments = [{"start": 9, "end": 16}]
        srt = FFmpegOps.generate_srt(sentences, segments=segments)
        assert "Inside" in srt
        assert "Before" not in srt
        assert "After" not in srt


def _make_test_wav(path: str, duration_sec: float = 1.0, freq: float = 440.0, sr: int = 16000, amplitude: float = 0.5):
    """Create a test WAV file with a sine wave."""
    n_samples = int(sr * duration_sec)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for i in range(n_samples):
            val = int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / sr))
            wf.writeframes(struct.pack("<h", val))


def _make_silence_wav(path: str, duration_sec: float = 1.0, sr: int = 16000):
    """Create a silent WAV file."""
    n_samples = int(sr * duration_sec)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * n_samples)


class TestSilenceDetection:
    def test_pure_silence(self, tmp_path: Path):
        wav = str(tmp_path / "silence.wav")
        _make_silence_wav(wav, duration_sec=2.0)
        result = _detect_silence_sync(wav)
        assert len(result) >= 1
        total_silence = sum(s["duration"] for s in result)
        assert total_silence > 1.5

    def test_pure_tone(self, tmp_path: Path):
        wav = str(tmp_path / "tone.wav")
        _make_test_wav(wav, duration_sec=1.0, amplitude=0.8)
        result = _detect_silence_sync(wav, threshold_db=-20.0)
        assert len(result) == 0

    def test_tone_then_silence(self, tmp_path: Path):
        wav = str(tmp_path / "mixed.wav")
        sr = 16000
        n_tone = sr * 1
        n_silence = sr * 1

        with wave.open(wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            for i in range(n_tone):
                val = int(0.5 * 32767 * math.sin(2 * math.pi * 440 * i / sr))
                wf.writeframes(struct.pack("<h", val))
            wf.writeframes(b"\x00\x00" * n_silence)

        result = _detect_silence_sync(wav, threshold_db=-20.0, min_silence_sec=0.3)
        assert len(result) >= 1
        has_trailing = any(s["end"] > 1.5 for s in result)
        assert has_trailing

    def test_empty_file(self, tmp_path: Path):
        wav = str(tmp_path / "empty.wav")
        sr = 16000
        with wave.open(wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(b"\x00\x00" * 100)
        result = _detect_silence_sync(wav)
        assert isinstance(result, list)

    def test_nonexistent_file(self):
        result = _detect_silence_sync("/nonexistent/path.wav")
        assert result == []


class TestFFmpegOps:
    def test_detect_returns_dict(self):
        ops = FFmpegOps()
        result = ops.detect()
        assert "available" in result
        assert "version" in result
        assert "path" in result

    def test_generate_srt_is_static(self):
        result = FFmpegOps.generate_srt([{"start": 0, "end": 1, "text": "hi"}])
        assert "hi" in result
