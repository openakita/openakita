"""Phase 1 unit tests for ``footage_gate_task_manager``.

The suite covers:

- Schema bring-up (3 tables created, 16 default config rows seeded).
- Tasks CRUD happy paths for all four modes.
- ``update_task_safe`` whitelist enforcement (ValueError on bogus key,
  JSON encoding of ``params`` / ``thumbs`` / ``error_hints``).
- ``cleanup_expired`` only deletes COMPLETED tasks past the retention
  cutoff (running tasks are preserved even past retention).
- The v1.0 red-line: ``assets_bus`` table EXISTS but is NEVER written
  by the task manager. Every CRUD path is followed by a ``count == 0``
  assertion.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from footage_gate_task_manager import (
    DEFAULT_CONFIG,
    FootageGateTaskManager,
)

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
async def tm(tmp_path: Path) -> FootageGateTaskManager:
    mgr = FootageGateTaskManager(tmp_path / "fg.db")
    await mgr.init()
    try:
        yield mgr
    finally:
        await mgr.close()


# ── Schema bring-up ──────────────────────────────────────────────────────


async def test_init_creates_three_tables(tmp_path: Path) -> None:
    mgr = FootageGateTaskManager(tmp_path / "x.db")
    await mgr.init()
    try:
        assert mgr._db is not None
        rows = await mgr._db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        names = {r[0] for r in rows}
        assert {"tasks", "config", "assets_bus"} <= names
    finally:
        await mgr.close()


async def test_default_config_seeded(tm: FootageGateTaskManager) -> None:
    cfg = await tm.get_all_config()
    for key, expected in DEFAULT_CONFIG.items():
        assert cfg.get(key) == expected, f"{key} seed mismatch"


async def test_set_config_round_trips(tm: FootageGateTaskManager) -> None:
    await tm.set_config("transcribe_api_key", "sk-foo")
    assert await tm.get_config("transcribe_api_key") == "sk-foo"


async def test_set_configs_batch_update(tm: FootageGateTaskManager) -> None:
    await tm.set_configs(
        {
            "silence_threshold_db": "-50",
            "cut_qc_max_attempts": "5",
        }
    )
    cfg = await tm.get_all_config()
    assert cfg["silence_threshold_db"] == "-50"
    assert cfg["cut_qc_max_attempts"] == "5"
    # Untouched keys still hold their defaults.
    assert cfg["silence_pad"] == DEFAULT_CONFIG["silence_pad"]


# ── Tasks CRUD ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["source_review", "silence_cut", "auto_color", "cut_qc"])
async def test_create_task_for_each_mode(tm: FootageGateTaskManager, mode: str) -> None:
    t = await tm.create_task(
        mode=mode,
        input_path="/fake/in.mp4",
        params={"flag": True, "n": 3},
    )
    assert t["mode"] == mode
    assert t["status"] == "pending"
    assert t["params"] == {"flag": True, "n": 3}
    assert t["is_hdr_source"] is False
    assert t["thumbs"] == []
    assert t["error_hints"] == []
    # v1.0 handoff red-line.
    assert t["origin_plugin_id"] is None
    assert t["origin_task_id"] is None
    assert t["asset_id"] is None
    # assets_bus untouched.
    assert await tm.count_assets_bus() == 0


async def test_get_task_returns_none_for_unknown_id(
    tm: FootageGateTaskManager,
) -> None:
    assert await tm.get_task("does-not-exist") is None


async def test_list_tasks_filters_by_mode_and_status(
    tm: FootageGateTaskManager,
) -> None:
    await tm.create_task(mode="silence_cut", input_path="/a.mp4")
    await tm.create_task(mode="silence_cut", input_path="/b.mp4")
    auto = await tm.create_task(mode="auto_color", input_path="/c.mp4")
    await tm.update_task_safe(auto["id"], status="done")

    sc, total_sc = await tm.list_tasks(mode="silence_cut")
    assert total_sc == 2 and len(sc) == 2

    done, total_done = await tm.list_tasks(status="done")
    assert total_done == 1 and done[0]["id"] == auto["id"]


# ── update_task_safe — whitelist hardening ───────────────────────────────


async def test_update_task_safe_rejects_unknown_column(
    tm: FootageGateTaskManager,
) -> None:
    t = await tm.create_task(mode="auto_color", input_path="/x.mp4")
    with pytest.raises(ValueError, match="not whitelisted"):
        await tm.update_task_safe(t["id"], evil_column="DROP TABLE tasks")


async def test_update_task_safe_json_encodes_params_thumbs_hints(
    tm: FootageGateTaskManager,
) -> None:
    t = await tm.create_task(mode="cut_qc", input_path="/y.mp4")
    await tm.update_task_safe(
        t["id"],
        params={"max_attempts": 3, "auto_remux": True},
        thumbs=["thumb_001.jpg", "thumb_002.jpg"],
        error_hints=["请检查输入"],
        status="failed",
        error_kind="dependency",
    )
    refreshed = await tm.get_task(t["id"])
    assert refreshed is not None
    assert refreshed["params"] == {"max_attempts": 3, "auto_remux": True}
    assert refreshed["thumbs"] == ["thumb_001.jpg", "thumb_002.jpg"]
    assert refreshed["error_hints"] == ["请检查输入"]
    assert refreshed["status"] == "failed"
    assert refreshed["error_kind"] == "dependency"


async def test_update_task_safe_returns_false_when_no_updates(
    tm: FootageGateTaskManager,
) -> None:
    t = await tm.create_task(mode="auto_color", input_path="/z.mp4")
    assert await tm.update_task_safe(t["id"]) is False


async def test_update_task_safe_normalises_is_hdr_source(
    tm: FootageGateTaskManager,
) -> None:
    """Stored as 0/1 INTEGER but exposed as bool — every truthy value
    must round-trip to True."""
    t = await tm.create_task(mode="auto_color", input_path="/h.mp4")
    await tm.update_task_safe(t["id"], is_hdr_source=True)
    assert (await tm.get_task(t["id"]))["is_hdr_source"] is True
    await tm.update_task_safe(t["id"], is_hdr_source=False)
    assert (await tm.get_task(t["id"]))["is_hdr_source"] is False


# ── delete_task / cleanup_expired ────────────────────────────────────────


async def test_delete_task(tm: FootageGateTaskManager) -> None:
    t = await tm.create_task(mode="source_review", input_path="/d.mp4")
    assert await tm.delete_task(t["id"]) is True
    assert await tm.delete_task(t["id"]) is False  # idempotent


async def test_cleanup_expired_only_removes_completed_past_retention(
    tm: FootageGateTaskManager,
) -> None:
    fresh = await tm.create_task(mode="silence_cut", input_path="/f.mp4")
    stale_done = await tm.create_task(mode="silence_cut", input_path="/s.mp4")
    stale_running = await tm.create_task(mode="silence_cut", input_path="/r.mp4")

    long_ago = time.time() - (60 * 86400)
    # Stale done — should be deleted by a 30-day sweep.
    await tm.update_task_safe(stale_done["id"], status="done", completed_at=long_ago)
    # Stale but still running — completed_at is NULL → preserved.
    await tm.update_task_safe(stale_running["id"], status="running")

    deleted = await tm.cleanup_expired(retention_days=30)
    assert deleted == 1

    survivors = {t["id"] for (t,) in [(t,) for t in (await tm.list_tasks())[0]]}
    assert fresh["id"] in survivors
    assert stale_running["id"] in survivors
    assert stale_done["id"] not in survivors


async def test_cleanup_expired_zero_days_is_noop(
    tm: FootageGateTaskManager,
) -> None:
    t = await tm.create_task(mode="auto_color", input_path="/n.mp4")
    await tm.update_task_safe(t["id"], status="done", completed_at=time.time() - 999999)
    deleted = await tm.cleanup_expired(retention_days=0)
    assert deleted == 0
    assert await tm.get_task(t["id"]) is not None


# ── assets_bus red-line: NEVER written in v1.0 ───────────────────────────


async def test_assets_bus_is_empty_after_full_lifecycle(
    tm: FootageGateTaskManager,
) -> None:
    """Hard contract: any v1.0 path that secretly INSERTs into assets_bus
    breaks the v2.0 cross-plugin handoff design (subtitle-craft → us →
    media-post). We exercise the full task lifecycle and verify the table
    stays empty throughout.
    """
    assert await tm.count_assets_bus() == 0
    t = await tm.create_task(mode="cut_qc", input_path="/a.mp4")
    assert await tm.count_assets_bus() == 0
    await tm.update_task_safe(t["id"], status="running", qc_attempts=1)
    assert await tm.count_assets_bus() == 0
    await tm.update_task_safe(
        t["id"],
        status="done",
        output_path="/out.mp4",
        completed_at=time.time(),
        qc_issues_count=0,
    )
    assert await tm.count_assets_bus() == 0
    await tm.delete_task(t["id"])
    assert await tm.count_assets_bus() == 0
    # And list_assets_bus reads cleanly without raising.
    assert await tm.list_assets_bus() == []
