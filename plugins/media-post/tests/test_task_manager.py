"""Phase 1 — ``mediapost_task_manager`` unit tests (Gate 1 per §11 Phase 1).

Coverage focus per §10.1 ``test_task_manager.py`` row:

- 6-table schema is created on ``init`` (tasks, cover_results,
  recompose_outputs, seo_results, chapter_cards_results, assets_bus).
- ``_UPDATABLE_COLUMNS["assets_bus"]`` is empty in v1.0 — write attempts
  must raise ``ValueError`` so v2.0 can opt in by adding columns there.
- ``assets_bus`` row count is 0 after every test (the §3.3 invariant
  protected by ``test_assets_bus_count_zero``).
- Cancel flag round-trip.
- Task CRUD + JSON column round-trip + per-mode result writers.

Style mirrors ``plugins/clip-sense/tests/test_task_manager.py`` — sync
tests with an ``asyncio.run_until_complete`` helper to avoid adding a
``pytest-asyncio`` dependency (red-line §13 #1: no new dev deps).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest
from mediapost_task_manager import (
    _UPDATABLE_COLUMNS,
    DEFAULT_CONFIG,
    MediaPostTaskManager,
)


def _run(coro):
    """Run an async coroutine in the current event loop (sync test helper)."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "media_post.sqlite"


@pytest.fixture()
def tm(db_path: Path) -> MediaPostTaskManager:
    manager = MediaPostTaskManager(db_path)
    _run(manager.init())
    try:
        yield manager
    finally:
        _run(manager.close())


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


class TestSchema:
    @pytest.mark.parametrize(
        "table",
        [
            "tasks",
            "cover_results",
            "recompose_outputs",
            "seo_results",
            "chapter_cards_results",
            "assets_bus",
            "config",
        ],
    )
    def test_table_exists(self, tm: MediaPostTaskManager, table: str) -> None:
        assert tm._db is not None

        async def _check():
            cur = await tm._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            return await cur.fetchone()

        row = _run(_check())
        assert row is not None, f"table {table!r} missing"

    def test_tasks_has_origin_columns(self, tm: MediaPostTaskManager) -> None:
        # Forward-compat: §3.3 says these columns must exist in v1.0 but
        # always be NULL until v2.0 cross-plugin Handoff lands.
        async def _columns():
            cur = await tm._db.execute("PRAGMA table_info(tasks)")
            rows = await cur.fetchall()
            return {r["name"] for r in rows}

        cols = _run(_columns())
        assert "origin_plugin_id" in cols
        assert "origin_task_id" in cols

    def test_assets_bus_has_v2_required_columns(
        self, tm: MediaPostTaskManager
    ) -> None:
        # The §8 schema reservation is checked here so v2.0 doesn't have to
        # add columns + run a migration.
        async def _columns():
            cur = await tm._db.execute("PRAGMA table_info(assets_bus)")
            rows = await cur.fetchall()
            return {r["name"] for r in rows}

        cols = _run(_columns())
        for required in (
            "asset_id",
            "origin_plugin_id",
            "origin_task_id",
            "asset_kind",
            "asset_uri",
            "meta_json",
            "created_at",
        ):
            assert required in cols, f"assets_bus column {required!r} missing"

    def test_assets_bus_count_zero_after_init(
        self, tm: MediaPostTaskManager
    ) -> None:
        # Red-line §3.3: pipeline never writes to assets_bus in v1.0.
        assert _run(tm.assets_bus_count()) == 0

    def test_seeded_config_has_defaults(self, tm: MediaPostTaskManager) -> None:
        cfg = _run(tm.get_all_config())
        for key in DEFAULT_CONFIG:
            assert key in cfg

    def test_assets_bus_whitelist_empty_in_v1(self) -> None:
        # The whitelist for assets_bus is intentionally empty in v1.0 so any
        # write attempt would raise — making future v2.0 code opt in
        # explicitly via _UPDATABLE_COLUMNS rather than silently succeed.
        assert _UPDATABLE_COLUMNS["assets_bus"] == frozenset()


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


class TestConfig:
    def test_set_and_get_config(self, tm: MediaPostTaskManager) -> None:
        _run(tm.set_config("vlm_concurrency", "2"))
        assert _run(tm.get_config("vlm_concurrency")) == "2"

    def test_set_configs_bulk(self, tm: MediaPostTaskManager) -> None:
        _run(tm.set_configs({"recompose_fps": "1.0", "ema_alpha": "0.3"}))
        cfg = _run(tm.get_all_config())
        assert cfg["recompose_fps"] == "1.0"
        assert cfg["ema_alpha"] == "0.3"

    def test_unknown_config_returns_none(self, tm: MediaPostTaskManager) -> None:
        assert _run(tm.get_config("does-not-exist")) is None


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------


