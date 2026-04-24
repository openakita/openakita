"""Unit tests for footage_gate_pipeline — 8-step orchestrator + 4 modes + error mapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import footage_gate_pipeline as pipeline
import pytest
from footage_gate_ffmpeg import FFmpegError
from footage_gate_pipeline import (
    PipelineContext,
    _classify_error,
    handle_exception,
    run_pipeline,
)

# ── Helpers ───────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, mode: str, **kw: Any) -> PipelineContext:
    inp = tmp_path / "input.mp4"
    inp.write_bytes(b"x")
    return PipelineContext(
        task_id="t1",
        mode=mode,
        input_path=inp,
        work_dir=tmp_path / "work",
        params=kw.pop("params", {}),
        **kw,
    )


# ── Step-level smoke ──────────────────────────────────────────────────────


class TestSetupAndValidate:
    def test_setup_creates_work_dir(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, "source_review")
        pipeline.setup_environment(ctx)
        assert ctx.work_dir.is_dir()

    def test_validate_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, "source_review")
        ctx.input_path = tmp_path / "ghost.mp4"
        with pytest.raises(FileNotFoundError):
            pipeline.validate_input(ctx)

    def test_validate_unknown_extension_raises_value_error(self, tmp_path: Path) -> None:
        f = tmp_path / "a.bin"
        f.write_bytes(b"x")
        ctx = _make_ctx(tmp_path, "source_review")
        ctx.input_path = f
        with pytest.raises(ValueError, match="unsupported"):
            pipeline.validate_input(ctx)


# ── Error classification — 9 kinds ────────────────────────────────────────


class TestClassifyError:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (FileNotFoundError("nope"), "not_found"),
            (TimeoutError("slow"), "timeout"),
            (RuntimeError("rate limit hit"), "rate_limit"),
            (RuntimeError("auth 401"), "auth"),
            (RuntimeError("moderation flagged"), "moderation"),
            (RuntimeError("quota exhausted"), "quota"),
            (RuntimeError("network down"), "network"),
            (RuntimeError("totally unrelated"), "unknown"),
        ],
    )
    def test_classifies(self, exc: BaseException, expected: str) -> None:
        assert _classify_error(exc) == expected

    def test_ffmpeg_error_classified_as_dependency(self) -> None:
        exc = FFmpegError(("ffmpeg",), 1, "no such file")
        assert _classify_error(exc) == "not_found"

    def test_ffmpeg_timeout_string_classified_as_timeout(self) -> None:
        exc = FFmpegError(("ffmpeg",), -1, "timeout after 10s")
        assert _classify_error(exc) == "timeout"


class TestHandleException:
    def test_populates_error_fields_with_zh_hints(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, "source_review")
        handle_exception(ctx, FileNotFoundError("ghost"))
        assert ctx.error_kind == "not_found"
        assert ctx.error_hints
        assert ctx.error_message and "FileNotFoundError" in ctx.error_message
        assert ctx.completed_at is not None


# ── source_review pipeline ────────────────────────────────────────────────


class TestSourceReviewPipeline:
    def test_writes_review_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _make_ctx(tmp_path, "source_review")
        ctx.work_dir.mkdir(parents=True, exist_ok=True)

        def fake_review(files, **_kw):
            return {
                "version": "1.0",
                "files": [{"path": str(files[0]), "media_type": "video", "quality_risks": []}],
                "summary": "ok",
                "planning_implications": [],
            }

        monkeypatch.setattr(pipeline, "review_source_media", fake_review)
        pipeline.run_source_review_pipeline(ctx)
        assert ctx.report_path is not None
        assert ctx.report_path.is_file()
        assert "review.json" in ctx.report_path.name

    def test_hdr_source_appends_risk(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _make_ctx(tmp_path, "source_review")
        ctx.is_hdr_source = True
        ctx.work_dir.mkdir(parents=True, exist_ok=True)

        def fake_review(files, **_kw):
            return {
                "version": "1.0",
                "files": [{"path": str(files[0]), "media_type": "video", "quality_risks": []}],
                "summary": "ok",
                "planning_implications": [],
            }

        monkeypatch.setattr(pipeline, "review_source_media", fake_review)
        pipeline.run_source_review_pipeline(ctx)
        import json

        report = json.loads(ctx.report_path.read_text(encoding="utf-8"))
        assert any("hdr" in r.lower() for r in report["files"][0]["quality_risks"])


# ── silence_cut pipeline ──────────────────────────────────────────────────


class TestSilenceCutPipeline:
    def test_no_audio_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _make_ctx(tmp_path, "silence_cut")
        monkeypatch.setattr(pipeline, "has_audio_track", lambda *_a, **_kw: False)
        with pytest.raises(ValueError, match="no audio"):
            pipeline.run_silence_cut_pipeline(ctx)

    def test_happy_path_writes_outputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _make_ctx(tmp_path, "silence_cut")
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(pipeline, "has_audio_track", lambda *_a, **_kw: True)
        monkeypatch.setattr(
            pipeline,
            "compute_non_silent_intervals",
            lambda *_a, **_kw: [(0.0, 1.0), (2.0, 3.0)],
        )

        def fake_apply(_inp, output, intervals, **_kw):
            output.write_bytes(b"x")
            return {
                "kept_seconds": 2.0,
                "removed_seconds": 5.0,
                "segments": len(intervals),
            }

        monkeypatch.setattr(pipeline, "apply_silence_cut", fake_apply)

        pipeline.run_silence_cut_pipeline(ctx)
        assert ctx.output_path and ctx.output_path.is_file()
        assert ctx.report_path and ctx.report_path.is_file()
        assert ctx.removed_seconds == 5.0


# ── auto_color pipeline ───────────────────────────────────────────────────


class TestAutoColorPipeline:
    def test_renders_when_filter_nonempty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _make_ctx(tmp_path, "auto_color")
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            pipeline,
            "auto_grade_for_clip",
            lambda *_a, **_kw: ("eq=contrast=1.05", {"y_mean": 0.5}),
        )

        called: list[Any] = []

        def fake_apply(input_path, output_path, fstr, **_kw):
            called.append((input_path, output_path, fstr))
            output_path.write_bytes(b"x")

        monkeypatch.setattr(pipeline, "apply_grade", fake_apply)
        pipeline.run_auto_color_pipeline(ctx)
        assert called
        assert ctx.output_path and ctx.output_path.is_file()

    def test_hdr_source_records_tonemap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _make_ctx(tmp_path, "auto_color")
        ctx.is_hdr_source = True
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            pipeline,
            "auto_grade_for_clip",
            lambda *_a, **_kw: ("eq=contrast=1.05", {"y_mean": 0.5}),
        )
        monkeypatch.setattr(
            pipeline,
            "apply_grade",
            lambda *_a, **kw: kw,  # noqa: ARG005
        )
        # apply_grade returning None is fine for the test; ensure file write fallback
        pipeline.apply_grade = lambda i, o, f, **kw: o.write_bytes(b"x")  # type: ignore[assignment]
        pipeline.run_auto_color_pipeline(ctx)
        import json

        grade = json.loads(ctx.report_path.read_text(encoding="utf-8"))
        assert grade["hdr_source"] is True
        assert grade["tonemap_chain"]


# ── cut_qc pipeline ───────────────────────────────────────────────────────


class TestCutQcPipeline:
    def test_missing_edl_raises(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, "cut_qc")
        with pytest.raises(ValueError, match="EDL"):
            pipeline.run_cut_qc_pipeline(ctx)

    def test_writes_qc_report(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _make_ctx(
            tmp_path,
            "cut_qc",
            params={
                "edl": {
                    "cuts": [{"in_seconds": 0, "out_seconds": 1, "source": {"path": "x.mp4"}}],
                    "total_duration_s": 1.0,
                },
                "auto_remux": False,
            },
        )
        ctx.work_dir.mkdir(parents=True, exist_ok=True)

        from footage_gate_qc import NormalizedEdl, QcResult

        def fake_run(video, payload, *, work_dir, **_kw):
            return QcResult(
                issues=[],
                attempts=0,
                final_video=video,
                grid_path=None,
                edl_used=NormalizedEdl(
                    cuts=[],
                    subtitles=[],
                    overlays=[],
                    output_resolution=(1920, 1080),
                    total_duration_s=1.0,
                    field_naming="standard",
                    raw={},
                ),
                naming_normalized=False,
            )

        monkeypatch.setattr(pipeline, "run_qc_with_remux", fake_run)
        pipeline.run_cut_qc_pipeline(ctx)
        assert ctx.report_path is not None
        assert ctx.report_path.is_file()


# ── End-to-end run_pipeline error path ────────────────────────────────────


class TestRunPipelineErrorPath:
    def test_missing_input_caught_and_classified(self, tmp_path: Path) -> None:
        ctx = PipelineContext(
            task_id="t-bad",
            mode="source_review",
            input_path=tmp_path / "nope.mp4",
            work_dir=tmp_path / "work",
        )
        out = run_pipeline(ctx)
        assert out.error_kind == "not_found"
        assert out.error_hints
        assert out.completed_at is not None

    def test_unknown_mode_caught(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Bypass ffprobe so the unknown-mode dispatch is what trips.
        monkeypatch.setattr(
            pipeline,
            "ffprobe_json",
            lambda *_a, **_kw: {"format": {"duration": 10.0}},
        )
        monkeypatch.setattr(pipeline, "is_hdr_source", lambda *_a, **_kw: False)
        ctx = _make_ctx(tmp_path, "not-a-mode")
        out = run_pipeline(ctx)
        assert out.error_kind == "unknown"
