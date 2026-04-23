"""Tests for clip_pipeline.py — 4 modes + error handling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clip_pipeline import (
    ClipPipelineContext,
    PipelineError,
    _classify_error,
    _merge_overlapping,
    run_pipeline,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_ctx(
    tmp_path: Path,
    mode: str = "silence_clean",
    params: dict | None = None,
) -> ClipPipelineContext:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video content for testing")
    task_dir = tmp_path / "task_001"
    return ClipPipelineContext(
        task_id="test001",
        mode=mode,
        params=params or {},
        task_dir=task_dir,
        source_video_path=source,
    )


def _make_mock_tm():
    tm = AsyncMock()
    tm.update_task = AsyncMock()
    tm.get_transcript_by_hash = AsyncMock(return_value=None)
    tm.create_transcript = AsyncMock(return_value={"id": "tr001"})
    tm.update_transcript = AsyncMock()
    return tm


def _make_mock_ffmpeg(available: bool = True, duration: float = 60.0):
    ffmpeg = MagicMock()
    ffmpeg.available = available
    ffmpeg.get_duration = AsyncMock(return_value=duration)
    ffmpeg.extract_audio = AsyncMock(return_value=Path("/tmp/audio.wav"))
    ffmpeg.detect_silence = AsyncMock(return_value=[
        {"start": 5.0, "end": 8.0, "duration": 3.0},
        {"start": 20.0, "end": 22.0, "duration": 2.0},
    ])
    ffmpeg.cut_segments = AsyncMock(return_value=Path("/tmp/output.mp4"))
    ffmpeg.remove_segments = AsyncMock(return_value=Path("/tmp/output.mp4"))
    ffmpeg.burn_subtitles = AsyncMock(return_value=Path("/tmp/subtitled.mp4"))
    return ffmpeg


def _make_mock_asr():
    from clip_asr_client import TranscriptResult, TranscriptSentence
    asr = AsyncMock()
    asr.transcribe = AsyncMock(return_value=TranscriptResult(
        sentences=[
            TranscriptSentence(start=0.0, end=5.0, text="Hello world"),
            TranscriptSentence(start=5.5, end=10.0, text="This is a test"),
        ],
        full_text="Hello world This is a test",
        language="zh",
        duration_sec=10.0,
        api_task_id="task_abc",
    ))
    asr.analyze_highlights = AsyncMock(return_value=[
        {"start_sec": 0, "end_sec": 5, "reason": "intro", "score": 8},
    ])
    asr.analyze_topics = AsyncMock(return_value=[
        {"title": "Intro", "start_sec": 0, "end_sec": 30, "summary": "introduction"},
    ])
    asr.analyze_filler = AsyncMock(return_value=[
        {"start_sec": 3, "end_sec": 4, "type": "filler", "content": "um"},
    ])
    return asr


class TestSilenceCleanPipeline:
    def test_happy_path(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="silence_clean")
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg()
        emit = MagicMock()

        run(run_pipeline(ctx, tm, None, ffmpeg, emit))

        tm.update_task.assert_any_call("test001", status="succeeded", pipeline_step="done")
        assert any(
            call.kwargs.get("status") == "succeeded" or
            (len(call.args) > 1 and call.args[1].get("status") == "succeeded")
            for call in emit.call_args_list
        )

    def test_skips_transcribe_and_analyze(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="silence_clean")
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg()
        emit = MagicMock()

        run(run_pipeline(ctx, tm, None, ffmpeg, emit))

        step_names = [
            call.args[1].get("step") if len(call.args) > 1 else call.kwargs.get("step")
            for call in emit.call_args_list
            if (len(call.args) > 1 and call.args[1].get("step")) or call.kwargs.get("step")
        ]
        steps_seen = set()
        for c in emit.call_args_list:
            data = c.args[1] if len(c.args) > 1 else {}
            if "step" in data:
                steps_seen.add(data["step"])

        assert "transcribe" not in steps_seen
        assert "analyze" not in steps_seen
        assert "setup" in steps_seen
        assert "execute" in steps_seen


class TestHighlightExtractPipeline:
    def test_happy_path(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="highlight_extract")
        ctx.source_url = "http://example.com/video.mp4"
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg()
        asr = _make_mock_asr()
        emit = MagicMock()

        run(run_pipeline(ctx, tm, asr, ffmpeg, emit))
        tm.update_task.assert_any_call("test001", status="succeeded", pipeline_step="done")


class TestTopicSplitPipeline:
    def test_happy_path(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="topic_split")
        ctx.source_url = "http://example.com/video.mp4"
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg()
        asr = _make_mock_asr()
        emit = MagicMock()

        run(run_pipeline(ctx, tm, asr, ffmpeg, emit))
        tm.update_task.assert_any_call("test001", status="succeeded", pipeline_step="done")


class TestTalkingPolishPipeline:
    def test_happy_path(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="talking_polish")
        ctx.source_url = "http://example.com/video.mp4"
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg()
        asr = _make_mock_asr()
        emit = MagicMock()

        run(run_pipeline(ctx, tm, asr, ffmpeg, emit))
        tm.update_task.assert_any_call("test001", status="succeeded", pipeline_step="done")

    def test_remove_flags_filter_segments(self, tmp_path: Path):
        """remove_filler/stutter/repetition toggles must actually filter
        the analyze_filler output before passing to remove_segments.
        Regression guard: prior to the fix the toggles were silently dropped
        by the Pydantic model and unused by the pipeline."""
        ctx = _make_ctx(tmp_path, mode="talking_polish")
        ctx.source_url = "http://example.com/video.mp4"
        ctx.params = {
            "remove_filler": False,
            "remove_stutter": True,
            "remove_repetition": False,
        }
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg()
        asr = _make_mock_asr()
        asr.analyze_filler = AsyncMock(return_value=[
            {"start_sec": 1, "end_sec": 2, "type": "filler", "content": "um"},
            {"start_sec": 5, "end_sec": 6, "type": "stutter", "content": "I-I"},
            {"start_sec": 9, "end_sec": 10, "type": "repetition", "content": "the the"},
        ])
        # detect_silence returns nothing so remove_segments only sees
        # filler-analysis segments (filtered by toggle).
        ffmpeg.detect_silence = AsyncMock(return_value=[])
        emit = MagicMock()

        run(run_pipeline(ctx, tm, asr, ffmpeg, emit))

        # Exactly one segment (the stutter) must be passed to remove_segments.
        assert ffmpeg.remove_segments.await_count == 1
        passed = ffmpeg.remove_segments.await_args.args[1]
        assert len(passed) == 1
        assert passed[0]["start"] == 5 and passed[0]["end"] == 6


class TestPipelineErrors:
    def test_ffmpeg_not_available(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="silence_clean")
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg(available=False)
        emit = MagicMock()

        run(run_pipeline(ctx, tm, None, ffmpeg, emit))

        has_fail = any(
            c.args[1].get("status") == "failed" if len(c.args) > 1 else False
            for c in emit.call_args_list
        )
        assert has_fail

    def test_no_api_key_for_highlight(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="highlight_extract")
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg()
        emit = MagicMock()

        run(run_pipeline(ctx, tm, None, ffmpeg, emit))

        error_emits = [
            c.args[1] for c in emit.call_args_list
            if len(c.args) > 1 and c.args[1].get("error_kind") == "auth"
        ]
        assert len(error_emits) > 0

    def test_duration_exceeded(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="silence_clean")
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg(duration=10000.0)
        emit = MagicMock()

        run(run_pipeline(ctx, tm, None, ffmpeg, emit))

        error_emits = [
            c.args[1] for c in emit.call_args_list
            if len(c.args) > 1 and c.args[1].get("error_kind") == "duration"
        ]
        assert len(error_emits) > 0

    def test_cancelled_task(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="silence_clean")
        ctx.cancelled = True
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg()
        emit = MagicMock()

        run(run_pipeline(ctx, tm, None, ffmpeg, emit))

        cancel_emits = [
            c.args[1] for c in emit.call_args_list
            if len(c.args) > 1 and c.args[1].get("status") == "cancelled"
        ]
        assert len(cancel_emits) > 0

    def test_source_not_found(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="silence_clean")
        ctx.source_video_path = Path("/nonexistent/video.mp4")
        tm = _make_mock_tm()
        ffmpeg = _make_mock_ffmpeg()
        emit = MagicMock()

        run(run_pipeline(ctx, tm, None, ffmpeg, emit))

        error_emits = [
            c.args[1] for c in emit.call_args_list
            if len(c.args) > 1 and c.args[1].get("error_kind") == "format"
        ]
        assert len(error_emits) > 0


class TestHelpers:
    def test_classify_error_timeout(self):
        assert _classify_error(TimeoutError("timeout")) == "timeout"

    def test_classify_error_network(self):
        assert _classify_error(ConnectionError("connection refused")) == "network"

    def test_classify_error_auth(self):
        assert _classify_error(Exception("401 unauthorized")) == "auth"

    def test_classify_error_unknown(self):
        assert _classify_error(ValueError("something")) == "unknown"

    def test_merge_overlapping_empty(self):
        assert _merge_overlapping([]) == []

    def test_merge_overlapping_no_overlap(self):
        result = _merge_overlapping([
            {"start": 0, "end": 5},
            {"start": 10, "end": 15},
        ])
        assert len(result) == 2

    def test_merge_overlapping_with_overlap(self):
        result = _merge_overlapping([
            {"start": 0, "end": 5},
            {"start": 4, "end": 10},
            {"start": 15, "end": 20},
        ])
        assert len(result) == 2
        assert result[0]["end"] == 10

    def test_merge_overlapping_adjacent(self):
        result = _merge_overlapping([
            {"start": 0, "end": 5},
            {"start": 5.03, "end": 10},
        ])
        assert len(result) == 1


class TestTranscriptCache:
    def test_cache_hit(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, mode="highlight_extract")
        ctx.source_url = "http://example.com/video.mp4"
        tm = _make_mock_tm()
        tm.get_transcript_by_hash = AsyncMock(return_value={
            "id": "cached_tr",
            "status": "succeeded",
            "sentences": [{"start": 0, "end": 5, "text": "cached"}],
            "full_text": "cached",
        })
        ffmpeg = _make_mock_ffmpeg()
        asr = _make_mock_asr()
        emit = MagicMock()

        run(run_pipeline(ctx, tm, asr, ffmpeg, emit))

        asr.transcribe.assert_not_called()
        assert ctx.transcript_text == "cached"
