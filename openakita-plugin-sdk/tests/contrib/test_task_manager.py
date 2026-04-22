"""Tests for openakita_plugin_sdk.contrib.task_manager."""

from __future__ import annotations

import pytest

# task_manager imports aiosqlite lazily; skip the whole module if missing
pytest.importorskip("aiosqlite")

from openakita_plugin_sdk.contrib import BaseTaskManager, TaskStatus  # noqa: E402


class _Mgr(BaseTaskManager):
    def extra_task_columns(self):
        return [("vendor_meta", "TEXT")]

    def default_config(self):
        return {"poll_interval": "10"}


@pytest.mark.asyncio
async def test_create_get_update_lifecycle(tmp_path) -> None:
    m = _Mgr(tmp_path / "t.db")
    tid = await m.create_task(prompt="hi", params={"n": 1})
    rec = await m.get_task(tid)
    assert rec is not None
    assert rec.prompt == "hi"
    assert rec.params == {"n": 1}
    assert rec.status == TaskStatus.PENDING.value

    await m.update_task(tid, status=TaskStatus.RUNNING.value, vendor_task_id="v1")
    rec2 = await m.get_task(tid)
    assert rec2.status == TaskStatus.RUNNING.value
    assert rec2.vendor_task_id == "v1"


@pytest.mark.asyncio
async def test_cancel_sets_status_cancelled(tmp_path) -> None:
    m = _Mgr(tmp_path / "t.db")
    tid = await m.create_task(prompt="x")
    out = await m.cancel_task(tid)
    assert out is not None
    assert out.status == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_cancel_terminal_task_is_noop(tmp_path) -> None:
    m = _Mgr(tmp_path / "t.db")
    tid = await m.create_task(prompt="x")
    await m.update_task(tid, status=TaskStatus.SUCCEEDED.value)
    out = await m.cancel_task(tid)
    assert out.status == TaskStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_list_filters_by_status(tmp_path) -> None:
    m = _Mgr(tmp_path / "t.db")
    a = await m.create_task(prompt="a")
    b = await m.create_task(prompt="b")
    await m.update_task(b, status=TaskStatus.SUCCEEDED.value)
    pendings = await m.list_tasks(status=TaskStatus.PENDING.value)
    assert {t.id for t in pendings} == {a}


@pytest.mark.asyncio
async def test_assets_persist(tmp_path) -> None:
    m = _Mgr(tmp_path / "t.db")
    tid = await m.create_task()
    aid = await m.add_asset(task_id=tid, asset_type="video", file_path="/x.mp4", size_bytes=1024)
    rows = await m.list_assets(task_id=tid)
    assert len(rows) == 1
    assert rows[0]["id"] == aid


@pytest.mark.asyncio
async def test_default_config_seeded(tmp_path) -> None:
    m = _Mgr(tmp_path / "t.db")
    await m.init()
    cfg = await m.get_config()
    assert cfg.get("poll_interval") == "10"


@pytest.mark.asyncio
async def test_extra_columns_stored_via_create(tmp_path) -> None:
    m = _Mgr(tmp_path / "t.db")
    tid = await m.create_task(extra={"vendor_meta": "v=1"})
    rec = await m.get_task(tid)
    assert rec.extra.get("vendor_meta") == "v=1"


def test_task_status_terminal_helper() -> None:
    assert TaskStatus.is_terminal("succeeded")
    assert TaskStatus.is_terminal("failed")
    assert TaskStatus.is_terminal("cancelled")
    assert not TaskStatus.is_terminal("running")