class TestTaskCrud:
    def test_create_get_task(self, tm: MediaPostTaskManager) -> None:
        task = _run(
            tm.create_task(
                mode="cover_pick",
                video_path="/tmp/x.mp4",
                params={"quantity": 8, "platform_hint": "tiktok"},
                cost_estimated=0.32,
                cost_kind="ok",
            )
        )
        assert task["mode"] == "cover_pick"
        assert task["cost_kind"] == "ok"
        assert task["params"] == {"quantity": 8, "platform_hint": "tiktok"}
        # origin_* must default to NULL per §3.3
        assert task["origin_plugin_id"] is None
        assert task["origin_task_id"] is None
        assert len(task["id"]) == 12

        again = _run(tm.get_task(task["id"]))
        assert again is not None
        assert again["id"] == task["id"]

    def test_update_task_with_aliased_json_columns(
        self, tm: MediaPostTaskManager
    ) -> None:
        task = _run(tm.create_task(mode="seo_pack"))
        _run(
            tm.update_task(
                task["id"],
                status="completed",
                video_meta={"duration_sec": 30.0, "width": 1920, "height": 1080},
                result_summary={"platforms": 5, "total_chars": 1234},
                cost_actual=0.025,
            )
        )
        refreshed = _run(tm.get_task(task["id"]))
        assert refreshed is not None
        assert refreshed["status"] == "completed"
        assert refreshed["video_meta"]["width"] == 1920
        assert refreshed["result_summary"]["platforms"] == 5
        assert refreshed["cost_actual"] == 0.025

    def test_update_task_rejects_unknown_column(
        self, tm: MediaPostTaskManager
    ) -> None:
        task = _run(tm.create_task(mode="cover_pick"))
        with pytest.raises(ValueError, match="not whitelisted"):
            _run(tm.update_task(task["id"], some_random_col="oops"))

    def test_update_task_safe_swallows_errors(
        self, tm: MediaPostTaskManager
    ) -> None:
        task = _run(tm.create_task(mode="cover_pick"))
        # Must NOT raise even with an invalid column key.
        _run(tm.update_task_safe(task["id"], some_random_col="oops"))
        # Mixed valid + invalid: safe variant treats whole call as best
        # effort and skips silently rather than committing partial state.
        _run(tm.update_task_safe(task["id"], status="failed", bogus_key="x"))

    def test_list_tasks_with_filters(self, tm: MediaPostTaskManager) -> None:
        _run(tm.create_task(mode="cover_pick"))
        _run(tm.create_task(mode="multi_aspect"))
        _run(tm.create_task(mode="seo_pack"))
        all_tasks = _run(tm.list_tasks())
        assert all_tasks["total"] == 3

        only_seo = _run(tm.list_tasks(mode="seo_pack"))
        assert only_seo["total"] == 1
        assert only_seo["tasks"][0]["mode"] == "seo_pack"

    def test_get_running_tasks_filters_status(
        self, tm: MediaPostTaskManager
    ) -> None:
        a = _run(tm.create_task(mode="cover_pick"))
        b = _run(tm.create_task(mode="cover_pick", status="running"))
        _run(tm.create_task(mode="cover_pick", status="completed"))
        running = _run(tm.get_running_tasks())
        ids = {t["id"] for t in running}
        assert a["id"] in ids and b["id"] in ids
        assert len(running) == 2

    def test_delete_task_cascades_results(
        self, tm: MediaPostTaskManager
    ) -> None:
        task = _run(tm.create_task(mode="cover_pick"))
        _run(
            tm.insert_cover_result(
                task_id=task["id"], rank=1, cover_path="/x.png"
            )
        )
        assert _run(tm.delete_task(task["id"])) is True
        results = _run(tm.list_cover_results(task["id"]))
        assert results == []

    def test_cancel_flag_round_trip(self, tm: MediaPostTaskManager) -> None:
        tm.request_cancel("task-1")
        assert tm.is_canceled("task-1") is True
        tm.clear_cancel("task-1")
        assert tm.is_canceled("task-1") is False


# ---------------------------------------------------------------------------
# Per-mode result writers
# ---------------------------------------------------------------------------


