"""Unit tests for ``mediapost_recompose``.

Coverage targets per ``docs/media-post-plan.md`` §6.2 + §11 Phase 3:

- ``compute_crop_dims`` matches §2.4 outputs (608x1080, 1080x1080).
- ``ema_smooth`` math + edge cases.
- ``build_crop_x_expression`` produces nested ifs in correct order.
- ``_downsample_to_depth_cap`` keeps last point + caps depth at 95.
- ``_detect_x_center`` falls back to default when bbox missing.
- ``smart_recompose`` happy path with stubbed ffmpeg helpers.
- Failure injection: ``_detect_subjects`` returning all None still
  produces a center-fallback trajectory.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mediapost_models import MediaPostError
from mediapost_recompose import (
    MAX_CROP_EXPR_DEPTH,
    RecomposeContext,
    _build_trajectory,
    _detect_x_center,
    _downsample_to_depth_cap,
    build_crop_x_expression,
    compute_crop_dims,
    ema_smooth,
    smart_recompose,
)


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestComputeCropDims:
    def test_horizontal_to_9_16(self) -> None:
        crop_w, crop_h = compute_crop_dims(1920, 1080, "9:16")
        assert (crop_w, crop_h) == (608, 1080)

    def test_horizontal_to_1_1(self) -> None:
        crop_w, crop_h = compute_crop_dims(1920, 1080, "1:1")
        assert (crop_w, crop_h) == (1080, 1080)

    def test_vertical_source_to_9_16(self) -> None:
        crop_w, crop_h = compute_crop_dims(1080, 1920, "9:16")
        assert crop_w == 1080
        assert abs(crop_h - 1920) <= 2

    def test_invalid_ratio_raises_format(self) -> None:
        with pytest.raises(MediaPostError) as ei:
            compute_crop_dims(1920, 1080, "weird")
        assert ei.value.kind == "format"


class TestEmaSmooth:
    def test_empty(self) -> None:
        assert ema_smooth([], 0.15) == []

    def test_single_value(self) -> None:
        assert ema_smooth([100.0], 0.15) == [100.0]

    def test_smooths_step_change(self) -> None:
        out = ema_smooth([0.0, 100.0, 100.0, 100.0], 0.5)
        assert out[0] == 0.0
        assert out[1] == 50.0
        assert out[2] == 75.0
        assert out[3] == 87.5

    def test_invalid_alpha_raises(self) -> None:
        with pytest.raises(MediaPostError):
            ema_smooth([1.0, 2.0], 0.0)
        with pytest.raises(MediaPostError):
            ema_smooth([1.0, 2.0], 1.5)


class TestBuildCropXExpression:
    def test_empty_returns_zero(self) -> None:
        assert build_crop_x_expression([]) == "0"

    def test_single_returns_value(self) -> None:
        assert build_crop_x_expression([(0.0, 200.0)]) == "200.0"

    def test_nested_if_correct_order(self) -> None:
        expr = build_crop_x_expression([(0.5, 100.0), (1.0, 200.0), (1.5, 300.0)])
        # Last value is the default (innermost), nested ifs from latest to earliest.
        assert expr.startswith("if(lt(t,0.500),")
        assert "if(lt(t,1.000),200.0,300.0)" in expr


class TestDownsampleToDepthCap:
    def test_below_cap_unchanged(self) -> None:
        traj = [(float(i), float(i * 10)) for i in range(50)]
        out, depth = _downsample_to_depth_cap(traj, 95)
        assert out == traj and depth == 50

    def test_above_cap_keeps_last(self) -> None:
        traj = [(float(i), float(i * 10)) for i in range(500)]
        out, depth = _downsample_to_depth_cap(traj, MAX_CROP_EXPR_DEPTH)
        assert depth <= MAX_CROP_EXPR_DEPTH
        assert out[-1] == traj[-1]

    def test_exact_cap(self) -> None:
        traj = [(float(i), float(i)) for i in range(MAX_CROP_EXPR_DEPTH)]
        out, depth = _downsample_to_depth_cap(traj, MAX_CROP_EXPR_DEPTH)
        assert depth == MAX_CROP_EXPR_DEPTH


class TestDetectXCenter:
    def test_none_returns_default(self) -> None:
        assert _detect_x_center(None, default=960.0) == 960.0

    def test_subject_not_detected(self) -> None:
        assert _detect_x_center({"subject_detected": False}, default=960.0) == 960.0

    def test_bbox_returns_center(self) -> None:
        det = {
            "subject_detected": True,
            "bounding_box": {"x": 100, "y": 50, "width": 200, "height": 150},
        }
        assert _detect_x_center(det, default=960.0) == 200.0

    def test_invalid_bbox_falls_back(self) -> None:
        det = {"subject_detected": True, "bounding_box": "broken"}
        assert _detect_x_center(det, default=500.0) == 500.0


class TestBuildTrajectory:
    def test_simple_smoothing(self) -> None:
        detections = [
            {
                "subject_detected": True,
                "bounding_box": {"x": 800, "y": 0, "width": 200, "height": 200},
            },
            {
                "subject_detected": True,
                "bounding_box": {"x": 1100, "y": 0, "width": 200, "height": 200},
            },
            {
                "subject_detected": True,
                "bounding_box": {"x": 1200, "y": 0, "width": 200, "height": 200},
            },
        ]
        traj = _build_trajectory(
            detections,
            fps=2.0,
            scene_cuts=[0.0, 10.0],
            orig_w=1920,
            crop_w=608,
            ema_alpha=0.5,
        )
        assert [round(t, 3) for t, _ in traj] == [0.0, 0.5, 1.0]
        assert traj[0][1] == pytest.approx(900.0 - 304.0)


# ---------------------------------------------------------------------------
# smart_recompose — full path with stubbed helpers
# ---------------------------------------------------------------------------


class _FakeVlm:
    def __init__(self, detections: list[dict[str, Any] | None]) -> None:
        self._d = detections

    async def call_vlm_concurrent(
        self,
        all_frames_b64: list[str],
        all_indices: list[int],
        prompt_template: str,
        prompt_kwargs_factory: Any,
        *,
        batch_size: int,
        concurrency: int,
    ) -> list[dict[str, Any] | None]:
        return list(self._d)


class TestSmartRecompose:
    def test_happy_path_calls_each_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        frame_dir = tmp_path / "frames_9_16"

        async def _fake_scene(video: Path, threshold: float) -> list[float]:
            return [0.0, 1.5, 3.0]

        async def _fake_extract(
            video: Path, out_dir: Path, fps: float, scale: str
        ) -> None:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "frame_00001.png").write_bytes(b"a")
            (out_dir / "frame_00002.png").write_bytes(b"b")
            (out_dir / "frame_00003.png").write_bytes(b"c")

        async def _fake_crop(*args: Any, **kwargs: Any) -> None:
            return None

        async def _fake_dur(video: Path) -> float:
            return 3.0

        monkeypatch.setattr("mediapost_recompose.detect_scene_cuts", _fake_scene)
        monkeypatch.setattr("mediapost_recompose.extract_frames", _fake_extract)
        monkeypatch.setattr("mediapost_recompose.run_ffmpeg_crop", _fake_crop)
        monkeypatch.setattr("mediapost_recompose.ffprobe_duration", _fake_dur)

        ctx = RecomposeContext(
            input_video=tmp_path / "in.mp4",
            orig_width=1920,
            orig_height=1080,
            target_aspect="9:16",
            output_video=tmp_path / "out.mp4",
        )
        client = _FakeVlm(
            [
                {
                    "subject_detected": True,
                    "bounding_box": {"x": 800, "y": 0, "width": 200, "height": 200},
                },
                None,
                {
                    "subject_detected": True,
                    "bounding_box": {"x": 900, "y": 0, "width": 200, "height": 200},
                },
            ]
        )
        result = _run(smart_recompose(ctx, client))
        assert result["crop_w"] == 608
        assert result["crop_h"] == 1080
        assert frame_dir.exists()
        assert isinstance(result["trajectory"], list)
        assert result["expr_depth"] >= 1

    def test_zero_frames_raises_dependency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_scene(video: Path, threshold: float) -> list[float]:
            return [0.0, 1.0]

        async def _fake_extract(
            video: Path, out_dir: Path, fps: float, scale: str
        ) -> None:
            out_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setattr("mediapost_recompose.detect_scene_cuts", _fake_scene)
        monkeypatch.setattr("mediapost_recompose.extract_frames", _fake_extract)

        ctx = RecomposeContext(
            input_video=tmp_path / "in.mp4",
            orig_width=1920,
            orig_height=1080,
            target_aspect="9:16",
            output_video=tmp_path / "out.mp4",
        )
        with pytest.raises(MediaPostError) as ei:
            _run(smart_recompose(ctx, _FakeVlm([])))
        assert ei.value.kind == "dependency"

    def test_invalid_orig_dims(self, tmp_path: Path) -> None:
        ctx = RecomposeContext(
            input_video=tmp_path / "in.mp4",
            orig_width=0,
            orig_height=0,
            target_aspect="9:16",
            output_video=tmp_path / "out.mp4",
        )
        with pytest.raises(MediaPostError) as ei:
            _run(smart_recompose(ctx, _FakeVlm([])))
        assert ei.value.kind == "format"
