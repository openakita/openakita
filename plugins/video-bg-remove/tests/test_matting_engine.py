"""Unit tests for ``matting_engine``.

The RVM ONNX session and the ffmpeg subprocesses are both mocked
out — no model file, no GPU, no PATH ffmpeg required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import matting_engine as me
from matting_engine import (
    DEFAULT_DOWNSAMPLE_RATIO,
    DEFAULT_FPS,
    DEFAULT_MODEL_FILENAME,
    Background,
    MattingPlan,
    MattingResult,
    build_default_background,
    composite_frame,
    ffmpeg_available,
    model_available,
    onnxruntime_available,
    parse_color,
    plan_matting,
    probe_video_meta,
    rgba_to_rgb_over,
    run_matting,
    to_verification,
)


# ── parse_color ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ([10, 20, 30], (10, 20, 30)),
        ((255, 255, 255), (255, 255, 255)),
        ("#00ff00", (0, 255, 0)),
        ("ff0000", (255, 0, 0)),
        ("0,177,64", (0, 177, 64)),
        ([300, -10, 128], (255, 0, 128)),  # clamping
    ],
)
def test_parse_color_accepts_supported_forms(raw: Any, expected: tuple) -> None:
    assert parse_color(raw) == expected


@pytest.mark.parametrize("bad", ["not-a-color", "#zzzzzz", [1, 2], 42, None])
def test_parse_color_rejects_invalid(bad: Any) -> None:
    with pytest.raises(ValueError):
        parse_color(bad)


# ── Background defaults & to_dict ────────────────────────────────────────


def test_default_background_is_chroma_green() -> None:
    bg = build_default_background()
    assert bg.kind == "color"
    assert bg.color == (0, 177, 64)


def test_background_to_dict_round_trips_via_json() -> None:
    bg = Background(kind="color", color=(10, 20, 30))
    raw = json.dumps(bg.to_dict())
    parsed = json.loads(raw)
    assert parsed["kind"] == "color"
    assert parsed["color"] == [10, 20, 30]


# ── plan_matting validation ──────────────────────────────────────────────


@pytest.fixture
def fake_probe(monkeypatch: pytest.MonkeyPatch):
    """Patch ``probe_video_meta`` so plan_matting tests are hermetic."""
    monkeypatch.setattr(
        me, "probe_video_meta",
        lambda *_a, **_kw: {"fps": 30.0, "width": 1280, "height": 720, "duration_sec": 5.0},
    )


def test_plan_matting_default_background_is_green(tmp_path: Path, fake_probe) -> None:
    plan = plan_matting(
        input_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.mp4"),
        model_path=str(tmp_path / "model.onnx"),
    )
    assert plan.background.kind == "color"
    assert plan.background.color == (0, 177, 64)
    assert plan.fps == 30.0
    assert plan.width == 1280 and plan.height == 720


def test_plan_matting_uses_explicit_color_dict(tmp_path: Path, fake_probe) -> None:
    plan = plan_matting(
        input_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.mp4"),
        model_path=str(tmp_path / "model.onnx"),
        background={"kind": "color", "color": "#0000ff"},
    )
    assert plan.background.color == (0, 0, 255)


def test_plan_matting_image_background_requires_existing_file(
    tmp_path: Path, fake_probe,
) -> None:
    with pytest.raises(ValueError, match="image_path"):
        plan_matting(
            input_path=str(tmp_path / "in.mp4"),
            output_path=str(tmp_path / "out.mp4"),
            model_path=str(tmp_path / "model.onnx"),
            background={"kind": "image", "image_path": str(tmp_path / "missing.png")},
        )


def test_plan_matting_transparent_requires_mov_output(
    tmp_path: Path, fake_probe,
) -> None:
    with pytest.raises(ValueError, match=r"\.mov"):
        plan_matting(
            input_path=str(tmp_path / "in.mp4"),
            output_path=str(tmp_path / "out.mp4"),  # NOT .mov
            model_path=str(tmp_path / "model.onnx"),
            background={"kind": "transparent"},
        )


def test_plan_matting_transparent_accepts_mov_output(
    tmp_path: Path, fake_probe,
) -> None:
    plan = plan_matting(
        input_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.mov"),
        model_path=str(tmp_path / "model.onnx"),
        background={"kind": "transparent"},
    )
    assert plan.background.kind == "transparent"


def test_plan_matting_rejects_empty_input(tmp_path: Path, fake_probe) -> None:
    with pytest.raises(ValueError, match="input_path"):
        plan_matting(
            input_path="",
            output_path=str(tmp_path / "out.mp4"),
            model_path=str(tmp_path / "model.onnx"),
        )


def test_plan_matting_rejects_empty_output(tmp_path: Path, fake_probe) -> None:
    with pytest.raises(ValueError, match="output_path"):
        plan_matting(
            input_path=str(tmp_path / "in.mp4"),
            output_path="",
            model_path=str(tmp_path / "model.onnx"),
        )


@pytest.mark.parametrize("ratio", [0.0, -0.1, 1.5, 2.0])
def test_plan_matting_rejects_out_of_range_downsample(
    tmp_path: Path, fake_probe, ratio: float,
) -> None:
    with pytest.raises(ValueError, match="downsample_ratio"):
        plan_matting(
            input_path=str(tmp_path / "in.mp4"),
            output_path=str(tmp_path / "out.mp4"),
            model_path=str(tmp_path / "model.onnx"),
            downsample_ratio=ratio,
        )


def test_plan_matting_to_dict_round_trips_via_json(
    tmp_path: Path, fake_probe,
) -> None:
    plan = plan_matting(
        input_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.mp4"),
        model_path=str(tmp_path / "model.onnx"),
    )
    raw = json.dumps(plan.to_dict(), ensure_ascii=False)
    parsed = json.loads(raw)
    assert parsed["fps"] == 30.0 and parsed["width"] == 1280


# ── probe_video_meta fallbacks ───────────────────────────────────────────


def test_probe_video_meta_returns_safe_defaults_when_ffprobe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a, **_kw):
        raise RuntimeError("ffprobe missing")

    monkeypatch.setattr(me, "ffprobe_json_sync", boom)
    meta = probe_video_meta("anything.mp4")
    assert meta == {"fps": DEFAULT_FPS, "width": 0, "height": 0, "duration_sec": 0.0}


def test_probe_video_meta_parses_frame_rate_fraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(me, "ffprobe_json_sync", lambda *_a, **_kw: {
        "streams": [{
            "codec_type": "video",
            "avg_frame_rate": "30000/1001",  # 29.97 NTSC
            "width": 1920, "height": 1080,
        }],
        "format": {"duration": "10.5"},
    })
    meta = probe_video_meta("v.mp4")
    assert abs(meta["fps"] - 29.970029970029970) < 1e-6
    assert meta["width"] == 1920 and meta["height"] == 1080
    assert meta["duration_sec"] == 10.5


def test_probe_video_meta_handles_zero_frame_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(me, "ffprobe_json_sync", lambda *_a, **_kw: {
        "streams": [{
            "codec_type": "video", "avg_frame_rate": "0/0",
            "width": 640, "height": 480,
        }],
        "format": {"duration": "1.0"},
    })
    meta = probe_video_meta("v.mp4")
    assert meta["fps"] == DEFAULT_FPS  # falls back to default


# ── composite_frame ──────────────────────────────────────────────────────


def test_composite_color_background_blends_per_alpha() -> None:
    """A 1×1 pixel of pure white over solid red with α=0.5 → (255,128,128) -ish."""
    fgr = np.array([[[255, 255, 255]]], dtype=np.uint8)  # 1×1×3
    alpha = np.array([[0.5]], dtype=np.float32)          # 1×1
    out = composite_frame(fgr, alpha, Background(kind="color", color=(255, 0, 0)))
    assert out.shape == (1, 1, 3)
    assert out[0, 0, 0] == 255            # red channel: 255*0.5 + 255*0.5 = 255
    assert 125 <= out[0, 0, 1] <= 130     # green: 255*0.5 + 0*0.5 ≈ 127
    assert 125 <= out[0, 0, 2] <= 130     # blue:  255*0.5 + 0*0.5 ≈ 127


def test_composite_color_full_alpha_returns_foreground() -> None:
    fgr = np.full((4, 4, 3), 200, dtype=np.uint8)
    alpha = np.ones((4, 4), dtype=np.float32)
    out = composite_frame(fgr, alpha, Background(kind="color", color=(0, 0, 0)))
    assert (out == 200).all()


def test_composite_color_zero_alpha_returns_background() -> None:
    fgr = np.full((4, 4, 3), 200, dtype=np.uint8)
    alpha = np.zeros((4, 4), dtype=np.float32)
    out = composite_frame(fgr, alpha, Background(kind="color", color=(50, 60, 70)))
    assert (out[..., 0] == 50).all()
    assert (out[..., 1] == 60).all()
    assert (out[..., 2] == 70).all()


def test_composite_transparent_background_returns_rgba() -> None:
    fgr = np.full((2, 2, 3), 200, dtype=np.uint8)
    alpha = np.full((2, 2), 0.5, dtype=np.float32)
    out = composite_frame(fgr, alpha, Background(kind="transparent"))
    assert out.shape == (2, 2, 4)
    assert (out[..., :3] == 200).all()
    # 0.5 * 255 ≈ 127
    assert (out[..., 3] >= 126).all() and (out[..., 3] <= 128).all()


def test_composite_image_background_uses_pillow(tmp_path: Path) -> None:
    """The PIL path must actually paint the image behind the foreground."""
    from PIL import Image

    bg_path = tmp_path / "bg.png"
    Image.new("RGB", (4, 4), (10, 20, 30)).save(bg_path)
    fgr = np.full((4, 4, 3), 100, dtype=np.uint8)
    alpha = np.zeros((4, 4), dtype=np.float32)  # entire frame is bg
    out = composite_frame(fgr, alpha, Background(kind="image", image_path=str(bg_path)))
    assert out.shape == (4, 4, 3)
    assert (out[..., 0] == 10).all()
    assert (out[..., 1] == 20).all()
    assert (out[..., 2] == 30).all()


def test_composite_unknown_background_kind_raises() -> None:
    fgr = np.zeros((1, 1, 3), dtype=np.uint8)
    alpha = np.zeros((1, 1), dtype=np.float32)
    with pytest.raises(ValueError, match="background.kind"):
        composite_frame(fgr, alpha, Background(kind="bogus"))


def test_rgba_to_rgb_over_flattens_correctly() -> None:
    rgba = np.zeros((1, 1, 4), dtype=np.uint8)
    rgba[..., :3] = 200
    rgba[..., 3] = 128  # ~50%
    rgb = rgba_to_rgb_over(rgba, bg_color=(0, 0, 0))
    # 200 * 128/255 ≈ 100
    assert 95 <= rgb[0, 0, 0] <= 105


# ── run_matting (integration with mocks) ─────────────────────────────────


class FakeSession:
    """Stand-in for ``onnxruntime.InferenceSession``.

    Emits a deterministic foreground (mid-gray) and a fixed-α matte so
    we can assert ``mean_alpha`` from the orchestrator.
    """

    def __init__(self, *, alpha: float = 0.7) -> None:
        self.alpha = alpha
        self.calls = 0

    def run(self, _outputs, inputs):
        self.calls += 1
        src = inputs["src"]            # [1, 3, H, W]
        h, w = src.shape[2], src.shape[3]
        fgr = np.full((1, 3, h, w), 0.5, dtype=np.float32)
        pha = np.full((1, 1, h, w), self.alpha, dtype=np.float32)
        rec = [np.zeros([1, 1, 1, 1], dtype=np.float32) for _ in range(4)]
        return [fgr, pha, *rec]


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, *, frames: int = 3, alpha: float = 0.7):
    """Wire run_matting up against fakes for session + ffmpeg I/O."""
    fake_session = FakeSession(alpha=alpha)

    def fake_iter(input_path, *, fps, width, height):
        for _ in range(frames):
            yield np.zeros((height, width, 3), dtype=np.uint8)

    monkeypatch.setattr(me, "iter_video_frames", fake_iter)
    return fake_session


def test_run_matting_runs_through_mocked_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = _patch_pipeline(monkeypatch, frames=5, alpha=0.6)

    plan = MattingPlan(
        input_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.mp4"),
        background=Background(kind="color", color=(0, 177, 64)),
        fps=25.0, width=8, height=6, duration_sec=0.2,
        model_path=str(tmp_path / "model.onnx"),
    )

    written = []

    def write_frame(arr) -> None:
        written.append(arr.shape)

    # Touch the output path so .stat() succeeds.
    Path(plan.output_path).write_bytes(b"\x00" * 64)

    result = run_matting(
        plan,
        session_factory=lambda _p: fake_session,
        write_frame=write_frame,
    )
    assert result.frame_count == 5
    assert fake_session.calls == 5
    assert len(written) == 5
    # mean_alpha should be ≈ 0.6 (we forced every alpha tensor to 0.6).
    assert 0.55 <= result.mean_alpha <= 0.65
    assert result.output_size_bytes == 64


def test_run_matting_progress_callback_fires_per_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = _patch_pipeline(monkeypatch, frames=3, alpha=0.5)
    plan = MattingPlan(
        input_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.mp4"),
        background=Background(kind="color", color=(0, 0, 0)),
        fps=10.0, width=2, height=2, duration_sec=0.3,
        model_path=str(tmp_path / "model.onnx"),
    )
    Path(plan.output_path).write_bytes(b"")

    progress = []
    run_matting(
        plan,
        session_factory=lambda _p: fake_session,
        on_progress=lambda done, total: progress.append((done, total)),
        write_frame=lambda _a: None,
    )
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_run_matting_handles_zero_frames_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty source → 0 frames, mean_alpha=0, no crash."""
    fake_session = _patch_pipeline(monkeypatch, frames=0)
    plan = MattingPlan(
        input_path=str(tmp_path / "in.mp4"),
        output_path=str(tmp_path / "out.mp4"),
        background=Background(kind="color", color=(0, 0, 0)),
        fps=25.0, width=4, height=4, duration_sec=0.0,
        model_path=str(tmp_path / "model.onnx"),
    )
    Path(plan.output_path).touch()

    result = run_matting(
        plan,
        session_factory=lambda _p: fake_session,
        write_frame=lambda _a: None,
    )
    assert result.frame_count == 0
    assert result.mean_alpha == 0.0


