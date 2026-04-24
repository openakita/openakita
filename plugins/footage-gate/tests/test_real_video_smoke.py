"""End-to-end real-FFmpeg smoke tests for footage-gate.

These tests skip cleanly when FFmpeg / FFprobe is not on PATH so the unit
suite still runs in environments without binaries (e.g. CI without
system FFmpeg). When FFmpeg *is* available, the tests:

1. Synthesise a tiny SDR mp4 (5 s, 320x240, color bars + 1 kHz tone) via
   FFmpeg's ``lavfi`` source. No external assets required.
2. Run the full :func:`run_pipeline` for ``source_review`` /
   ``silence_cut`` / ``auto_color`` end-to-end and assert each writes
   the expected artifacts and clean exit (no ``error_kind``).
3. For ``cut_qc``, run with a tiny EDL and assert the QC pipeline
   completes (issues are acceptable — the smoke test is about
   "the pipeline runs without crashing on a real file").

The goal is regression coverage for "did I break the executor wiring or
the FFmpeg flag generation?" rather than visual quality. For visual /
acceptance verification use [`USER_TEST_CASES.md`](../USER_TEST_CASES.md).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from footage_gate_pipeline import PipelineContext, run_pipeline


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(
    not _has_ffmpeg(),
    reason="real-video smoke needs FFmpeg + FFprobe on PATH",
)


def _make_synth_mp4(dest: Path, duration: float = 5.0) -> Path:
    """Generate a 320x240 / 5 s / 1 kHz tone mp4 using lavfi sources.

    No external assets are required and the output is fully deterministic
    so subsequent runs hash the same. Total file size is ~50 KB.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"smptebars=size=320x240:rate=30:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}:sample_rate=44100",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(dest),
    ]
    subprocess.run(cmd, check=True, timeout=60)
    return dest


@pytest.fixture(scope="module")
def synth_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("synth") / "synth.mp4"
    return _make_synth_mp4(out)


def _ctx(mode: str, input_path: Path, work_dir: Path, params: dict[str, Any] | None = None) -> PipelineContext:
    return PipelineContext(
        task_id=f"smoke_{mode}",
        mode=mode,
        input_path=input_path,
        work_dir=work_dir,
        params=params or {},
        ffmpeg_path=shutil.which("ffmpeg"),
        ffprobe_path=shutil.which("ffprobe"),
    )


def _run(ctx: PipelineContext) -> tuple[PipelineContext, list[tuple[str, dict[str, Any]]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    def emit(evt: str, payload: dict[str, Any]) -> None:
        events.append((evt, payload))

    run_pipeline(ctx, emit=emit)
    return ctx, events


def test_real_source_review(synth_mp4: Path, tmp_path: Path) -> None:
    ctx = _ctx("source_review", synth_mp4, tmp_path / "sr_work")
    ctx, events = _run(ctx)
    assert ctx.error_kind is None, f"unexpected error: {ctx.error_kind} / {ctx.error_message}"
    assert ctx.report_path and ctx.report_path.is_file(), "report.json must exist"
    report = json.loads(ctx.report_path.read_text(encoding="utf-8"))
    # ``usable_for`` lives on the per-file record (review can ingest a batch).
    files = report.get("files") or []
    assert files, "review report must always carry at least one file record"
    assert "usable_for" in files[0], "every file record must include 'usable_for'"
    assert ctx.duration_input_sec > 0
    # We only assert progress events were emitted at all (the exact event
    # names are an internal detail and shift between modes).
    assert events, "pipeline must emit at least one progress event"


def test_real_silence_cut(synth_mp4: Path, tmp_path: Path) -> None:
    ctx = _ctx(
        "silence_cut", synth_mp4, tmp_path / "sc_work",
        params={"threshold_db": -45, "min_silence_len": 0.15, "min_sound_len": 0.05, "pad": 0.05},
    )
    ctx, _events = _run(ctx)
    assert ctx.error_kind is None, f"unexpected error: {ctx.error_kind} / {ctx.error_message}"
    # Synth tone is continuous — silence_cut must produce SOME output (not None)
    # even if nothing is removed.
    assert ctx.report_path and ctx.report_path.is_file()
    report = json.loads(ctx.report_path.read_text(encoding="utf-8"))
    # The report nests interval data under either "report" or at the root
    # depending on whether silence was actually removed.
    inner = report.get("report") or report
    assert any(k in inner for k in ("intervals_removed", "removed_seconds", "kept_seconds")), (
        f"silence_cut report missing expected interval keys: {sorted(report)}"
    )


def test_real_auto_color(synth_mp4: Path, tmp_path: Path) -> None:
    ctx = _ctx(
        "auto_color", synth_mp4, tmp_path / "ac_work",
        params={"preset": "auto", "hdr_tonemap": True},
    )
    ctx, _events = _run(ctx)
    assert ctx.error_kind is None, f"unexpected error: {ctx.error_kind} / {ctx.error_message}"
    assert ctx.output_path and ctx.output_path.is_file(), "auto_color must emit a graded mp4"
    assert ctx.report_path and ctx.report_path.is_file()
    report = json.loads(ctx.report_path.read_text(encoding="utf-8"))
    # Synth source is NOT HDR, so tone-map chain must NOT have been prepended.
    assert report.get("is_hdr_source") in (False, None, 0), (
        "synth bars must not be flagged as HDR"
    )


def test_real_cut_qc_no_remux(synth_mp4: Path, tmp_path: Path) -> None:
    # ``source`` must be a dict ({path, media_type}) per the QC EDL contract;
    # it sidesteps OpenMontage Issue #42 (mixed video+image cuts crash).
    edl = {
        "cuts": [
            {
                "in_seconds": 0.0,
                "out_seconds": 4.5,
                "source": {"path": str(synth_mp4), "media_type": "video"},
            }
        ],
        "output_resolution": [320, 240],
    }
    ctx = _ctx(
        "cut_qc", synth_mp4, tmp_path / "qc_work",
        params={"edl": edl, "auto_remux": False, "max_attempts": 1},
    )
    ctx, _events = _run(ctx)
    # The QC pipeline may legitimately surface issues (e.g. boundary frame
    # check on a bars source can be noisy); the smoke test only asserts
    # the pipeline didn't crash.
    assert ctx.error_kind is None, f"unexpected error: {ctx.error_kind} / {ctx.error_message}"
    assert ctx.report_path and ctx.report_path.is_file()
    report = json.loads(ctx.report_path.read_text(encoding="utf-8"))
    assert "issues" in report or "qc_issues" in report
    # auto_remux OFF → qc_attempts must be 0 (we never re-rendered).
    assert (ctx.qc_attempts or 0) == 0
