"""Phase 3 unit tests — subtitle_pipeline.

Coverage map (against §3.4 + §九):

- ``test_no_handoff_in_pipeline_source``        → red-line #20 + Gate-3
- ``test_step45_trigger_*``                     → step 4.5 3-condition AND
- ``test_step45_failure_is_non_fatal``          → P1-12 fallback contract
- ``test_cache_hit_skips_paraformer_call``      → cache invariant
- ``test_translate_mode_skip_steps``            → mode skip_steps wiring
- ``test_repair_mode_runs_repair_step``         → mode dispatch
- ``test_cancel_between_steps_aborts``          → cooperative cancel
- ``test_error_kind_always_canonical_9``        → 9-key red-line at write site
- ``test_emit_terminal_payload_shape``          → SSE contract (§8.4)
- ``test_apply_speaker_map_prepends_label``     → speaker_map render path
- ``test_metadata_json_shape``                  → §8.4 metadata.json contract
- ``test_unknown_mode_emits_format_error``      → defensive top-of-pipeline

The harness uses fully in-memory fakes (no aiosqlite, no httpx, no ffmpeg).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from subtitle_asr_client import AsrError, AsrResult, AsrSentence, AsrWord
from subtitle_models import ALLOWED_ERROR_KINDS, ERROR_HINTS
from subtitle_pipeline import (
    PipelineError,
    SubtitlePipelineContext,
    _apply_speaker_map,
    _classify_error,
    _should_identify_characters,
    run_pipeline,
)
from subtitle_renderer import SRTCue

# ---------------------------------------------------------------------------
# Tiny in-memory fakes
# ---------------------------------------------------------------------------


@dataclass
class _CapturedEvent:
    name: str
    payload: dict[str, Any]


class _FakeTaskManager:
    """Tracks update_task / update_transcript calls in-memory."""

    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.transcripts: dict[str, dict[str, Any]] = {}
        self.cached_transcript: dict[str, Any] | None = None
        self._canceled: set[str] = set()
        self.update_task_calls: list[dict[str, Any]] = []
        self.update_transcript_calls: list[dict[str, Any]] = []

    # Task ops ----------------------------------------------------------
    def is_canceled(self, task_id: str) -> bool:
        return task_id in self._canceled

    def request_cancel(self, task_id: str) -> None:
        self._canceled.add(task_id)

    async def update_task(self, task_id: str, **updates: Any) -> None:
        self.tasks.setdefault(task_id, {}).update(updates)
        self.update_task_calls.append({"task_id": task_id, **updates})

    async def update_task_safe(self, task_id: str, **updates: Any) -> None:
        await self.update_task(task_id, **updates)

    # Transcript ops ----------------------------------------------------
    async def get_transcript_by_hash(self, source_hash: str) -> dict[str, Any] | None:
        return self.cached_transcript

    async def create_transcript(self, **kwargs: Any) -> dict[str, Any]:
        tid = f"tr-{len(self.transcripts) + 1}"
        rec = {"id": tid, "status": "pending", **kwargs}
        self.transcripts[tid] = rec
        return rec

    async def update_transcript(self, tid: str, **updates: Any) -> None:
        self.transcripts.setdefault(tid, {}).update(updates)
        self.update_transcript_calls.append({"tid": tid, **updates})


class _FakeAsr:
    """Minimal AsrClient stub; tests override individual coroutines as needed."""

    def __init__(self) -> None:
        self.transcribe = AsyncMock()
        self.identify_characters = AsyncMock(return_value={})
        self.translate_batch = AsyncMock(return_value=[])


def _events_collector() -> tuple[list[_CapturedEvent], Any]:
    events: list[_CapturedEvent] = []

    def emit(name: str, payload: dict[str, Any]) -> None:
        events.append(_CapturedEvent(name=name, payload=dict(payload)))

    return events, emit


def _new_ctx(
    *,
    task_id: str = "t-001",
    mode: str = "auto_subtitle",
    params: dict[str, Any] | None = None,
    task_dir: Path,
    source_path: Path | None = None,
    source_url: str = "",
    source_kind: str = "",
    source_duration_sec: float | None = None,
    speaker_ids: set[str] | None = None,
    transcript_words: list[dict[str, Any]] | None = None,
) -> SubtitlePipelineContext:
    return SubtitlePipelineContext(
        task_id=task_id,
        mode=mode,
        params=params or {},
        task_dir=task_dir,
        source_path=source_path,
        source_url=source_url,
        source_kind=source_kind,
        source_duration_sec=source_duration_sec,
        speaker_ids=speaker_ids or set(),
        transcript_words=transcript_words,
    )


# ---------------------------------------------------------------------------
# Red-line guards
# ---------------------------------------------------------------------------


class TestRedlineGuards:
    def test_no_handoff_in_pipeline_source(self) -> None:
        """Gate-3: rg "handoff" subtitle_pipeline.py must be 0 hits."""
        path = Path(__file__).resolve().parent.parent / "subtitle_pipeline.py"
        text = path.read_text(encoding="utf-8")
        assert "handoff" not in text.lower(), (
            "subtitle_pipeline.py must not reference Handoff (v1.0 → v2.0 deferred)"
        )

    def test_pipeline_uses_only_canonical_9_keys(self) -> None:
        """Every error_kind raised inside the module must be in ALLOWED_ERROR_KINDS."""
        path = Path(__file__).resolve().parent.parent / "subtitle_pipeline.py"
        text = path.read_text(encoding="utf-8")
        # Find every kind="..." literal
        import re

        for m in re.finditer(r'kind=["\']([a-z_]+)["\']', text):
            kind = m.group(1)
            assert kind in ALLOWED_ERROR_KINDS, (
                f"non-canonical error_kind in pipeline source: {kind!r}"
            )

    def test_classify_error_only_returns_canonical(self) -> None:
        for exc in [
            FileNotFoundError("x"),
            PermissionError("x"),
            TimeoutError("x"),
            RuntimeError("connection reset"),
            RuntimeError("401 unauthorized"),
            RuntimeError("ffmpeg missing"),
            RuntimeError("moderation flagged"),
            RuntimeError("totally surprising"),
        ]:
            assert _classify_error(exc) in ALLOWED_ERROR_KINDS


# ---------------------------------------------------------------------------
# Step 4.5 trigger logic
# ---------------------------------------------------------------------------


class TestStep45Trigger:
    def _ctx(self, **overrides: Any) -> SubtitlePipelineContext:
        return _new_ctx(task_dir=Path("."), **overrides)

    def test_all_three_true_with_speakers(self) -> None:
        ctx = self._ctx(
            mode="auto_subtitle",
            params={
                "diarization_enabled": True,
                "character_identify_enabled": True,
            },
            speaker_ids={"SPEAKER_00"},
        )
        assert _should_identify_characters(ctx) is True

    def test_no_speakers_returns_false(self) -> None:
        ctx = self._ctx(
            mode="auto_subtitle",
            params={
                "diarization_enabled": True,
                "character_identify_enabled": True,
            },
            speaker_ids=set(),
        )
        assert _should_identify_characters(ctx) is False

    def test_diarization_off_returns_false(self) -> None:
        ctx = self._ctx(
            mode="auto_subtitle",
            params={
                "diarization_enabled": False,
                "character_identify_enabled": True,
            },
            speaker_ids={"SPEAKER_00"},
        )
        assert _should_identify_characters(ctx) is False

    def test_character_identify_off_returns_false(self) -> None:
        ctx = self._ctx(
            mode="auto_subtitle",
            params={
                "diarization_enabled": True,
                "character_identify_enabled": False,
            },
            speaker_ids={"SPEAKER_00"},
        )
        assert _should_identify_characters(ctx) is False

    def test_wrong_mode_returns_false(self) -> None:
        ctx = self._ctx(
            mode="translate",
            params={
                "diarization_enabled": True,
                "character_identify_enabled": True,
            },
            speaker_ids={"SPEAKER_00"},
        )
        assert _should_identify_characters(ctx) is False


# ---------------------------------------------------------------------------
# Cache-hit invariant
# ---------------------------------------------------------------------------


class TestCacheHit:
    def test_cache_hit_skips_paraformer_call(self, tmp_path: Path) -> None:
        """If transcript cache hits, asr.transcribe is NEVER called."""
        events, emit = _events_collector()
        tm = _FakeTaskManager()
        # Pre-populate the cache with a SUCCEEDED transcript
        cached_words = [
            {
                "text": "缓存命中",
                "start_ms": 0,
                "end_ms": 500,
                "punctuation": "。",
                "speaker_id": None,
            }
        ]
        tm.cached_transcript = {
            "id": "tr-cached",
            "status": "succeeded",
            "words": cached_words,
            "full_text": "缓存命中",
            "language": "zh",
        }
        asr = _FakeAsr()  # transcribe returns AsyncMock; if called, that's a bug

        src_video = tmp_path / "in.mp4"
        src_video.write_bytes(b"fake video bytes" * 100)

        ctx = _new_ctx(
            task_dir=tmp_path / "task",
            source_path=src_video,
            source_url="http://host/in.mp4",
            source_kind="audio",  # skip ffmpeg WAV extract in step 3
            source_duration_sec=10.0,
        )
        asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

        asr.transcribe.assert_not_called()
        assert ctx.transcript_id == "tr-cached"
        assert ctx.transcript_full_text == "缓存命中"
        # Pipeline finished successfully (succeeded event was emitted).
        statuses = [e.payload.get("status") for e in events]
        assert "succeeded" in statuses


# ---------------------------------------------------------------------------
# Step 4.5 non-fatal failure
# ---------------------------------------------------------------------------


class TestStep45NonFatal:
    def test_step45_failure_does_not_abort_pipeline(self, tmp_path: Path) -> None:
        events, emit = _events_collector()
        tm = _FakeTaskManager()

        # ASR returns a transcript with one speaker so step 4.5 will trigger.
        words = [
            AsrWord(text="hi", start_ms=0, end_ms=200, speaker_id="SPEAKER_00"),
            AsrWord(text="!", start_ms=200, end_ms=210, speaker_id="SPEAKER_00", punctuation="."),
        ]
        sent = AsrSentence(
            start_ms=0,
            end_ms=210,
            text="hi.",
            words=tuple(words),
            speaker_id="SPEAKER_00",
        )
        result = AsrResult(
            sentences=[sent],
            full_text="hi.",
            language="en",
            duration_sec=0.21,
            speaker_count=1,
        )

        asr = _FakeAsr()
        asr.transcribe = AsyncMock(return_value=result)
        # Step 4.5 always fails:
        asr.identify_characters = AsyncMock(side_effect=AsrError("vendor down", kind="network"))

        src = tmp_path / "in.wav"
        src.write_bytes(b"X" * 1000)
        ctx = _new_ctx(
            task_dir=tmp_path / "task",
            source_path=src,
            source_url="http://host/in.wav",
            source_kind="audio",
            source_duration_sec=10.0,
            params={
                "diarization_enabled": True,
                "character_identify_enabled": True,
            },
        )
        asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

        statuses = [e.payload.get("status") for e in events]
        assert "succeeded" in statuses, (
            f"Step 4.5 failure must not abort the pipeline; events: {[e.payload for e in events]}"
        )
        assert ctx.speaker_map_failed is True
        assert ctx.speaker_map == {}
        assert tm.tasks[ctx.task_id]["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Mode dispatch (translate / repair / burn skip patterns)
# ---------------------------------------------------------------------------


class TestModeDispatch:
    def test_translate_mode_skips_prepare_assets_and_runs_translate(self, tmp_path: Path) -> None:
        events, emit = _events_collector()
        tm = _FakeTaskManager()
        asr = _FakeAsr()
        asr.translate_batch = AsyncMock(return_value=["hello", "world"])

        srt_path = tmp_path / "in.srt"
        srt_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n你好\n\n2\n00:00:01,000 --> 00:00:02,000\n世界\n",
            encoding="utf-8",
        )
        ctx = _new_ctx(
            mode="translate",
            task_dir=tmp_path / "task",
            params={
                "srt_path": str(srt_path),
                "target_lang": "en",
                "source_lang": "zh",
            },
        )
        asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

        steps_seen = [
            e.payload.get("pipeline_step") for e in events if e.payload.get("status") == "running"
        ]
        # translate mode skips prepare_assets + asr_or_load? Actually it skips
        # prepare_assets + asr_or_load per MODES_BY_ID.skip_steps.
        assert "prepare_assets" not in steps_seen
        assert "translate_or_repair" in steps_seen
        asr.translate_batch.assert_awaited_once()
        # Final cues should be the translated text.
        assert ctx.cues is not None
        assert any("hello" in c["text"] for c in ctx.cues)

    def test_repair_mode_runs_repair_and_writes_srt(self, tmp_path: Path) -> None:
        events, emit = _events_collector()
        tm = _FakeTaskManager()
        asr = _FakeAsr()
        # SRT with overlap (cue1.end=2.0 > cue2.start=1.5) → should be trimmed.
        srt_path = tmp_path / "broken.srt"
        srt_path.write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nfirst\n\n2\n00:00:01,500 --> 00:00:03,000\nsecond\n",
            encoding="utf-8",
        )
        ctx = _new_ctx(
            mode="repair",
            task_dir=tmp_path / "task",
            params={"srt_path": str(srt_path)},
        )
        asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

        assert ctx.repair_stats is not None
        assert ctx.repair_stats.get("trimmed_overlap", 0) >= 1
        assert ctx.output_srt_path is not None
        assert ctx.output_srt_path.exists()


# ---------------------------------------------------------------------------
# Cooperative cancel
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_before_first_step(self, tmp_path: Path) -> None:
        events, emit = _events_collector()
        tm = _FakeTaskManager()
        tm.request_cancel("t-cancel")
        asr = _FakeAsr()

        ctx = _new_ctx(
            task_id="t-cancel",
            task_dir=tmp_path / "task",
            mode="repair",
            params={"srt_path": str(tmp_path / "x.srt")},  # never reached
        )
        asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

        statuses = [e.payload.get("status") for e in events]
        assert "canceled" in statuses
        # asr.translate_batch never called (we never even reached step 5).
        asr.translate_batch.assert_not_called()


# ---------------------------------------------------------------------------
# SSE event payload shape (§8.4)
# ---------------------------------------------------------------------------


class TestSseEventShape:
    def test_terminal_payload_includes_required_fields(self, tmp_path: Path) -> None:
        events, emit = _events_collector()
        tm = _FakeTaskManager()
        asr = _FakeAsr()

        # Use repair mode for a deterministic happy path.
        srt = tmp_path / "in.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        ctx = _new_ctx(
            mode="repair",
            task_dir=tmp_path / "task",
            params={"srt_path": str(srt)},
        )
        asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

        # Every event must carry task_id + mode + pipeline_step + status.
        for e in events:
            assert e.name == "task_update"
            assert e.payload["task_id"] == ctx.task_id
            assert e.payload["mode"] == ctx.mode
            assert "status" in e.payload
            assert "pipeline_step" in e.payload

    def test_unknown_mode_emits_format_error(self, tmp_path: Path) -> None:
        events, emit = _events_collector()
        tm = _FakeTaskManager()
        asr = _FakeAsr()
        ctx = _new_ctx(mode="not_a_real_mode", task_dir=tmp_path / "task")
        asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

        # Find the failed event.
        failed = [e for e in events if e.payload.get("status") == "failed"]
        assert len(failed) == 1
        assert failed[0].payload["error_kind"] == "format"
        assert failed[0].payload["error_kind"] in ERROR_HINTS


# ---------------------------------------------------------------------------
# Speaker map application (P1-12 success path)
# ---------------------------------------------------------------------------


class TestSpeakerMap:
    def test_apply_speaker_map_prepends_label(self) -> None:
        cues = [
            SRTCue(index=1, start=0.0, end=1.0, text="hello", speaker_id="SPEAKER_00"),
            SRTCue(index=2, start=1.0, end=2.0, text="world", speaker_id="SPEAKER_01"),
            SRTCue(index=3, start=2.0, end=3.0, text="other", speaker_id="SPEAKER_99"),
        ]
        out = _apply_speaker_map(cues, {"SPEAKER_00": "主持人", "SPEAKER_01": "嘉宾"})
        assert out[0].text == "[主持人] hello"
        assert out[1].text == "[嘉宾] world"
        assert out[2].text == "other"  # unmapped speaker untouched


# ---------------------------------------------------------------------------
# metadata.json (§8.4 contract)
# ---------------------------------------------------------------------------


class TestMetadataJson:
    def test_metadata_json_has_required_keys(self, tmp_path: Path) -> None:
        events, emit = _events_collector()
        tm = _FakeTaskManager()
        asr = _FakeAsr()
        srt = tmp_path / "in.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        ctx = _new_ctx(
            mode="repair",
            task_dir=tmp_path / "task",
            params={"srt_path": str(srt), "knob": 42},
        )
        asyncio.run(run_pipeline(ctx, tm, asr, emit=emit))

        meta_path = ctx.task_dir / "metadata.json"
        assert meta_path.exists()
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        for key in ("task_id", "mode", "params", "outputs", "cost", "completed_at"):
            assert key in data, f"metadata.json missing required key: {key}"
        assert data["mode"] == "repair"
        assert data["params"]["knob"] == 42
        assert data["outputs"]["srt"]


# ---------------------------------------------------------------------------
# PipelineError canonical-kind enforcement
# ---------------------------------------------------------------------------


class TestPipelineErrorCanonical:
    def test_non_canonical_kind_is_coerced(self) -> None:
        e = PipelineError("nope", kind="rate_limit")  # not in 9 keys
        assert e.kind == "unknown"

    @pytest.mark.parametrize("kind", sorted(ALLOWED_ERROR_KINDS))
    def test_all_canonical_kinds_accepted(self, kind: str) -> None:
        e = PipelineError("msg", kind=kind)
        assert e.kind == kind
