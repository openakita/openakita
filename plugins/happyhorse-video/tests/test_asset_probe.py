"""asset_probe — image / audio / video probe + per-endpoint assertions.

These tests exercise the public API without relying on a working
ffprobe binary: a 1x1 PNG written via Pillow is enough to verify the
image branch end-to-end, and the audio/video branches use ffprobe when
available (the CI image installs it) but fall back to size-only checks
otherwise. The point of the test is that probes NEVER raise — only the
``assert_*`` helpers do, and only with :class:`AssetSpecError`.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest
from happyhorse_inline.asset_probe import (
    AssetSpecError,
    AudioProbe,
    ImageProbe,
    VideoProbe,
    assert_animate_image,
    assert_animate_video,
    assert_s2v_audio,
    assert_s2v_image,
    assert_videoretalk_audio,
    probe_audio,
    probe_image,
    probe_video,
)


# ─── Helpers ─────────────────────────────────────────────────────────


def _has_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


@pytest.fixture
def tiny_png(tmp_path: Path) -> Path:
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    img = Image.new("RGB", (800, 600), color=(128, 64, 64))
    out = tmp_path / "tiny.png"
    img.save(out, format="PNG")
    return out


@pytest.fixture
def big_png(tmp_path: Path) -> Path:
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    img = Image.new("RGB", (8000, 8000), color=(0, 0, 0))
    out = tmp_path / "big.png"
    img.save(out, format="PNG")
    return out


# ─── Low-level probes don't raise ────────────────────────────────────


def test_probe_image_extracts_dimensions(tiny_png: Path):
    probe = probe_image(tiny_png)
    assert isinstance(probe, ImageProbe)
    assert probe.width == 800
    assert probe.height == 600
    assert probe.fmt == "png"
    assert probe.size_bytes > 0


def test_probe_image_missing_file_returns_zero():
    probe = probe_image("/no/such/file.png")
    assert probe.width == 0
    assert probe.size_bytes == 0


def test_probe_audio_returns_dataclass_for_missing_file():
    probe = probe_audio("/no/such/file.mp3")
    assert isinstance(probe, AudioProbe)
    assert probe.duration_sec == 0.0


def test_probe_video_returns_dataclass_for_missing_file():
    probe = probe_video("/no/such/file.mp4")
    assert isinstance(probe, VideoProbe)
    assert probe.duration_sec == 0.0


# ─── assert_s2v_image — spec 400..7000 px JPG/PNG/BMP/WEBP ──────────


def test_assert_s2v_image_accepts_in_range(tiny_png: Path):
    assert_s2v_image(tiny_png)


def test_assert_s2v_image_rejects_too_large(big_png: Path):
    with pytest.raises(AssetSpecError, match="7000"):
        assert_s2v_image(big_png)


def test_assert_s2v_image_rejects_too_small(tmp_path: Path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    img = Image.new("RGB", (100, 100))
    out = tmp_path / "tiny2.png"
    img.save(out, format="PNG")
    with pytest.raises(AssetSpecError, match="400"):
        assert_s2v_image(out)


def test_assert_s2v_image_rejects_bad_format(tmp_path: Path):
    # Write raw bytes with .gif extension — passes ext check but fails spec.
    out = tmp_path / "bad.gif"
    out.write_bytes(b"GIF89a")
    with pytest.raises(AssetSpecError, match="格式"):
        assert_s2v_image(out)


# ─── assert_animate_image — spec 200..4096 px, <=5 MB ───────────────


def test_assert_animate_image_accepts_in_range(tiny_png: Path):
    assert_animate_image(tiny_png)


def test_assert_animate_image_rejects_oversize(tmp_path: Path):
    PIL = pytest.importorskip("PIL")
    from PIL import Image

    # Pillow can't easily produce >5 MB JPEG quickly; emulate by writing
    # padded bytes with a valid PNG header so the size check fires before
    # PIL even opens (size_bytes is from stat()).
    out = tmp_path / "fat.png"
    PIL_img = Image.new("RGB", (1024, 1024), color=(255, 0, 0))
    PIL_img.save(out, format="PNG")
    # Pad up to 6 MB so the size check trips:
    with open(out, "ab") as fh:
        fh.write(b"\0" * (6 * 1024 * 1024))
    with pytest.raises(AssetSpecError, match="5 MB"):
        assert_animate_image(out)


# ─── assert_videoretalk_audio (size + ext checks always run) ────────


def test_assert_videoretalk_audio_rejects_oversize(tmp_path: Path):
    out = tmp_path / "huge.wav"
    out.write_bytes(b"RIFF" + b"\0" * (31 * 1024 * 1024))
    with pytest.raises(AssetSpecError, match="30 MB"):
        assert_videoretalk_audio(out)


def test_assert_videoretalk_audio_rejects_bad_ext(tmp_path: Path):
    out = tmp_path / "song.flac"
    out.write_bytes(b"fLaC" + b"\0" * 1024)
    with pytest.raises(AssetSpecError, match="格式"):
        assert_videoretalk_audio(out)


# ─── assert_s2v_audio (size + ext checks) ───────────────────────────


def test_assert_s2v_audio_rejects_oversize(tmp_path: Path):
    out = tmp_path / "huge2.wav"
    out.write_bytes(b"RIFF" + b"\0" * (16 * 1024 * 1024))
    with pytest.raises(AssetSpecError, match="15 MB"):
        assert_s2v_audio(out)


def test_assert_s2v_audio_rejects_bad_ext(tmp_path: Path):
    out = tmp_path / "song.aac"
    out.write_bytes(b"\0" * 1024)
    with pytest.raises(AssetSpecError, match="格式"):
        assert_s2v_audio(out)


# ─── assert_animate_video (size + ext checks) ───────────────────────


def test_assert_animate_video_rejects_oversize(tmp_path: Path):
    out = tmp_path / "huge.mp4"
    out.write_bytes(b"\0\0\0\x20ftypmp42" + b"\0" * (201 * 1024 * 1024))
    with pytest.raises(AssetSpecError, match="200 MB"):
        assert_animate_video(out)


def test_assert_animate_video_rejects_bad_ext(tmp_path: Path):
    out = tmp_path / "clip.webm"
    out.write_bytes(b"\0" * 1024)
    with pytest.raises(AssetSpecError, match="格式"):
        assert_animate_video(out)
