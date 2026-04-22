"""Tests for _shared.source_review.

Strategy: monkeypatch ``ffprobe_json_sync`` so we can exercise every gate
without needing real media files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from _shared import (
    ReviewIssue,
    ReviewReport,
    ReviewThresholds,
    review_audio,
    review_image,
    review_source,
    review_video,
)
from _shared import source_review as sr


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_video_probe(width: int = 1280, height: int = 720,
                       duration: float = 10.0, fps: str = "30/1",
                       codec: str = "h264") -> dict[str, Any]:
    return {
        "format": {"duration": str(duration)},
        "streams": [{
            "codec_type": "video", "codec_name": codec,
            "width": width, "height": height, "avg_frame_rate": fps,
            "nb_frames": "300",
        }],
    }


def _make_audio_probe(duration: float = 10.0, sr: int = 44100,
                       channels: int = 2, codec: str = "aac") -> dict[str, Any]:
    return {
        "format": {"duration": str(duration)},
        "streams": [{
            "codec_type": "audio", "codec_name": codec,
            "sample_rate": str(sr), "channels": channels,
        }],
    }


def _make_image_probe(width: int = 1920, height: int = 1080,
                       codec: str = "png") -> dict[str, Any]:
    return {
        "format": {"duration": "0"},
        "streams": [{
            "codec_type": "video", "codec_name": codec,
            "width": width, "height": height, "nb_frames": "1",
            "avg_frame_rate": "0/0",
        }],
    }


@pytest.fixture
def fake_file(tmp_path: Path) -> Path:
    """A real (but empty) file so the existence check passes."""
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"")
    return p


def _patch_probe(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(sr, "ffprobe_json_sync",
                        lambda *a, **kw: payload)


# ── Missing / unprobeable file ──────────────────────────────────────────────


def test_review_video_missing_file_returns_error_report() -> None:
    rep = review_video("D:/no/such/path/xyz.mp4")
    assert not rep.passed
    assert any(i.code == "source.missing" for i in rep.errors)


def test_review_video_unprobeable_file_returns_error(
    monkeypatch: pytest.MonkeyPatch, fake_file: Path,
) -> None:
    from _shared.ffmpeg import FFmpegError

    def boom(*a: object, **kw: object) -> dict[str, Any]:
        raise FFmpegError("nope", cmd=["ffprobe"], returncode=1)

    monkeypatch.setattr(sr, "ffprobe_json_sync", boom)
    rep = review_video(fake_file)
    assert not rep.passed
    assert any(i.code == "source.probe_failed" for i in rep.errors)


# ── Video gates ─────────────────────────────────────────────────────────────


def test_review_video_passes_clean_input(
    monkeypatch: pytest.MonkeyPatch, fake_file: Path,
) -> None:
    _patch_probe(monkeypatch, _make_video_probe())
    rep = review_video(fake_file)
    assert rep.passed
    assert rep.kind == "video"
    assert rep.metadata["width"] == 1280
    assert rep.metadata["height"] == 720
    assert rep.metadata["fps"] == 30.0


def test_review_video_low_resolution_fails(
    monkeypatch: pytest.MonkeyPatch, fake_file: Path,
) -> None:
    _patch_probe(monkeypatch, _make_video_probe(width=320, height=240))
    rep = review_video(fake_file)
    assert not rep.passed
    codes = [i.code for i in rep.errors]
    assert "video.resolution_too_low" in codes


def test_review_video_too_short_fails(
    monkeypatch: pytest.MonkeyPatch, fake_file: Path,
) -> None:
    _patch_probe(monkeypatch, _make_video_probe(duration=1.5))
    rep = review_video(fake_file)
    assert not rep.passed
    assert any(i.code == "video.too_short" for i in rep.errors)


def test_review_video_low_fps_warns_but_passes(
    monkeypatch: pytest.MonkeyPatch, fake_file: Path,
) -> None:
    _patch_probe(monkeypatch, _make_video_probe(fps="10/1"))
    rep = review_video(fake_file)
    assert rep.passed  # warning, not error
    assert any(i.code == "video.fps_too_low" for i in rep.warnings)


def test_review_video_too_long_warns(
    monkeypatch: pytest.MonkeyPatch, fake_file: Path,
) -> None:
    _patch_probe(monkeypatch, _make_video_probe(duration=60 * 60 * 2))
    rep = review_video(fake_file)
    assert any(i.code == "video.too_long" for i in rep.warnings)


def test_review_video_no_video_stream(
    monkeypatch: pytest.MonkeyPatch, fake_file: Path,
) -> None:
    _patch_probe(monkeypatch, {"format": {"duration": "10"},
                               "streams": [{"codec_type": "audio"}]})
    rep = review_video(fake_file)
    assert not rep.passed
    assert any(i.code == "video.no_stream" for i in rep.errors)


def test_review_video_custom_thresholds_relax_gates(
    monkeypatch: pytest.MonkeyPatch, fake_file: Path,
) -> None:
    _patch_probe(monkeypatch, _make_video_probe(width=320, height=240,
                                                duration=1.0, fps="10/1"))
    relaxed = ReviewThresholds(video_min_width=320, video_min_height=240,
                               video_min_duration_sec=1.0, video_min_fps=5.0)
    rep = review_video(fake_file, thresholds=relaxed)
    assert rep.passed


# ── Audio gates ─────────────────────────────────────────────────────────────


def test_review_audio_passes_clean_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "fake.wav"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, _make_audio_probe())
    rep = review_audio(p)
    assert rep.passed
    assert rep.kind == "audio"


def test_review_audio_too_short_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "fake.wav"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, _make_audio_probe(duration=1.0))
    rep = review_audio(p)
    assert not rep.passed
    assert any(i.code == "audio.too_short" for i in rep.errors)


def test_review_audio_low_sample_rate_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "fake.wav"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, _make_audio_probe(sr=8000))
    rep = review_audio(p)
    assert rep.passed  # warning
    assert any(i.code == "audio.sample_rate_too_low" for i in rep.warnings)


def test_review_audio_zero_channels_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "fake.wav"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, _make_audio_probe(channels=0))
    rep = review_audio(p)
    assert not rep.passed
    assert any(i.code == "audio.channels_too_few" for i in rep.errors)


def test_review_audio_no_audio_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "fake.wav"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, {"format": {"duration": "10"}, "streams": []})
    rep = review_audio(p)
    assert not rep.passed
    assert any(i.code == "audio.no_stream" for i in rep.errors)


# ── Image gates ─────────────────────────────────────────────────────────────


def test_review_image_passes_hd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "fake.png"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, _make_image_probe())
    rep = review_image(p)
    assert rep.passed
    assert rep.kind == "image"


def test_review_image_thumbnail_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "fake.png"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, _make_image_probe(width=320, height=240))
    rep = review_image(p)
    assert not rep.passed
    assert any(i.code == "image.resolution_too_low" for i in rep.errors)


# ── review_source dispatch ──────────────────────────────────────────────────


def test_review_source_dispatches_video(
    monkeypatch: pytest.MonkeyPatch, fake_file: Path,
) -> None:
    _patch_probe(monkeypatch, _make_video_probe())
    rep = review_source(fake_file)
    assert rep.kind == "video"
    assert rep.passed


def test_review_source_dispatches_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "x.mp3"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, _make_audio_probe())
    rep = review_source(p)
    assert rep.kind == "audio"


def test_review_source_dispatches_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "x.png"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, _make_image_probe())
    rep = review_source(p)
    assert rep.kind == "image"


def test_review_source_unknown_kind_returns_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"")
    _patch_probe(monkeypatch, {"format": {"format_name": "weird"}, "streams": []})
    rep = review_source(p)
    assert not rep.passed
    assert any(i.code == "source.unknown_kind" for i in rep.errors)


# ── ReviewReport semantics ──────────────────────────────────────────────────


def test_report_passed_means_no_errors_only_warnings_ok() -> None:
    rep = ReviewReport(
        source="x", kind="video", metadata={},
        issues=(ReviewIssue("x.warn", "warning", "m", "x", 1, 2),),
    )
    assert rep.passed
    assert len(rep.warnings) == 1
    assert len(rep.errors) == 0


def test_report_to_dict_round_trip() -> None:
    rep = ReviewReport(
        source="x", kind="video",
        metadata={"width": 1280},
        issues=(ReviewIssue("x.err", "error", "m", "k", 1, 2),),
    )
    d = rep.to_dict()
    assert d["passed"] is False
    assert d["metadata"] == {"width": 1280}
    assert d["issues"][0]["code"] == "x.err"


def test_parse_fps_handles_fractional_and_decimal() -> None:
    assert sr._parse_fps("30000/1001") == pytest.approx(29.97, abs=0.01)
    assert sr._parse_fps("24") == 24.0
    assert sr._parse_fps("0/0") == 0.0
    assert sr._parse_fps("") == 0.0
    assert sr._parse_fps("garbage") == 0.0