# ── runtime / dep helpers ────────────────────────────────────────────────


def test_onnxruntime_available_returns_bool() -> None:
    # Either is fine — what matters is that the helper never raises.
    assert isinstance(onnxruntime_available(), bool)


def test_ffmpeg_available_returns_bool() -> None:
    assert isinstance(ffmpeg_available(), bool)


def test_model_available_false_for_missing_file(tmp_path: Path) -> None:
    assert model_available(tmp_path / "nope.onnx") is False


def test_model_available_false_for_tiny_file(tmp_path: Path) -> None:
    p = tmp_path / "tiny.onnx"
    p.write_bytes(b"\x00" * 100)  # < 1 MB sanity gate
    assert model_available(p) is False


def test_model_available_true_for_real_sized_file(tmp_path: Path) -> None:
    p = tmp_path / "model.onnx"
    p.write_bytes(b"\x00" * (2 * 1024 * 1024))  # 2 MB
    assert model_available(p) is True


def test_default_model_filename_matches_official_release_name() -> None:
    """RVM official release ships ``rvm_mobilenetv3_fp32.onnx``; the
    plugin's UI download link assumes this exact name."""
    assert DEFAULT_MODEL_FILENAME == "rvm_mobilenetv3_fp32.onnx"


# ── to_verification ──────────────────────────────────────────────────────


