"""HappyhorseTaskManager — async sqlite CRUD smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from happyhorse_task_manager import HappyhorseTaskManager


@pytest_asyncio.fixture()
async def tm(tmp_path: Path):
    mgr = HappyhorseTaskManager(tmp_path / "hh.db")
    await mgr.init()
    try:
        yield mgr
    finally:
        await mgr.close()


@pytest.mark.asyncio
async def test_create_then_get_task(tm: HappyhorseTaskManager):
    task_id = await tm.create_task(
        mode="t2v",
        model_id="happyhorse-1.0-t2v",
        prompt="hello",
    )
    row = await tm.get_task(task_id)
    assert row is not None
    assert row["mode"] == "t2v"
    assert row["model_id"] == "happyhorse-1.0-t2v"
    assert row["prompt"] == "hello"
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_update_task_safe_workbench_fields(tm: HappyhorseTaskManager):
    task_id = await tm.create_task(mode="i2v", model_id="happyhorse-1.0-i2v")
    await tm.update_task_safe(
        task_id,
        status="succeeded",
        video_url="https://example.com/v.mp4",
        video_path="/tmp/v.mp4",
        last_frame_url="https://example.com/lf.png",
        last_frame_path="/tmp/lf.png",
        asset_ids_json=["a1", "a2"],
    )
    row = await tm.get_task(task_id)
    assert row is not None
    assert row["status"] == "succeeded"
    assert row["video_url"] == "https://example.com/v.mp4"
    assert row["asset_ids"] == ["a1", "a2"]


@pytest.mark.asyncio
async def test_update_task_safe_rejects_unknown_column(tm: HappyhorseTaskManager):
    task_id = await tm.create_task(mode="t2v")
    with pytest.raises(ValueError):
        await tm.update_task_safe(task_id, totally_unknown_column="x")


@pytest.mark.asyncio
async def test_list_tasks_filters_by_status_and_mode(tm: HappyhorseTaskManager):
    a = await tm.create_task(mode="t2v")
    b = await tm.create_task(mode="i2v")
    await tm.update_task_safe(a, status="succeeded")
    rows = await tm.list_tasks(status="succeeded")
    ids = [r["id"] for r in rows]
    assert a in ids and b not in ids
    rows = await tm.list_tasks(mode="i2v")
    ids = [r["id"] for r in rows]
    assert b in ids and a not in ids


@pytest.mark.asyncio
async def test_chain_group_id_query(tm: HappyhorseTaskManager):
    gid = "chain_xyz"
    await tm.create_task(mode="i2v", chain_group_id=gid, chain_index=1, chain_total=3)
    await tm.create_task(mode="i2v", chain_group_id=gid, chain_index=2, chain_total=3)
    await tm.create_task(mode="i2v", chain_group_id="other", chain_index=1)
    rows = await tm.list_tasks(chain_group_id=gid)
    assert len(rows) == 2
    assert all(r["chain_group_id"] == gid for r in rows)


@pytest.mark.asyncio
async def test_config_round_trip(tm: HappyhorseTaskManager):
    await tm.set_config("api_key", "sk-xxx")
    cfg = await tm.get_all_config()
    assert cfg["api_key"] == "sk-xxx"
    await tm.set_configs({"oss_bucket": "demo", "oss_endpoint": "ep"})
    cfg = await tm.get_all_config()
    assert cfg["oss_bucket"] == "demo"
    assert cfg["oss_endpoint"] == "ep"
    assert cfg["api_key"] == "sk-xxx"


@pytest.mark.asyncio
async def test_idempotency_via_client_request_id(tm: HappyhorseTaskManager):
    task_id = await tm.create_task(
        mode="t2v", prompt="x", client_request_id="cri_abc"
    )
    found = await tm.get_task_by_client_request_id("cri_abc")
    assert found is not None
    assert found["id"] == task_id


@pytest.mark.asyncio
async def test_delete_task(tm: HappyhorseTaskManager):
    task_id = await tm.create_task(mode="t2v")
    assert await tm.delete_task(task_id) is True
    assert await tm.get_task(task_id) is None


@pytest.mark.asyncio
async def test_count_tasks(tm: HappyhorseTaskManager):
    await tm.create_task(mode="t2v")
    await tm.create_task(mode="t2v")
    await tm.create_task(mode="i2v")
    assert await tm.count_tasks() == 3
