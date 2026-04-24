"""Unit tests for ``mediapost_pipeline``.

Coverage targets per ``docs/media-post-plan.md`` §6.6 + §11 Phase 3:

- ``run_pipeline`` runs the full step list for each of the 4 modes.
- ``MediaPostError`` from a step is translated to ``status=failed`` +
  ``error_kind`` written to the task row.
- Unknown exceptions become ``error_kind=unknown``.
- Cancellation between steps marks the task ``cancelled``.
- Cost estimation > warn threshold without ``cost_approved`` short-
  circuits to ``approval_required``.
- Mode-specific dispatch maps to the right execute step.
- ``broadcast_ui_event`` is invoked for every status transition.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mediapost_models import COST_THRESHOLD_WARN_CNY, MediaPostError
from mediapost_pipeline import MediaPostContext, run_pipeline
from mediapost_task_manager import MediaPostTaskManager


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeApi:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def broadcast_ui_event(self, event_type: str, data: dict[str, Any], **kwargs: Any) -> None:
        self.events.append((event_type, data))


class _FakeVlmClient:
    """Mode-specific stub. Returns one preset detection / response."""

    def __init__(self, vlm_response: list[Any] | None = None, qwen_text: str = "{}") -> None:
        self._vlm = vlm_response or [None]
        self._qwen = qwen_text

    async def call_vlm_concurrent(self, *a: Any, **kw: Any) -> list[Any]:
        return list(self._vlm)

    async def qwen_plus_call(self, **kwargs: Any) -> str:
        return self._qwen


def _build_tm(tmp_path: Path) -> MediaPostTaskManager:
    tm = MediaPostTaskManager(tmp_path / "media_post.sqlite")
    _run(tm.init())
    return tm


def _make_ctx(
    *,
    tmp_path: Path,
    mode: str,
    params: dict[str, Any] | None = None,
    vlm_client: Any | None = None,
) -> tuple[MediaPostContext, MediaPostTaskManager, _FakeApi]:
    tm = _build_tm(tmp_path)
    api = _FakeApi()
    task = _run(tm.create_task(mode=mode, params=params or {}))
    ctx = MediaPostContext(
        task_id=task["id"],
        mode=mode,
        params=params or {},
        task_dir=tmp_path / "task" / task["id"],
        api=api,
        tm=tm,
        vlm_client=vlm_client or _FakeVlmClient(),
    )
    return ctx, tm, api


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------


class TestModeDispatch:
    def test_unknown_mode_marks_failed(self, tmp_path: Path) -> None:
        ctx, tm, api = _make_ctx(tmp_path=tmp_path, mode="cover_pick")
        ctx.mode = "made_up"
        _run(run_pipeline(ctx))
        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["status"] == "failed"
        assert task["error_kind"] == "format"
        _run(tm.close())


# ---------------------------------------------------------------------------
# Cover pick happy path
# ---------------------------------------------------------------------------


class TestCoverPick:
    def test_happy_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Stub the cover_picker so we don't need ffmpeg.
        async def _fake_pick(ctx: Any, vlm_client: Any, *, progress_cb: Any = None) -> list[dict[str, Any]]:
            return [
                {
                    "rank": 1,
                    "cover_path": str(tmp_path / "c1.png"),
                    "thumbnail_path": str(tmp_path / "c1.png"),
                    "overall_score": 4.5,
                    "lighting": 5,
                    "composition": 4,
                    "subject_clarity": 5,
                    "visual_appeal": 5,
                    "text_safe_zone": 3,
                    "main_subject_bbox": None,
                    "best_for": "thumbnail",
                    "reason": "good",
                },
            ]

        monkeypatch.setattr("mediapost_pipeline.pick_covers", _fake_pick)

        params = {"quantity": 8, "min_score_threshold": 3.0, "duration_sec": 10}
        ctx, tm, api = _make_ctx(tmp_path=tmp_path, mode="cover_pick", params=params)
        ctx.video_path = tmp_path / "fake.mp4"
        ctx.video_path.write_bytes(b"fake")

        _run(run_pipeline(ctx))

        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["status"] == "completed"
        cover_rows = _run(tm.list_cover_results(ctx.task_id))
        assert len(cover_rows) == 1
        # broadcast events: at least running + completed + at least one progress.
        statuses = [data.get("status") for _, data in api.events]
        assert "completed" in statuses
        _run(tm.close())


# ---------------------------------------------------------------------------
# multi_aspect uses ffprobe + smart_recompose stubs
# ---------------------------------------------------------------------------


class TestMultiAspect:
    def test_happy_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_smart(ctx: Any, vlm_client: Any, *, progress_cb: Any = None) -> dict[str, Any]:
            return {
                "trajectory": [{"t": 0.0, "x_left": 100.0}],
                "scene_cuts": [0.0, 1.0],
                "expr_depth": 1,
                "crop_w": 608,
                "crop_h": 1080,
                "fallback_letterbox_used": True,
            }

        async def _fake_dur(video: Path) -> float:
            return 5.0

        monkeypatch.setattr("mediapost_pipeline.smart_recompose", _fake_smart)
        monkeypatch.setattr("mediapost_pipeline.ffprobe_duration", _fake_dur)

        params = {
            "target_aspects": ["9:16", "1:1"],
            "orig_width": 1920,
            "orig_height": 1080,
            "duration_sec": 5,
        }
        ctx, tm, api = _make_ctx(tmp_path=tmp_path, mode="multi_aspect", params=params)
        ctx.video_path = tmp_path / "fake.mp4"
        ctx.video_path.write_bytes(b"fake")
        ctx.video_meta = {"width": 1920, "height": 1080}

        _run(run_pipeline(ctx))

        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["status"] == "completed"
        rec_rows = _run(tm.list_recompose_outputs(ctx.task_id))
        assert {r["aspect"] for r in rec_rows} == {"9:16", "1:1"}
        _run(tm.close())


# ---------------------------------------------------------------------------
# seo_pack
# ---------------------------------------------------------------------------


class TestSeoPack:
    def test_happy_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_pack(**kwargs: Any) -> dict[str, dict[str, Any] | None]:
            return {p: {"title": p} for p in kwargs["platforms"]}

        monkeypatch.setattr("mediapost_pipeline.generate_seo_pack", _fake_pack)

        params = {
            "platforms": ["tiktok", "youtube"],
            "subtitle_excerpt": "some text",
            "instruction": "summarize",
            "video_title_hint": "a video",
        }
        ctx, tm, api = _make_ctx(
            tmp_path=tmp_path,
            mode="seo_pack",
            params=params,
            vlm_client=_FakeVlmClient(qwen_text='{"title":"ok"}'),
        )
        _run(run_pipeline(ctx))
        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["status"] == "completed"
        seo_rows = _run(tm.list_seo_results(ctx.task_id))
        assert len(seo_rows) == 2
        _run(tm.close())

    def test_all_platforms_fail(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_pack(**kwargs: Any) -> dict[str, dict[str, Any] | None]:
            return dict.fromkeys(kwargs["platforms"])

        monkeypatch.setattr("mediapost_pipeline.generate_seo_pack", _fake_pack)

        ctx, tm, api = _make_ctx(
            tmp_path=tmp_path,
            mode="seo_pack",
            params={"platforms": ["tiktok"], "subtitle_excerpt": "x", "instruction": "y"},
        )
        _run(run_pipeline(ctx))
        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["status"] == "failed"
        assert task["error_kind"] == "format"
        _run(tm.close())


# ---------------------------------------------------------------------------
# chapter_cards
# ---------------------------------------------------------------------------


class TestChapterCards:
    def test_happy_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_render(ctx: Any, *, progress_cb: Any = None) -> list[dict[str, Any]]:
            return [
                {
                    "chapter_index": 1,
                    "title": "Intro",
                    "subtitle": "",
                    "template_id": "modern",
                    "png_path": str(tmp_path / "ch1.png"),
                    "width": 1280,
                    "height": 720,
                    "render_path": "drawtext",
                    "extra_meta": {},
                },
            ]

        monkeypatch.setattr("mediapost_pipeline.render_chapter_cards", _fake_render)
        params = {"chapters": [{"chapter_index": 1, "title": "Intro"}], "template_id": "modern"}
        ctx, tm, api = _make_ctx(tmp_path=tmp_path, mode="chapter_cards", params=params)
        _run(run_pipeline(ctx))
        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["status"] == "completed"
        rows = _run(tm.list_chapter_card_results(ctx.task_id))
        assert len(rows) == 1
        _run(tm.close())

    def test_empty_chapters_fails(self, tmp_path: Path) -> None:
        ctx, tm, api = _make_ctx(
            tmp_path=tmp_path, mode="chapter_cards", params={"chapters": []}
        )
        _run(run_pipeline(ctx))
        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["status"] == "failed"
        assert task["error_kind"] == "format"
        _run(tm.close())


# ---------------------------------------------------------------------------
# Error handling + cancellation + approval
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_media_post_error_translates_to_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _failing_pick(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise MediaPostError("dependency", "ffmpeg gone")

        monkeypatch.setattr("mediapost_pipeline.pick_covers", _failing_pick)

        ctx, tm, api = _make_ctx(tmp_path=tmp_path, mode="cover_pick", params={"duration_sec": 1})
        ctx.video_path = tmp_path / "fake.mp4"
        ctx.video_path.write_bytes(b"x")
        _run(run_pipeline(ctx))
        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["status"] == "failed"
        assert task["error_kind"] == "dependency"
        assert task["error_message"] and "ffmpeg gone" in task["error_message"]
        _run(tm.close())

    def test_unknown_exception_becomes_unknown_kind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _crash(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            raise RuntimeError("boom")

        monkeypatch.setattr("mediapost_pipeline.pick_covers", _crash)
        ctx, tm, api = _make_ctx(tmp_path=tmp_path, mode="cover_pick", params={"duration_sec": 1})
        ctx.video_path = tmp_path / "fake.mp4"
        ctx.video_path.write_bytes(b"x")
        _run(run_pipeline(ctx))
        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["error_kind"] == "unknown"
        _run(tm.close())

    def test_cancellation_marks_cancelled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx, tm, api = _make_ctx(tmp_path=tmp_path, mode="cover_pick", params={"duration_sec": 1})
        tm.request_cancel(ctx.task_id)
        _run(run_pipeline(ctx))
        task = _run(tm.get_task(ctx.task_id))
        assert task is not None and task["status"] == "cancelled"
        _run(tm.close())


class TestApprovalRequired:
    def test_high_cost_short_circuits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # multi_aspect with a long video → cost above warn threshold.
        async def _fake_dur(video: Path) -> float:
            return 60 * 60.0  # 1 hour

        monkeypatch.setattr("mediapost_pipeline.ffprobe_duration", _fake_dur)
        params = {
            "target_aspects": ["9:16"],
            "orig_width": 1920,
            "orig_height": 1080,
        }
        ctx, tm, api = _make_ctx(tmp_path=tmp_path, mode="multi_aspect", params=params)
        ctx.video_path = tmp_path / "fake.mp4"
        ctx.video_path.write_bytes(b"x")
        _run(run_pipeline(ctx))
        task = _run(tm.get_task(ctx.task_id))
        assert task is not None
        assert task["status"] == "approval_required"
        assert task["cost_estimated"] >= COST_THRESHOLD_WARN_CNY
        # Make sure the broadcast carried the approval status.
        statuses = [data.get("status") for _, data in api.events]
        assert "approval_required" in statuses
        _run(tm.close())


# ---------------------------------------------------------------------------
# Result summary + metadata.json
# ---------------------------------------------------------------------------


class TestSummary:
    def test_metadata_written(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_pick(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "rank": 1,
                    "cover_path": "/tmp/c1.png",
                    "thumbnail_path": "/tmp/c1.png",
                    "overall_score": 4.0,
                    "lighting": 4,
                    "composition": 4,
                    "subject_clarity": 4,
                    "visual_appeal": 4,
                    "text_safe_zone": 4,
                    "main_subject_bbox": None,
                    "best_for": "thumbnail",
                    "reason": "ok",
                },
            ]

        monkeypatch.setattr("mediapost_pipeline.pick_covers", _fake_pick)
        ctx, tm, api = _make_ctx(tmp_path=tmp_path, mode="cover_pick", params={"duration_sec": 1})
        ctx.video_path = tmp_path / "fake.mp4"
        ctx.video_path.write_bytes(b"x")
        _run(run_pipeline(ctx))
        meta_path = ctx.task_dir / "metadata.json"
        assert meta_path.exists()
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        assert data["mode"] == "cover_pick"
        assert data["cover_count"] == 1
        _run(tm.close())