class TestPerModeResults:
    def test_cover_results_round_trip(self, tm: MediaPostTaskManager) -> None:
        task = _run(tm.create_task(mode="cover_pick"))
        _run(
            tm.insert_cover_result(
                task_id=task["id"],
                rank=1,
                cover_path="/x/cover_01.png",
                overall_score=4.2,
                lighting=4.0,
                composition=4.5,
                subject_clarity=4.0,
                visual_appeal=4.2,
                text_safe_zone=3.8,
                main_subject_bbox={
                    "x": 100,
                    "y": 200,
                    "width": 300,
                    "height": 400,
                },
                best_for="thumbnail",
                reason="warm light, centered subject",
            )
        )
        results = _run(tm.list_cover_results(task["id"]))
        assert len(results) == 1
        assert results[0]["main_subject_bbox"] == {
            "x": 100,
            "y": 200,
            "width": 300,
            "height": 400,
        }
        assert results[0]["best_for"] == "thumbnail"

    def test_recompose_outputs_serialise_trajectory(
        self, tm: MediaPostTaskManager
    ) -> None:
        task = _run(tm.create_task(mode="multi_aspect"))
        trajectory = [(0.0, 540.0), (0.5, 545.2), (1.0, 555.7)]
        _run(
            tm.insert_recompose_output(
                task_id=task["id"],
                aspect="9:16",
                output_path="/x/out_vertical.mp4",
                output_w=608,
                output_h=1080,
                duration_sec=30.0,
                trajectory=trajectory,
                ema_alpha_used=0.15,
                fps_used=2.0,
                scene_cut_count=4,
                fallback_letterbox_used=False,
            )
        )
        rows = _run(tm.list_recompose_outputs(task["id"]))
        assert len(rows) == 1
        assert rows[0]["aspect"] == "9:16"
        assert rows[0]["fallback_letterbox_used"] is False
        # JSON round-trips as plain lists, not tuples.
        assert rows[0]["trajectory"] == [list(t) for t in trajectory]

    def test_seo_results_round_trip(self, tm: MediaPostTaskManager) -> None:
        task = _run(tm.create_task(mode="seo_pack"))
        for plat in ("tiktok", "bilibili"):
            _run(
                tm.insert_seo_result(
                    task_id=task["id"],
                    platform=plat,
                    payload={"title": f"hello-{plat}", "tags": [plat]},
                    tokens_used=240,
                )
            )
        rows = _run(tm.list_seo_results(task["id"]))
        assert {r["platform"] for r in rows} == {"tiktok", "bilibili"}
        for r in rows:
            assert r["payload"]["title"].startswith("hello-")

    def test_chapter_card_results_round_trip(
        self, tm: MediaPostTaskManager
    ) -> None:
        task = _run(tm.create_task(mode="chapter_cards"))
        for i, title in enumerate(["intro", "body", "outro"]):
            _run(
                tm.insert_chapter_card_result(
                    task_id=task["id"],
                    chapter_index=i,
                    title=title,
                    template_id="modern",
                    png_path=f"/x/chapter_{i:02d}.png",
                    width=1280,
                    height=720,
                    render_path="playwright",
                )
            )
        rows = _run(tm.list_chapter_card_results(task["id"]))
        assert [r["chapter_index"] for r in rows] == [0, 1, 2]
        assert rows[1]["title"] == "body"


# ---------------------------------------------------------------------------
# assets_bus invariants — v1.0 must never write a row.
# ---------------------------------------------------------------------------


class TestAssetsBusInvariants:
    def test_assets_bus_count_remains_zero_after_workflow(
        self, tm: MediaPostTaskManager
    ) -> None:
        # Run a representative end-to-end CRUD across all 4 modes and
        # confirm assets_bus stays empty (no accidental write paths).
        for mode in ("cover_pick", "multi_aspect", "seo_pack", "chapter_cards"):
            task = _run(tm.create_task(mode=mode))
            _run(tm.update_task(task["id"], status="completed"))
        assert _run(tm.assets_bus_count()) == 0

    def test_get_asset_returns_none_for_missing_id(
        self, tm: MediaPostTaskManager
    ) -> None:
        assert _run(tm.get_asset("nope")) is None


# ---------------------------------------------------------------------------
# 9-kind error_kind round-trip
# ---------------------------------------------------------------------------


class TestErrorKindPersistence:
    @pytest.mark.parametrize(
        "kind",
        [
            "network",
            "timeout",
            "auth",
            "quota",
            "moderation",
            "dependency",
            "format",
            "duration",
            "unknown",
        ],
    )
    def test_each_canonical_kind_round_trips_through_tasks_table(
        self, tm: MediaPostTaskManager, kind: str
    ) -> None:
        task = _run(tm.create_task(mode="cover_pick"))
        _run(
            tm.update_task(
                task["id"],
                status="failed",
                error_kind=kind,
                error_message=f"sample for {kind}",
                error_hints={"zh": ["x"], "en": ["y"]},
            )
        )
        refreshed = _run(tm.get_task(task["id"]))
        assert refreshed is not None
        assert refreshed["error_kind"] == kind
        assert refreshed["error_hints"] == {"zh": ["x"], "en": ["y"]}


def test_aiosqlite_module_is_loaded() -> None:
    # Smoke: aiosqlite is available; not used at runtime here but imported
    # by the task manager so confirm the import surface stays in pyproject.
    assert aiosqlite.__name__ == "aiosqlite"
