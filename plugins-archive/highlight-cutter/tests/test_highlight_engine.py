"""Unit tests for highlight-cutter engine (no ffmpeg / asr required)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add the plugin dir to sys.path so the local imports work
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from highlight_engine import (  # noqa: E402
    HighlightSegment,
    TranscriptChunk,
    keyword_score,
    pick_segments,
)


def test_keyword_score_assigns_higher_to_keyword_chunk() -> None:
    chunks = [
        TranscriptChunk(0, 5, "今天天气不错。"),
        TranscriptChunk(5, 12, "哇，太厉害了！这个结论你必须记住。"),
    ]
    scored = keyword_score(chunks)
    assert scored[1][1] > scored[0][1]
    assert "亮点关键词" in scored[1][2] or "完整句" in scored[1][2]


def test_pick_segments_distributes_across_buckets() -> None:
    chunks = [TranscriptChunk(i * 10.0, i * 10.0 + 5.0, f"chunk {i}") for i in range(10)]
    scored = [(c, 1.0, "uniform") for c in chunks]
    picked = pick_segments(scored, target_count=5, min_segment_sec=3.0,
                           max_segment_sec=10.0, total_duration=100.0)
    assert len(picked) == 5
    starts = [p.start for p in picked]
    # Should span the timeline, not cluster at start
    assert max(starts) >= 60
    assert all(starts[i] < starts[i + 1] for i in range(len(starts) - 1))


def test_pick_segments_respects_min_max() -> None:
    chunks = [
        TranscriptChunk(0, 1, "too short"),       # 1s — below min
        TranscriptChunk(10, 30, "good"),           # 20s — at max
        TranscriptChunk(50, 90, "way too long"),   # 40s — clamped
    ]
    scored = [(c, 1.0, "x") for c in chunks]
    picked = pick_segments(scored, target_count=3, min_segment_sec=3.0,
                           max_segment_sec=20.0, total_duration=100.0)
    # Each picked seg's duration must be <= max_segment_sec
    for p in picked:
        assert p.duration <= 20.0


def test_pick_segments_empty_input() -> None:
    assert pick_segments([], target_count=5) == []


def test_highlight_segment_to_dict_roundtrip() -> None:
    s = HighlightSegment(start=0, end=5, score=0.9, reason="r", text="t", label="l")
    d = s.to_dict()
    assert d["start"] == 0 and d["end"] == 5 and d["label"] == "l"
