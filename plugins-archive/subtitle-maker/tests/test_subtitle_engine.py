"""subtitle-maker engine tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from subtitle_engine import (  # noqa: E402
    TranscriptChunk, burn_subtitles_command, to_srt, to_vtt,
)


def _sample() -> list[TranscriptChunk]:
    return [
        TranscriptChunk(0.0, 1.5, "Hello, world."),
        TranscriptChunk(1.5, 4.25, "这是中文字幕。"),
        TranscriptChunk(4.25, 4.25, ""),  # empty — should be skipped
    ]


def test_to_srt_format() -> None:
    out = to_srt(_sample())
    assert "1\n00:00:00,000 --> 00:00:01,500\nHello, world." in out
    assert "2\n00:00:01,500 --> 00:00:04,250\n这是中文字幕。" in out
    # The empty third chunk should be skipped
    assert "3\n" not in out


def test_to_vtt_format_starts_with_header() -> None:
    out = to_vtt(_sample())
    assert out.startswith("WEBVTT\n")
    assert "00:00:01.500 --> 00:00:04.250" in out


def test_to_srt_handles_negative_and_overflow() -> None:
    out = to_srt([TranscriptChunk(-1.0, 0.999, "neg start"),
                  TranscriptChunk(3601.5, 3661.0, "long")])
    assert "00:00:00,000 --> 00:00:00,999" in out
    assert "01:00:01,500 --> 01:01:01,000" in out


def test_burn_subtitles_command_uses_subtitle_filter(tmp_path) -> None:
    src = tmp_path / "v.mp4"; src.write_text("")
    srt = tmp_path / "s.srt"; srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n")
    out = tmp_path / "o.mp4"
    cmd = burn_subtitles_command(source_video=src, srt_file=srt, output=out, ffmpeg="ffmpeg")
    joined = " ".join(cmd)
    assert "subtitles=" in joined
    assert "yuv420p" in joined
    assert str(out) in joined
