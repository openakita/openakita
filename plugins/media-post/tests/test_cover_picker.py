"""Unit tests for ``mediapost_cover_picker``.

Coverage targets per ``docs/media-post-plan.md`` §6.3 + §11 Phase 3:

- happy path (3 candidates -> 2 finals after threshold).
- All-None VLM response raises ``MediaPostError("format")``.
- Below-threshold candidates dropped.
- Sort order: highest ``overall_score`` first.
- ``_safe_float`` / ``_safe_int`` coerce malformed VLM output.
- ffmpeg failure surfaces as ``MediaPostError("dependency")``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mediapost_cover_picker import (
    COVER_PICK_PROMPT,
    CoverPickContext,
    _rank_and_filter,
    _safe_float,
    _safe_int,
    pick_covers,
)
from mediapost_models import MediaPostError


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeVlmClient:
    """Returns scripted detections for ``call_vlm_concurrent``."""

    def __init__(self, detections: list[dict[str, Any] | None]) -> None:
        self._detections = detections
        self.calls: list[dict[str, Any]] = []

    async def call_vlm_concurrent(  # noqa: D401
        self,
        all_frames_b64: list[str],
        all_indices: list[int],
        prompt_template: str,
        prompt_kwargs_factory: Any,
        *,
        batch_size: int,
        concurrency: int,
    ) -> list[dict[str, Any] | None]:
        self.calls.append(
            {
                "n": len(all_frames_b64),
                "indices": list(all_indices),
                "batch_size": batch_size,
                "concurrency": concurrency,
                "kwargs": prompt_kwargs_factory(all_indices),
                "prompt_head": prompt_template.split("\n", 1)[0],
            }
        )
        return self._detections


def _make_files(tmp_path: Path, count: int) -> list[Path]:
    cand_dir = tmp_path / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(count):
        p = cand_dir / f"cand_{i:02d}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64)
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestSafeCoerce:
    def test_safe_float_handles_bad_values(self) -> None:
        assert _safe_float("3.5") == 3.5
        assert _safe_float(None, default=1.0) == 1.0
        assert _safe_float("nope", default=2.0) == 2.0

    def test_safe_int_handles_bad_values(self) -> None:
        assert _safe_int("4") == 4
        assert _safe_int(None) == 0
        assert _safe_int("oops", default=7) == 7


class TestRankAndFilter:
    def test_drops_none_and_below_threshold(self, tmp_path: Path) -> None:
        files = _make_files(tmp_path, 4)
        detections: list[dict[str, Any] | None] = [
            {"overall_score": 4.5, "best_for": "thumbnail"},
            None,
            {"overall_score": 2.0, "best_for": "thumbnail"},
            {"overall_score": 3.5, "best_for": "hero_image"},
        ]
        ranked = _rank_and_filter(
            files, detections, min_score_threshold=3.0, quantity=8
        )
        assert [det.get("overall_score") for _, det in ranked] == [4.5, 3.5]

    def test_quantity_caps_results(self, tmp_path: Path) -> None:
        files = _make_files(tmp_path, 3)
        detections: list[dict[str, Any] | None] = [
            {"overall_score": 5.0},
            {"overall_score": 4.0},
            {"overall_score": 3.5},
        ]
        ranked = _rank_and_filter(
            files, detections, min_score_threshold=0.0, quantity=2
        )
        assert len(ranked) == 2
        assert ranked[0][1]["overall_score"] == 5.0


# ---------------------------------------------------------------------------
# pick_covers — full path with stubbed extraction
# ---------------------------------------------------------------------------


class TestPickCovers:
    def test_happy_path_writes_finalists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        files = _make_files(tmp_path, 3)

        async def _fake_extract(*args: Any, **kwargs: Any) -> list[Path]:
            return files

        monkeypatch.setattr("mediapost_cover_picker._extract_candidates", _fake_extract)

        client = _FakeVlmClient(
            [
                {
                    "overall_score": 4.5,
                    "lighting": 4,
                    "composition": 5,
                    "subject_clarity": 4,
                    "visual_appeal": 5,
                    "text_safe_zone": 3,
                    "best_for": "thumbnail",
                    "reason": "great composition",
                    "main_subject_bbox": {"x": 100, "y": 50, "width": 200, "height": 150},
                },
                {"overall_score": 2.0},
                {
                    "overall_score": 3.7,
                    "lighting": 3,
                    "composition": 4,
                    "subject_clarity": 3,
                    "visual_appeal": 4,
                    "text_safe_zone": 4,
                    "best_for": "hero_image",
                    "reason": "lots of headroom",
                },
            ]
        )

        ctx = CoverPickContext(
            input_video=tmp_path / "fake.mp4",
            out_dir=tmp_path / "out",
            quantity=4,
            min_score_threshold=3.0,
            platform_hint="bilibili",
        )

        rows = _run(pick_covers(ctx, client))

        assert len(rows) == 2
        assert rows[0]["rank"] == 1 and rows[0]["overall_score"] == 4.5
        assert rows[1]["rank"] == 2 and rows[1]["overall_score"] == 3.7
        for row in rows:
            assert Path(row["cover_path"]).exists()

        assert client.calls and client.calls[0]["kwargs"]["platform"] == "bilibili"
        assert client.calls[0]["batch_size"] == ctx.vlm_batch_size

    def test_all_none_detections_raises_format(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = _make_files(tmp_path, 2)

        async def _fake_extract(*a: Any, **kw: Any) -> list[Path]:
            return files

        monkeypatch.setattr("mediapost_cover_picker._extract_candidates", _fake_extract)

        client = _FakeVlmClient([None, None])
        ctx = CoverPickContext(
            input_video=tmp_path / "fake.mp4",
            out_dir=tmp_path / "out2",
            quantity=4,
            min_score_threshold=3.0,
        )
        with pytest.raises(MediaPostError) as ei:
            _run(pick_covers(ctx, client))
        assert ei.value.kind == "format"

    def test_zero_candidates_raises_dependency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fake_extract(*a: Any, **kw: Any) -> list[Path]:
            return []

        monkeypatch.setattr("mediapost_cover_picker._extract_candidates", _fake_extract)

        ctx = CoverPickContext(
            input_video=tmp_path / "fake.mp4",
            out_dir=tmp_path / "out3",
        )
        with pytest.raises(MediaPostError) as ei:
            _run(pick_covers(ctx, _FakeVlmClient([])))
        assert ei.value.kind == "dependency"

    def test_progress_callback_invoked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = _make_files(tmp_path, 1)

        async def _fake_extract(*a: Any, **kw: Any) -> list[Path]:
            return files

        monkeypatch.setattr("mediapost_cover_picker._extract_candidates", _fake_extract)
        client = _FakeVlmClient([{"overall_score": 5.0}])

        seen: list[tuple[float, str]] = []

        def _cb(progress: float, label: str) -> None:
            seen.append((progress, label))

        ctx = CoverPickContext(
            input_video=tmp_path / "fake.mp4",
            out_dir=tmp_path / "out4",
            quantity=1,
            min_score_threshold=0.0,
        )
        _run(pick_covers(ctx, client, progress_cb=_cb))
        assert seen and seen[-1][0] == 1.0


class TestPromptShape:
    def test_prompt_includes_required_fields(self) -> None:
        rendered = COVER_PICK_PROMPT.format(
            frame_count=8, frame_indices=[0, 1, 2, 3, 4, 5, 6, 7], platform="tiktok"
        )
        assert "tiktok" in rendered
        assert "frame_idx" in rendered
        assert "overall_score" in rendered