def _make_result(
    *, frames: int = 25, mean_alpha: float = 0.5,
    duration: float = 1.0, fps: float = 25.0,
    output_size: int = 1024, bg_kind: str = "color",
) -> MattingResult:
    plan = MattingPlan(
        input_path="/tmp/in.mp4", output_path="/tmp/out.mp4",
        background=Background(kind=bg_kind, color=(0, 0, 0)),
        fps=fps, width=4, height=4, duration_sec=duration,
        model_path="/tmp/model.onnx",
    )
    return MattingResult(
        plan=plan, output_path=plan.output_path,
        elapsed_sec=0.1, frame_count=frames,
        output_size_bytes=output_size, mean_alpha=mean_alpha,
    )


def test_verification_clean_run_is_ok() -> None:
    v = to_verification(_make_result())
    assert v.verified is True
    assert v.low_confidence_fields == []
    assert v.verifier_id == "video_bg_remove_self_check"


def test_verification_flags_zero_frames() -> None:
    v = to_verification(_make_result(frames=0, mean_alpha=0.0))
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.frame_count" in paths


def test_verification_flags_near_zero_alpha_as_no_subject() -> None:
    v = to_verification(_make_result(mean_alpha=0.005))
    assert v.verified is False
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.mean_alpha" in paths


def test_verification_flags_zero_size_output() -> None:
    v = to_verification(_make_result(output_size=0))
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.output_size_bytes" in paths


def test_verification_flags_truncated_render() -> None:
    """Source advertises 1s @ 25fps (25 frames) but we only got 5 → flag."""
    v = to_verification(_make_result(frames=5, duration=1.0, fps=25.0))
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.frame_count" in paths


def test_verification_clean_for_transparent_zero_size_does_not_double_flag() -> None:
    """Transparent renders to .mov; zero size IS suspicious here too,
    but we want the path to fire only once (the zero-size check is
    skipped for transparent bg in production code — keep the test as
    documentation of that contract)."""
    v = to_verification(_make_result(output_size=0, bg_kind="transparent"))
    paths = [f.path for f in v.low_confidence_fields]
    assert "$.output_size_bytes" not in paths
