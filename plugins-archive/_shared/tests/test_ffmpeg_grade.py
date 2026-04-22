"""Tests for the auto color-grade pipeline added to ``contrib.ffmpeg``.

Sprint 13 / B7 backfill — port of ``video-use/helpers/grade.py:78-271``
into the SDK.  We never spawn a real ``ffmpeg`` here: ``signalstats``
output is synthesized as text and parsing / decision logic is exercised
directly.  The only sub-process patched out is :func:`run_ffmpeg_sync`
(via ``monkeypatch``) so :func:`sample_signalstats_sync` becomes a
deterministic file-write test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _shared import (
    AUTO_GRADE_PRESETS,
    DEFAULT_GRADE_CLAMP_PCT,
    FFmpegError,
    FFmpegResult,
    GradeStats,
    auto_color_grade_filter,
    get_grade_preset,
    list_grade_presets,
    sample_signalstats,
    sample_signalstats_sync,
)
from _shared import ffmpeg as ffmpeg_mod
from _shared.ffmpeg import _parse_signalstats_metadata


# ── presets ────────────────────────────────────────────────────────────────


def test_default_clamp_is_eight_percent() -> None:
    """``video-use`` ships ±8% — the SDK constant must match exactly."""
    assert DEFAULT_GRADE_CLAMP_PCT == 0.08


def test_list_grade_presets_sorted() -> None:
    names = list_grade_presets()
    assert names == sorted(names)
    # The four presets we ported must all be present.
    assert set(names) == {"none", "neutral_punch", "subtle", "warm_cinematic"}


def test_get_grade_preset_returns_filter_string() -> None:
    assert get_grade_preset("none") == ""
    assert get_grade_preset("subtle").startswith("eq=")
    assert "colorbalance" in get_grade_preset("warm_cinematic")
    assert AUTO_GRADE_PRESETS["neutral_punch"].startswith("eq=")


def test_get_grade_preset_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown grade preset"):
        get_grade_preset("does-not-exist")


# ── signalstats parsing ────────────────────────────────────────────────────


def _signalstats_text(
    *,
    yavg: list[float],
    ymin: list[float],
    ymax: list[float],
    sat: list[float],
    bit_depth: int = 8,
) -> str:
    """Build a synthetic ``metadata=print`` block."""
    lines = []
    for i, _ in enumerate(yavg):
        lines.append(f"frame:{i}    pts:{i*1000}    pts_time:{i*0.04}")
        lines.append(f"lavfi.signalstats.YBITDEPTH={bit_depth}")
        lines.append(f"lavfi.signalstats.YAVG={yavg[i]}")
        lines.append(f"lavfi.signalstats.YMIN={ymin[i]}")
        lines.append(f"lavfi.signalstats.YMAX={ymax[i]}")
        lines.append(f"lavfi.signalstats.SATAVG={sat[i]}")
    return "\n".join(lines)


def test_parse_signalstats_eight_bit() -> None:
    """A balanced 8-bit clip → all values normalized to 0..1."""
    text = _signalstats_text(
        yavg=[120.0, 130.0, 140.0],
        ymin=[10.0, 12.0, 8.0],
        ymax=[230.0, 240.0, 235.0],
        sat=[40.0, 50.0, 45.0],
        bit_depth=8,
    )
    stats = _parse_signalstats_metadata(text)
    assert stats.bit_depth == 8
    assert stats.samples == 3
    # mean YAVG = 130 → 130/255 ≈ 0.5098
    assert stats.y_mean == pytest.approx(0.5098, abs=1e-3)
    # range = (mean_max - mean_min) / 255
    assert stats.y_range == pytest.approx((235 - 10) / 255, abs=1e-3)
    assert 0.0 < stats.sat_mean < 1.0


def test_parse_signalstats_ten_bit_normalization() -> None:
    """10-bit (max 1023) values must be normalized correctly."""
    text = _signalstats_text(
        yavg=[512.0],
        ymin=[40.0],
        ymax=[960.0],
        sat=[200.0],
        bit_depth=10,
    )
    stats = _parse_signalstats_metadata(text)
    assert stats.bit_depth == 10
    assert stats.y_mean == pytest.approx(512.0 / 1023, abs=1e-3)
    assert stats.y_range == pytest.approx((960.0 - 40.0) / 1023, abs=1e-3)


def test_parse_signalstats_clamped_to_unit_range() -> None:
    """Even malformed (out-of-range) values must clamp to [0, 1]."""
    text = _signalstats_text(
        yavg=[300.0],  # > 255 in 8-bit → would normalize > 1
        ymin=[0.0],
        ymax=[300.0],
        sat=[300.0],
        bit_depth=8,
    )
    stats = _parse_signalstats_metadata(text)
    assert 0.0 <= stats.y_mean <= 1.0
    assert 0.0 <= stats.y_range <= 1.0
    assert 0.0 <= stats.sat_mean <= 1.0


def test_parse_signalstats_empty_returns_neutral() -> None:
    """No YAVG → samples == 0, neutral defaults."""
    stats = _parse_signalstats_metadata("ffmpeg blob with no metadata")
    assert stats.is_empty
    assert stats.samples == 0
    assert stats.y_mean == 0.5
    assert stats.bit_depth == 8


def test_parse_signalstats_handles_garbled_value_lines() -> None:
    """Lines with garbage after ``=`` must not crash the parser."""
    text = "\n".join([
        "lavfi.signalstats.YBITDEPTH=garbage",
        "lavfi.signalstats.YAVG=120",
        "lavfi.signalstats.YMIN=",            # missing value
        "lavfi.signalstats.YMAX=235",
        "lavfi.signalstats.SATAVG=NaN_text",  # not a number
    ])
    stats = _parse_signalstats_metadata(text)
    assert stats.samples == 1
    assert stats.bit_depth == 8  # garbage value ignored, default kept
    assert stats.sat_mean == pytest.approx(0.25)


def test_grade_stats_to_dict_round_trip() -> None:
    s = GradeStats(y_mean=0.5, y_range=0.7, sat_mean=0.25, bit_depth=10, samples=5)
    d = s.to_dict()
    assert d == {
        "y_mean": 0.5, "y_range": 0.7, "sat_mean": 0.25,
        "bit_depth": 10, "samples": 5,
    }


# ── auto_color_grade_filter — decision branches ────────────────────────────


def _stats(y_mean: float, y_range: float, sat_mean: float) -> GradeStats:
    return GradeStats(
        y_mean=y_mean, y_range=y_range, sat_mean=sat_mean,
        bit_depth=8, samples=10,
    )


def test_well_balanced_clip_returns_subtle_pullback() -> None:
    """y_mean≈0.5, y_range≈0.7, sat≈0.25 → only sat=0.98 (every other axis = 1.0)."""
    f = auto_color_grade_filter(_stats(0.50, 0.72, 0.25))
    assert f == "eq=contrast=1.030:saturation=0.980"


def test_underexposed_clip_lifts_gamma() -> None:
    """y_mean=0.32 → gamma should lift up toward 1.10."""
    f = auto_color_grade_filter(_stats(0.32, 0.72, 0.25))
    assert "gamma=1." in f
    # gamma value within clamp [0.92, 1.08]
    gamma_str = [p for p in f.replace("eq=", "").split(":") if p.startswith("gamma=")][0]
    val = float(gamma_str.split("=")[1])
    assert 1.0 < val <= 1.08


def test_overexposed_clip_pulls_gamma_back() -> None:
    """y_mean=0.65 → gamma=0.97 pullback (before clamp)."""
    f = auto_color_grade_filter(_stats(0.65, 0.72, 0.25))
    assert "gamma=0.97" in f


def test_flat_clip_boosts_contrast() -> None:
    """y_range=0.5 (very flat) → contrast lifted to clamp ceiling."""
    f = auto_color_grade_filter(_stats(0.50, 0.50, 0.25))
    assert "contrast=1.080" in f  # at the +8% clamp ceiling


def test_very_desaturated_clip_boosts_saturation() -> None:
    """sat_mean=0.10 → saturation boost +4%."""
    f = auto_color_grade_filter(_stats(0.50, 0.72, 0.10))
    assert "saturation=1.040" in f


def test_punchy_clip_pulls_saturation_back() -> None:
    """sat_mean=0.45 → saturation pullback to 0.96."""
    f = auto_color_grade_filter(_stats(0.50, 0.72, 0.45))
    assert "saturation=0.960" in f


def test_clamp_guard_for_extreme_input() -> None:
    """Even with crazy stats, no axis exceeds the clamp."""
    f = auto_color_grade_filter(_stats(0.05, 0.05, 0.01))
    parts = f.replace("eq=", "").split(":")
    for part in parts:
        _, val = part.split("=")
        v = float(val)
        assert 1.0 - DEFAULT_GRADE_CLAMP_PCT <= v <= 1.0 + DEFAULT_GRADE_CLAMP_PCT


def test_custom_clamp_pct_narrows_range() -> None:
    """clamp_pct=0.03 → contrast ceiling = 1.03."""
    f = auto_color_grade_filter(_stats(0.50, 0.50, 0.25), clamp_pct=0.03)
    parts = f.replace("eq=", "").split(":")
    for part in parts:
        _, val = part.split("=")
        v = float(val)
        assert 0.97 <= v <= 1.03


def test_clamp_pct_validation() -> None:
    with pytest.raises(ValueError, match="clamp_pct"):
        auto_color_grade_filter(_stats(0.5, 0.5, 0.25), clamp_pct=0)
    with pytest.raises(ValueError, match="clamp_pct"):
        auto_color_grade_filter(_stats(0.5, 0.5, 0.25), clamp_pct=0.6)
    with pytest.raises(ValueError, match="clamp_pct"):
        auto_color_grade_filter(_stats(0.5, 0.5, 0.25), clamp_pct=-0.1)


def test_empty_stats_falls_back_to_subtle_preset() -> None:
    """Probe failure (samples=0) → caller still gets a clean baseline."""
    s = GradeStats(y_mean=0.5, y_range=0.72, sat_mean=0.25, samples=0)
    assert auto_color_grade_filter(s) == AUTO_GRADE_PRESETS["subtle"]


def test_drop_threshold_skips_imperceptible_axis() -> None:
    """Adjustments < 0.5% should be omitted from the filter string."""
    # A clip already in the dead-zone for all three axes.
    f = auto_color_grade_filter(_stats(0.50, 0.72, 0.25))
    # contrast=1.03 and sat=0.98 both > 0.5% off → kept; gamma exactly 1 → dropped
    assert "gamma=" not in f
    assert "contrast=1.030" in f and "saturation=0.980" in f


# ── sample_signalstats_sync — orchestration ────────────────────────────────


def test_sample_signalstats_sync_validates_inputs(tmp_path: Path) -> None:
    media = tmp_path / "x.mp4"
    media.write_bytes(b"fake")
    with pytest.raises(ValueError, match="duration"):
        sample_signalstats_sync(media, duration=0)
    with pytest.raises(ValueError, match="n_samples"):
        sample_signalstats_sync(media, n_samples=0)


def test_sample_signalstats_sync_writes_metadata_and_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch ``run_ffmpeg_sync`` to drop a synthetic metadata file in place."""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"fake")

    captured = {}

    def fake_run(cmd, *, timeout_sec, check=True, capture=True, input_bytes=None):
        # The argv contains ``metadata=print:file=<path>`` — extract and write.
        arg = next(a for a in cmd if "metadata=print:file=" in a)
        meta = arg.split("file=", 1)[1]
        Path(meta).write_text(_signalstats_text(
            yavg=[128.0, 132.0],
            ymin=[16.0, 20.0],
            ymax=[220.0, 230.0],
            sat=[60.0, 65.0],
            bit_depth=8,
        ))
        captured["timeout_sec"] = timeout_sec
        captured["argv0"] = cmd[0]
        return FFmpegResult(cmd=list(cmd), returncode=0, stdout="", stderr="", duration_sec=0.1)

    monkeypatch.setattr(ffmpeg_mod, "run_ffmpeg_sync", fake_run)
    monkeypatch.setattr(ffmpeg_mod, "resolve_binary", lambda name: name)

    stats = sample_signalstats_sync(
        media, start=1.0, duration=4.0, n_samples=2, timeout_sec=12.0,
    )
    assert stats.samples == 2
    assert stats.bit_depth == 8
    assert captured["timeout_sec"] == 12.0


