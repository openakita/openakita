"""Unit tests for the video-color-grade pure-logic engine.

We never invoke real ffmpeg here: every sub-process call is monkey-patched
so the tests are deterministic and run in <1 second.  The actual grading
math (signalstats parsing + \u00b18% clamp) is exercised in the SDK tests
(``openakita-plugin-sdk/tests/contrib/test_ffmpeg_grade.py``); these
tests cover the plugin-level orchestration: planning, command building,
verification envelope, error fallbacks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita_plugin_sdk.contrib import (
    AUTO_GRADE_PRESETS,
    DEFAULT_GRADE_CLAMP_PCT,
    FFmpegResult,
    GradeStats,
)

from grade_engine import (
    DEFAULT_PROBE_DURATION_SEC,
    DEFAULT_RENDER_TIMEOUT_SEC,
    DEFAULT_SAMPLE_FRAMES,
    MODE_AUTO,
    MODE_PRESET,
    GradeJobResult,
    GradePlan,
    analyze_clip,
    apply_grade,
    build_grade_command,
    ffmpeg_available,
    list_modes,
    plan_grade,
    probe_video_duration_sec,
    to_verification,
)
import grade_engine


# ── module surface ─────────────────────────────────────────────────────────


def test_default_clamp_re_exports_sdk_constant() -> None:
    """The plugin re-exports the SDK's clamp default — single source of truth."""
    assert DEFAULT_GRADE_CLAMP_PCT == 0.08
    assert grade_engine.DEFAULT_GRADE_CLAMP_PCT == DEFAULT_GRADE_CLAMP_PCT


def test_list_modes_includes_auto_and_every_preset() -> None:
    modes = list_modes()
    assert MODE_AUTO in modes
    for name in AUTO_GRADE_PRESETS:
        assert f"{MODE_PRESET}:{name}" in modes
    # Total = 1 (auto) + N presets
    assert len(modes) == 1 + len(AUTO_GRADE_PRESETS)


def test_default_constants_are_sane() -> None:
    assert DEFAULT_PROBE_DURATION_SEC > 0
    assert DEFAULT_SAMPLE_FRAMES >= 5
    assert DEFAULT_RENDER_TIMEOUT_SEC >= 60


def test_ffmpeg_available_returns_bool() -> None:
    """Just exercise the probe — we do not assert true/false because CI
    machines vary.  The contract is "no exception, returns bool"."""
    assert isinstance(ffmpeg_available(), bool)


# ── probe_video_duration_sec ───────────────────────────────────────────────


