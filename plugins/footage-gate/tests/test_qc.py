"""Unit tests for footage_gate_qc — 4 checkers + 5 defenses."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import footage_gate_qc as qc
import numpy as np
import pytest
from footage_gate_qc import (
    Issue,
    NormalizedEdl,
    boundary_frame_check,
    duration_check,
    parse_edl,
    preprocess_image_cuts,
    run_qc_with_remux,
    subtitle_overlay_check,
    waveform_spike_check,
)

# ── parse_edl — Issue #43 belt-and-suspenders ─────────────────────────────


class TestParseEdl:
    def test_standard_naming_passes_through(self) -> None:
        payload = {
            "cuts": [{"in_seconds": 0.0, "out_seconds": 2.0, "source": {"path": "a.mp4"}}],
            "output_resolution": [1920, 1080],
        }
        edl = parse_edl(payload)
        assert edl.cuts[0]["in_seconds"] == 0.0
        assert edl.cuts[0]["out_seconds"] == 2.0
        assert edl.field_naming == "standard"

    def test_legacy_start_seconds_normalized(self) -> None:
        payload = {
            "cuts": [{"start_seconds": 1.0, "end_seconds": 5.0, "source": {"path": "a.mp4"}}]
        }
        edl = parse_edl(payload)
        assert edl.cuts[0]["in_seconds"] == 1.0
        assert edl.cuts[0]["out_seconds"] == 5.0
        assert edl.field_naming == "legacy"

    def test_total_duration_computed_when_missing(self) -> None:
        edl = parse_edl(
            {
                "cuts": [
                    {"in_seconds": 0.0, "out_seconds": 1.0, "source": {}},
                    {"in_seconds": 1.0, "out_seconds": 3.5, "source": {}},
                ]
            }
        )
        assert edl.total_duration_s == pytest.approx(3.5)

    def test_string_payload_accepted(self) -> None:
        edl = parse_edl(
            '{"cuts":[{"in_seconds":0,"out_seconds":1,"source":{}}],"total_duration_s":1}'
        )
        assert edl.cuts[0]["out_seconds"] == 1.0

    def test_resolution_dict_form(self) -> None:
        edl = parse_edl(
            {
                "cuts": [],
                "output_resolution": {"width": 1080, "height": 1920},
            }
        )
        assert edl.output_resolution == (1080, 1920)


# ── preprocess_image_cuts — Issue #42 ─────────────────────────────────────


class TestPreprocessImageCuts:
    def test_image_cut_rewritten_to_mp4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_calls: list[Any] = []

        def fake_run_ffmpeg(args, **_kw):
            run_calls.append(args)
            # Touch the output file so the rewrite path triggers.
            for token in args:
                if isinstance(token, str) and token.endswith(".mp4"):
                    Path(token).write_bytes(b"x")

        monkeypatch.setattr(qc, "run_ffmpeg", fake_run_ffmpeg)

        edl = parse_edl(
            {
                "cuts": [
                    {
                        "in_seconds": 0.0,
                        "out_seconds": 2.0,
                        "source": {"media_type": "image", "path": "/tmp/x.png"},
                    }
                ],
                "output_resolution": [1080, 1920],
            }
        )
        edl, info = preprocess_image_cuts(edl, work_dir=tmp_path / "imgs", fps=30)
        assert edl.cuts[0]["source"]["media_type"] == "video"
        assert edl.cuts[0]["source"]["original_image_path"] == "/tmp/x.png"
        assert edl.cuts[0]["source"]["path"].endswith(".mp4")
        assert info[0].kind == "image_cut_preprocessed"
        # Verify ffmpeg called with the expected loop / size args.
        joined = " ".join(map(str, run_calls[0]))
        assert "-loop" in joined and "1" in joined
        assert "1080x1920" in joined

    def test_video_cut_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            qc, "run_ffmpeg", MagicMock(side_effect=AssertionError("should not run"))
        )
        edl = parse_edl(
            {
                "cuts": [
                    {
                        "in_seconds": 0.0,
                        "out_seconds": 2.0,
                        "source": {"media_type": "video", "path": "x.mp4"},
                    }
                ]
            }
        )
        edl, info = preprocess_image_cuts(edl, work_dir=tmp_path)
        assert info == []
        assert edl.cuts[0]["source"]["path"] == "x.mp4"


# ── boundary_frame_check ──────────────────────────────────────────────────


def _solid_image(width: int, height: int, value: int, dest: Path) -> Path:
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", (width, height), color=value).save(dest)
    return dest


class TestBoundaryFrameCheck:
    def test_no_diff_returns_no_issue(self, tmp_path: Path) -> None:
        def fake_extract(_video, *, timestamps, dest_dir, **_kw):
            paths = [
                _solid_image(160, 90, 128, dest_dir / f"f_{i}.png")
                for i, _ in enumerate(timestamps)
            ]
            return paths

        edl = parse_edl(
            {
                "cuts": [
                    {"in_seconds": 0.0, "out_seconds": 2.0, "source": {}},
                    {"in_seconds": 2.0, "out_seconds": 4.0, "source": {}},
                ]
            }
        )
        out = boundary_frame_check(
            tmp_path / "v.mp4",
            edl.cuts,
            extract_frames_fn=fake_extract,
            work_dir=tmp_path / "frames",
        )
        assert out == []

    def test_huge_diff_flags(self, tmp_path: Path) -> None:
        def fake_extract(_video, *, timestamps, dest_dir, **_kw):
            paths = []
            for i, _ in enumerate(timestamps):
                value = 10 if i % 2 == 0 else 240
                paths.append(_solid_image(160, 90, value, dest_dir / f"f_{i}.png"))
            return paths

        edl = parse_edl(
            {
                "cuts": [
                    {"in_seconds": 0.0, "out_seconds": 2.0, "source": {}},
                    {"in_seconds": 2.0, "out_seconds": 4.0, "source": {}},
                ]
            }
        )
        out = boundary_frame_check(
            tmp_path / "v.mp4",
            edl.cuts,
            extract_frames_fn=fake_extract,
            work_dir=tmp_path / "frames",
            samples=4,
        )
        assert len(out) == 1
        assert out[0].kind == "bad_cut_visual"
        assert out[0].cut_index == 1


# ── waveform_spike_check ──────────────────────────────────────────────────


class TestWaveformSpikeCheck:
    def test_quiet_envelope_returns_no_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = np.full(2000, 0.1, dtype=np.float32)
        monkeypatch.setattr(qc, "compute_envelope", lambda *_a, **_kw: env)
        monkeypatch.setattr(
            qc,
            "ffprobe_json",
            lambda *_a, **_kw: {"format": {"duration": 10.0}},
        )
        edl = parse_edl(
            {
                "cuts": [
                    {"in_seconds": 0.0, "out_seconds": 5.0, "source": {}},
                    {"in_seconds": 5.0, "out_seconds": 10.0, "source": {}},
                ]
            }
        )
        out = waveform_spike_check(
            tmp_path / "v.mp4",
            edl.cuts,
            compute_envelope_fn=lambda *_a, **_kw: env,
        )
        assert out == []

    def test_spike_at_cut_flags(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env = np.zeros(2000, dtype=np.float32)
        env[1000] = 0.99  # spike at the cut location
        monkeypatch.setattr(
            qc,
            "ffprobe_json",
            lambda *_a, **_kw: {"format": {"duration": 10.0}},
        )
        edl = parse_edl(
            {
                "cuts": [
                    {"in_seconds": 0.0, "out_seconds": 5.0, "source": {}},
                    {"in_seconds": 5.0, "out_seconds": 10.0, "source": {}},
                ]
            }
        )
        out = waveform_spike_check(
            tmp_path / "v.mp4",
            edl.cuts,
            compute_envelope_fn=lambda *_a, **_kw: env,
            threshold=0.85,
        )
        assert len(out) == 1
        assert out[0].kind == "bad_cut_audio_spike"


# ── subtitle_overlay_check — PR #5 vertical safe-zone defense ─────────────


class TestSubtitleOverlayCheck:
    def test_landscape_ignored(self) -> None:
        edl = parse_edl(
            {
                "cuts": [],
                "output_resolution": [1920, 1080],
                "subtitles": [{"id": "s1", "MarginV": 10}],
            }
        )
        assert subtitle_overlay_check(edl) == []

    def test_portrait_below_safe_zone_flags(self) -> None:
        edl = parse_edl(
            {
                "cuts": [],
                "output_resolution": [1080, 1920],
                "subtitles": [{"id": "s1", "MarginV": 35}],
            }
        )
        out = subtitle_overlay_check(edl)
        assert len(out) == 1
        assert out[0].kind == "subtitle_in_safe_zone"
        assert out[0].payload["margin_v"] == 35

    def test_portrait_above_safe_zone_passes(self) -> None:
        edl = parse_edl(
            {
                "cuts": [],
                "output_resolution": [1080, 1920],
                "subtitles": [{"id": "s1", "MarginV": 95}],
            }
        )
        assert subtitle_overlay_check(edl) == []

    def test_filter_chain_overlay_after_subs_flags(self) -> None:
        edl = parse_edl(
            {
                "cuts": [],
                "output_resolution": [1920, 1080],
                "filter_chain": ["subtitles=s.srt", "overlay=10:10"],
            }
        )
        out = subtitle_overlay_check(edl)
        assert any(i.kind == "subtitle_overlay_order" for i in out)


# ── duration_check ────────────────────────────────────────────────────────


class TestDurationCheck:
    def test_within_tolerance_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            qc,
            "ffprobe_json",
            lambda *_a, **_kw: {"format": {"duration": 10.2}},
        )
        edl = parse_edl({"cuts": [], "total_duration_s": 10.0})
        assert duration_check(tmp_path / "v.mp4", edl) == []

    def test_outside_tolerance_flags(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            qc,
            "ffprobe_json",
            lambda *_a, **_kw: {"format": {"duration": 12.0}},
        )
        edl = parse_edl({"cuts": [], "total_duration_s": 10.0})
        out = duration_check(tmp_path / "v.mp4", edl)
        assert len(out) == 1
        assert out[0].kind == "duration_mismatch"


# ── run_qc_with_remux — full loop with all 5 defenses ─────────────────────


class TestRunQcWithRemux:
    def test_no_auto_remux_no_fixes_attempted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force one warning so we'd remux IF auto_remux were True.
        monkeypatch.setattr(
            qc,
            "run_all_checks",
            lambda *_a, **_kw: [Issue(kind="bad_cut_visual", severity="warning", message="x")],
        )
        monkeypatch.setattr(qc, "render_qc_grid", lambda *_a, **_kw: None)
        monkeypatch.setattr(qc, "preprocess_image_cuts", lambda edl, **_kw: (edl, []))
        remux_calls: list[Any] = []

        def remux_should_not_fire(*a, **kw):
            remux_calls.append((a, kw))
            return tmp_path / "boom.mp4"

        result = run_qc_with_remux(
            tmp_path / "v.mp4",
            {"cuts": [{"in_seconds": 0, "out_seconds": 5, "source": {}}]},
            work_dir=tmp_path / "qc",
            auto_remux=False,
            remux_fn=remux_should_not_fire,
        )
        assert remux_calls == []
        assert result.attempts == 0
        assert any(i.kind == "bad_cut_visual" for i in result.issues)

    def test_auto_remux_loop_capped_at_max_attempts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            qc,
            "run_all_checks",
            lambda *_a, **_kw: [
                Issue(
                    kind="bad_cut_visual",
                    severity="warning",
                    message="x",
                    cut_index=1,
                )
            ],
        )
        monkeypatch.setattr(qc, "render_qc_grid", lambda *_a, **_kw: None)
        monkeypatch.setattr(qc, "preprocess_image_cuts", lambda edl, **_kw: (edl, []))
        attempts: list[int] = []

        def fake_remux(_edl, output, **_kw):
            attempts.append(1)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"x")
            return output

        result = run_qc_with_remux(
            tmp_path / "v.mp4",
            {
                "cuts": [
                    {"in_seconds": 0, "out_seconds": 5, "source": {"path": "a.mp4"}},
                    {"in_seconds": 5, "out_seconds": 10, "source": {"path": "a.mp4"}},
                ]
            },
            work_dir=tmp_path / "qc",
            auto_remux=True,
            max_attempts=3,
            remux_fn=fake_remux,
        )
        assert len(attempts) == 3
        assert result.attempts == 3

    def test_legacy_field_naming_emits_info(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(qc, "run_all_checks", lambda *_a, **_kw: [])
        monkeypatch.setattr(qc, "render_qc_grid", lambda *_a, **_kw: None)
        monkeypatch.setattr(qc, "preprocess_image_cuts", lambda edl, **_kw: (edl, []))

        result = run_qc_with_remux(
            tmp_path / "v.mp4",
            {"cuts": [{"start_seconds": 0, "end_seconds": 1, "source": {}}]},
            work_dir=tmp_path / "qc",
        )
        assert result.naming_normalized
        assert any(i.kind == "edl_field_normalized" for i in result.issues)


# ── render_qc_grid smoke ──────────────────────────────────────────────────


class TestRenderQcGrid:
    def test_renders_png_from_extracted_frames(self, tmp_path: Path) -> None:
        def fake_extract(_video, *, timestamps, dest_dir, **_kw):
            return [
                _solid_image(320, 180, 100 + i * 30, dest_dir / f"f_{i}.png")
                for i, _ in enumerate(timestamps)
            ]

        edl: NormalizedEdl = parse_edl(
            {
                "cuts": [
                    {"in_seconds": 0.5, "out_seconds": 1.0, "source": {}},
                    {"in_seconds": 1.0, "out_seconds": 2.0, "source": {}},
                    {"in_seconds": 2.0, "out_seconds": 3.0, "source": {}},
                    {"in_seconds": 3.0, "out_seconds": 4.0, "source": {}},
                ]
            }
        )
        dest = tmp_path / "grid.png"
        out = qc.render_qc_grid(
            tmp_path / "v.mp4",
            edl,
            dest,
            extract_frames_fn=fake_extract,
        )
        assert out == dest
        assert dest.is_file()