def test_sample_signalstats_sync_returns_neutral_on_ffmpeg_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ffmpeg failure must not crash callers — return neutral stats."""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"fake")

    def boom(cmd, **kw):
        raise FFmpegError("boom", cmd=list(cmd), returncode=1, stderr_tail="")

    monkeypatch.setattr(ffmpeg_mod, "run_ffmpeg_sync", boom)
    monkeypatch.setattr(ffmpeg_mod, "resolve_binary", lambda name: name)

    stats = sample_signalstats_sync(media, duration=2.0)
    assert stats.is_empty


def test_sample_signalstats_sync_returns_neutral_when_meta_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the metadata file vanished mid-flight (cleanup race), return neutral."""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"fake")

    def fake_run(cmd, **kw):
        # Do not write the metadata file → reader returns blank → samples=0
        return FFmpegResult(cmd=list(cmd), returncode=0, stdout="", stderr="", duration_sec=0.1)

    monkeypatch.setattr(ffmpeg_mod, "run_ffmpeg_sync", fake_run)
    monkeypatch.setattr(ffmpeg_mod, "resolve_binary", lambda name: name)

    stats = sample_signalstats_sync(media, duration=2.0)
    assert stats.is_empty


def test_sample_signalstats_async_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async wrapper just dispatches to the sync impl in a thread."""
    import asyncio

    media = tmp_path / "v.mp4"
    media.write_bytes(b"fake")

    def fake_sync(*args, **kw):
        return GradeStats(y_mean=0.51, y_range=0.7, sat_mean=0.26, samples=4)

    monkeypatch.setattr(ffmpeg_mod, "sample_signalstats_sync", fake_sync)

    async def go() -> GradeStats:
        return await sample_signalstats(media, duration=2.0)

    stats = asyncio.run(go())
    assert stats.samples == 4