def test_probe_returns_zero_when_ffprobe_throws(monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("ffprobe missing")

    monkeypatch.setattr(grade_engine, "ffprobe_json_sync", boom)
    assert probe_video_duration_sec("anywhere.mp4") == 0.0


def test_probe_returns_rounded_duration(monkeypatch) -> None:
    monkeypatch.setattr(
        grade_engine, "ffprobe_json_sync",
        lambda *a, **k: {"format": {"duration": "12.34567"}},
    )
    assert probe_video_duration_sec("any.mp4") == 12.346


def test_probe_returns_zero_for_unparseable_duration(monkeypatch) -> None:
    monkeypatch.setattr(
        grade_engine, "ffprobe_json_sync",
        lambda *a, **k: {"format": {"duration": "not-a-number"}},
    )
    assert probe_video_duration_sec("any.mp4") == 0.0


def test_probe_handles_missing_format_field(monkeypatch) -> None:
    monkeypatch.setattr(
        grade_engine, "ffprobe_json_sync",
        lambda *a, **k: {"streams": []},
    )
    assert probe_video_duration_sec("any.mp4") == 0.0


# ── analyze_clip ───────────────────────────────────────────────────────────


def _stats(y_mean: float, y_range: float, sat_mean: float, samples: int = 10):
    return GradeStats(
        y_mean=y_mean, y_range=y_range, sat_mean=sat_mean,
        bit_depth=8, samples=samples,
    )


def test_analyze_clip_uses_probed_duration_when_none_passed(
    tmp_path: Path, monkeypatch,
) -> None:
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    monkeypatch.setattr(grade_engine, "probe_video_duration_sec", lambda *a, **k: 4.0)

    captured = {}
    def fake_sample(_v, *, start, duration, n_samples, timeout_sec, ffmpeg):
        captured["duration"] = duration
        return _stats(0.5, 0.72, 0.25)

    monkeypatch.setattr(grade_engine, "sample_signalstats_sync", fake_sample)
    analyze_clip(src)
    # min(probe_duration=4, DEFAULT=10) → 4
    assert captured["duration"] == 4.0


def test_analyze_clip_uses_default_when_probe_returns_zero(
    tmp_path: Path, monkeypatch,
) -> None:
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    monkeypatch.setattr(grade_engine, "probe_video_duration_sec", lambda *a, **k: 0.0)
    captured = {}
    def fake_sample(_v, *, start, duration, **kw):
        captured["duration"] = duration
        return _stats(0.5, 0.72, 0.25)

    monkeypatch.setattr(grade_engine, "sample_signalstats_sync", fake_sample)
    analyze_clip(src)
    assert captured["duration"] == DEFAULT_PROBE_DURATION_SEC


def test_analyze_clip_returns_filter_string(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    monkeypatch.setattr(
        grade_engine, "sample_signalstats_sync",
        lambda *a, **k: _stats(0.32, 0.72, 0.25),  # underexposed → gamma lift
    )
    stats, filter_str = analyze_clip(src, duration=2.0)
    assert stats.y_mean < 0.42
    assert "gamma=" in filter_str


# ── plan_grade ─────────────────────────────────────────────────────────────


def test_plan_grade_auto_mode(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    monkeypatch.setattr(
        grade_engine, "sample_signalstats_sync",
        lambda *a, **k: _stats(0.5, 0.72, 0.25),
    )
    plan = plan_grade(input_path=src, output_path=tmp_path / "out.mp4")
    assert plan.mode == MODE_AUTO
    assert plan.input_path == str(src)
    assert plan.output_path == str(tmp_path / "out.mp4")
    assert plan.clamp_pct == DEFAULT_GRADE_CLAMP_PCT
    assert plan.stats.samples > 0


def test_plan_grade_preset_mode(tmp_path: Path, monkeypatch) -> None:
    """Preset mode does NOT sample — verify by ensuring sample_signalstats
    is never called."""
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")

    def must_not_call(*a, **k):
        raise AssertionError("preset mode must not sample")

    monkeypatch.setattr(grade_engine, "sample_signalstats_sync", must_not_call)
    plan = plan_grade(
        input_path=src, output_path=tmp_path / "out.mp4",
        mode="preset:warm_cinematic",
    )
    assert plan.mode == "preset:warm_cinematic"
    assert "colorbalance" in plan.filter_string  # warm_cinematic signature


def test_plan_grade_preset_none_yields_empty_filter(tmp_path: Path) -> None:
    plan = plan_grade(
        input_path=tmp_path / "v.mp4",
        output_path=tmp_path / "o.mp4",
        mode="preset:none",
    )
    assert plan.filter_string == ""


def test_plan_grade_unknown_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported mode"):
        plan_grade(
            input_path=tmp_path / "v.mp4",
            output_path=tmp_path / "o.mp4",
            mode="bananas",
        )


def test_plan_grade_unknown_preset_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown grade preset"):
        plan_grade(
            input_path=tmp_path / "v.mp4",
            output_path=tmp_path / "o.mp4",
            mode="preset:nonsense",
        )


def test_plan_to_dict_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        grade_engine, "sample_signalstats_sync",
        lambda *a, **k: _stats(0.5, 0.72, 0.25),
    )
    plan = plan_grade(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
    )
    d = plan.to_dict()
    assert set(d.keys()) >= {
        "input_path", "output_path", "mode", "filter_string",
        "clamp_pct", "stats",
    }
    assert d["stats"]["samples"] == plan.stats.samples


# ── build_grade_command ────────────────────────────────────────────────────


def _fake_resolve(name: str) -> str:
    return f"/usr/bin/{name}"


def test_build_grade_command_includes_eq_when_filter_present(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(grade_engine, "resolve_binary", _fake_resolve)
    plan = GradePlan(
        input_path=str(tmp_path / "v.mp4"),
        output_path=str(tmp_path / "out.mp4"),
        mode=MODE_AUTO,
        filter_string="eq=contrast=1.05:gamma=1.04",
        stats=_stats(0.4, 0.6, 0.2),
        clamp_pct=0.08,
        sample_window_sec=10,
        sample_frames=10,
    )
    cmd = build_grade_command(plan)
    assert cmd[0].endswith("ffmpeg")
    assert "-i" in cmd
    assert "-vf" in cmd
    assert "eq=contrast=1.05:gamma=1.04" in cmd
    assert "libx264" in cmd
    assert "+faststart" in cmd
    assert cmd[-1] == str(tmp_path / "out.mp4")


def test_build_grade_command_omits_vf_when_filter_empty(
    tmp_path: Path, monkeypatch,
) -> None:
    """No filter → no ``-vf`` flag, but we still re-encode (predictable mp4)."""
    monkeypatch.setattr(grade_engine, "resolve_binary", _fake_resolve)
    plan = GradePlan(
        input_path=str(tmp_path / "v.mp4"),
        output_path=str(tmp_path / "out.mp4"),
        mode="preset:none",
        filter_string="",
        stats=_stats(0.5, 0.72, 0.25, samples=0),
        clamp_pct=0.08,
        sample_window_sec=0,
        sample_frames=0,
    )
    cmd = build_grade_command(plan)
    assert "-vf" not in cmd
    assert "libx264" in cmd  # still re-encoding for consistent container


def test_build_grade_command_respects_crf_and_preset(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(grade_engine, "resolve_binary", _fake_resolve)
    plan = GradePlan(
        input_path=str(tmp_path / "v.mp4"),
        output_path=str(tmp_path / "out.mp4"),
        mode=MODE_AUTO, filter_string="eq=contrast=1.04",
        stats=_stats(0.5, 0.72, 0.25), clamp_pct=0.08,
        sample_window_sec=10, sample_frames=10,
    )
    cmd = build_grade_command(plan, crf=23, preset="medium")
    assert "23" in cmd
    assert "medium" in cmd


# ── apply_grade ────────────────────────────────────────────────────────────


def test_apply_grade_invokes_ffmpeg_and_records_size(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(grade_engine, "resolve_binary", _fake_resolve)
    monkeypatch.setattr(grade_engine, "probe_video_duration_sec", lambda *a, **k: 30.0)

    out_path = tmp_path / "out.mp4"

    def fake_run(cmd, *, timeout_sec, check=True, capture=True, input_bytes=None):
        # simulate ffmpeg writing the file
        out_path.write_bytes(b"fake encoded output")
        return FFmpegResult(cmd=list(cmd), returncode=0, stdout="", stderr="", duration_sec=2.5)

    monkeypatch.setattr(grade_engine, "run_ffmpeg_sync", fake_run)

    plan = GradePlan(
        input_path=str(tmp_path / "v.mp4"),
        output_path=str(out_path),
        mode=MODE_AUTO, filter_string="eq=contrast=1.05",
        stats=_stats(0.5, 0.72, 0.25), clamp_pct=0.08,
        sample_window_sec=10, sample_frames=10,
    )
    result = apply_grade(plan)
    assert isinstance(result, GradeJobResult)
    assert result.duration_sec == 30.0
    assert result.elapsed_sec == 2.5
    assert result.output_size_bytes == len(b"fake encoded output")
    assert result.ffmpeg_cmd[0].endswith("ffmpeg")


def test_apply_grade_creates_parent_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(grade_engine, "resolve_binary", _fake_resolve)
    monkeypatch.setattr(grade_engine, "probe_video_duration_sec", lambda *a, **k: 5.0)
    out_path = tmp_path / "nested" / "deep" / "graded.mp4"

    def fake_run(cmd, **kw):
        out_path.write_bytes(b"x")
        return FFmpegResult(cmd=list(cmd), returncode=0, stdout="", stderr="", duration_sec=0.1)

    monkeypatch.setattr(grade_engine, "run_ffmpeg_sync", fake_run)
    plan = GradePlan(
        input_path=str(tmp_path / "v.mp4"),
        output_path=str(out_path),
        mode=MODE_AUTO, filter_string="",
        stats=_stats(0.5, 0.72, 0.25, samples=0), clamp_pct=0.08,
        sample_window_sec=0, sample_frames=0,
    )
    apply_grade(plan)
    assert out_path.exists()


# ── to_verification ────────────────────────────────────────────────────────


def _make_result(
    *, mode: str = MODE_AUTO, filter_string: str = "eq=contrast=1.05",
    samples: int = 10, duration_sec: float = 10.0,
) -> GradeJobResult:
    plan = GradePlan(
        input_path="/in.mp4", output_path="/out.mp4",
        mode=mode, filter_string=filter_string,
        stats=_stats(0.5, 0.72, 0.25, samples=samples),
        clamp_pct=0.08, sample_window_sec=10, sample_frames=10,
    )
    return GradeJobResult(
        plan=plan, duration_sec=duration_sec, elapsed_sec=1.0,
        output_size_bytes=1024, ffmpeg_cmd=["ffmpeg"],
    )


def test_verification_clean_run_is_ok() -> None:
    v = to_verification(_make_result())
    assert v.verified is True
    assert v.low_confidence_fields == []


def test_verification_flags_empty_signalstats_in_auto_mode() -> None:
    v = to_verification(_make_result(samples=0))
    assert v.verified is False
    paths = {f.path for f in v.low_confidence_fields}
    assert "$.plan.stats.samples" in paths


def test_verification_skips_empty_signalstats_for_preset_mode() -> None:
    """Preset mode never samples — empty stats is expected, not a flag."""
    v = to_verification(_make_result(
        mode="preset:warm_cinematic", samples=0, filter_string="eq=...",
    ))
    paths = {f.path for f in v.low_confidence_fields}
    assert "$.plan.stats.samples" not in paths


def test_verification_flags_no_op_filter_in_auto_mode() -> None:
    v = to_verification(_make_result(filter_string=""))
    paths = {f.path for f in v.low_confidence_fields}
    assert "$.plan.filter_string" in paths


def test_verification_flags_unknown_duration() -> None:
    v = to_verification(_make_result(duration_sec=0.0))
    paths = {f.path for f in v.low_confidence_fields}
    assert "$.duration_sec" in paths
