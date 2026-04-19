"""Tests for openakita_plugin_sdk.contrib.render_pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita_plugin_sdk.contrib import RenderPipeline, build_render_pipeline
from openakita_plugin_sdk.contrib.render_pipeline import RenderSegment


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    """Provide a deterministic ffmpeg path on any OS by stubbing shutil.which."""
    fake = str(tmp_path / "fake_ffmpeg.exe")
    Path(fake).write_text("")  # so .is_absolute()=True branch picks it up too

    def _which(name: str) -> str:
        return fake if "ffmpeg" in name else ""

    monkeypatch.setattr("openakita_plugin_sdk.contrib.render_pipeline.shutil.which", _which)
    return fake


def test_simple_command_includes_safety_defaults(tmp_path, fake_ffmpeg) -> None:
    pipe = build_render_pipeline(
        segments=[(str(tmp_path / "in.mp4"), 1.0, 5.0)],
        output=str(tmp_path / "out.mp4"),
    )
    cmd = pipe.to_simple_command(ffmpeg="ffmpeg")
    assert "ffmpeg" in cmd[0]
    joined = " ".join(cmd)
    # hard rules
    assert "yuv420p" in joined          # pix_fmt
    assert "fps=24" in joined           # fps filter
    assert "setpts=PTS-STARTPTS" in joined
    assert "afade=t=in" in joined and "afade=t=out" in joined
    assert "+faststart" in joined
    assert "-ss 1.000" in joined and "-t 4.000" in joined  # trim


def test_dict_segment_works(tmp_path) -> None:
    pipe = build_render_pipeline(
        segments=[{"source": str(tmp_path / "a.mp4"), "start": 0, "end": 3, "label": "x"}],
        output=str(tmp_path / "out.mp4"),
    )
    assert pipe.segments[0].label == "x"
    assert pipe.segments[0].duration == 3.0


def test_rendersegment_passthrough() -> None:
    s = RenderSegment(source="a.mp4", start=0, end=5, audio_fade_ms=50)
    pipe = build_render_pipeline(segments=[s], output="o.mp4")
    assert pipe.segments[0].audio_fade_ms == 50


def test_unknown_segment_type_raises() -> None:
    with pytest.raises(TypeError):
        build_render_pipeline(segments=[123], output="o.mp4")


def test_simple_command_rejects_multi_segment(tmp_path) -> None:
    pipe = build_render_pipeline(
        segments=[
            {"source": "a.mp4", "start": 0, "end": 1},
            {"source": "b.mp4", "start": 0, "end": 1},
        ],
        output=str(tmp_path / "out.mp4"),
    )
    with pytest.raises(ValueError):
        pipe.to_simple_command(ffmpeg="/usr/bin/ffmpeg")


def test_concat_command_includes_demuxer_flags(tmp_path, fake_ffmpeg) -> None:
    pipe = build_render_pipeline(
        segments=[{"source": "a.mp4", "start": 0, "end": 1}],
        output=str(tmp_path / "out.mp4"),
    )
    cmd = pipe.to_concat_command(list_file=str(tmp_path / "list.txt"), ffmpeg="ffmpeg")
    joined = " ".join(cmd)
    assert "-f concat" in joined and "-safe 0" in joined


def test_write_concat_list_quotes_paths(tmp_path) -> None:
    pipe = build_render_pipeline(
        segments=[
            {"source": tmp_path / "a b.mp4", "start": 0, "end": 1},
            {"source": tmp_path / "c.mp4",   "start": 0, "end": 1},
        ],
        output=str(tmp_path / "out.mp4"),
    )
    listfile = tmp_path / "list.txt"
    pipe.write_concat_list(listfile)
    text = listfile.read_text(encoding="utf-8")
    assert "file '" in text
    assert "a b.mp4" in text


def test_loudness_pass_commands_well_formed(tmp_path, fake_ffmpeg) -> None:
    p1 = RenderPipeline.loudness_pass1_command(
        tmp_path / "in.mp4", target_lufs=-16.0, ffmpeg="ffmpeg",
    )
    assert "loudnorm" in " ".join(p1)
    p2 = RenderPipeline.loudness_pass2_command(
        tmp_path / "in.mp4", tmp_path / "out.mp4",
        measured={"input_i": -20.0, "input_tp": -1.0, "input_lra": 5.0,
                  "input_thresh": -30.0, "target_offset": 0.5},
        ffmpeg="ffmpeg",
    )
    joined = " ".join(p2)
    assert "measured_I=-20.0" in joined
    assert "linear=true" in joined


def test_resolve_bin_raises_friendly(tmp_path) -> None:
    pipe = build_render_pipeline(
        segments=[{"source": "a.mp4", "start": 0, "end": 1}],
        output=str(tmp_path / "out.mp4"),
    )
    with pytest.raises(RuntimeError, match="not found in PATH"):
        pipe.to_simple_command(ffmpeg="definitely_not_ffmpeg_xyz")
