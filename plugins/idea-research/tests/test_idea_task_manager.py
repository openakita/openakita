"""Unit tests for ``IdeaTaskManager`` (§8 + §10 update_task_safe whitelist)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from idea_task_manager import (
    TASK_UPDATE_WHITELIST,
    VALID_TASK_STATUSES,
    IdeaTaskManager,
)


@pytest.fixture()
def tm(tmp_path: Path) -> IdeaTaskManager:
    db = tmp_path / "idea.db"
    return IdeaTaskManager(db)


# ---- schema ----------------------------------------------------------------


def test_init_creates_all_seven_tables(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    with sqlite3.connect(tm.db_path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "tasks",
        "subscriptions",
        "trend_items",
        "personas",
        "hook_library",
        "cookies",
        "settings",
    }
    assert expected <= names


def test_init_seeds_twelve_builtin_personas(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    asyncio.run(tm.init())  # idempotent
    rows = asyncio.run(tm.list_personas())
    builtin = [r for r in rows if r.get("is_builtin") == 1]
    assert len(builtin) == 12


# ---- tasks -----------------------------------------------------------------


def test_insert_and_get_task_round_trip(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    tid = asyncio.run(tm.insert_task(mode="radar_pull", input_payload={"keywords": ["AI"]}))
    task = asyncio.run(tm.get_task(tid))
    assert task is not None
    assert task["status"] == "pending"
    assert task["mode"] == "radar_pull"
    assert task["input_json"] == {"keywords": ["AI"]}


def test_insert_task_rejects_unknown_mode(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    with pytest.raises(ValueError):
        asyncio.run(tm.insert_task(mode="bogus", input_payload={}))


def test_update_task_safe_whitelist_drops_unknown_columns(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    tid = asyncio.run(tm.insert_task(mode="breakdown_url", input_payload={"url": "x"}))
    res = asyncio.run(
        tm.update_task_safe(
            tid,
            {
                "status": "running",
                "progress_pct": 42,
                "current_step": "media_download",
                "evil_column": "ignored",
                "id": "tampered",
            },
        )
    )
    assert res["updated"] == 1
    assert "evil_column" in res["ignored"] and "id" in res["ignored"]
    after = asyncio.run(tm.get_task(tid))
    assert after["status"] == "running"
    assert after["progress_pct"] == 42
    assert after["current_step"] == "media_download"
    assert after["id"] == tid


def test_update_task_safe_rejects_invalid_status(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    tid = asyncio.run(tm.insert_task(mode="breakdown_url", input_payload={"url": "x"}))
    with pytest.raises(ValueError):
        asyncio.run(tm.update_task_safe(tid, {"status": "weird"}))


def test_update_task_safe_with_no_clean_keys_returns_zero(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    tid = asyncio.run(tm.insert_task(mode="breakdown_url", input_payload={"url": "x"}))
    res = asyncio.run(tm.update_task_safe(tid, {"id": "nope"}))
    assert res == {"updated": 0, "ignored": ["id"]}


def test_list_tasks_filters_and_orders(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    a = asyncio.run(tm.insert_task(mode="radar_pull", input_payload={}))
    b = asyncio.run(tm.insert_task(mode="breakdown_url", input_payload={}))
    asyncio.run(tm.update_task_safe(b, {"status": "done"}))
    page = asyncio.run(tm.list_tasks(mode="breakdown_url"))
    assert page["total"] == 1
    assert page["tasks"][0]["id"] == b
    pending_page = asyncio.run(tm.list_tasks(status="pending"))
    assert {t["id"] for t in pending_page["tasks"]} == {a}


def test_delete_task_returns_rowcount(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    tid = asyncio.run(tm.insert_task(mode="radar_pull", input_payload={}))
    assert asyncio.run(tm.delete_task(tid)) == 1
    assert asyncio.run(tm.get_task(tid)) is None


def test_task_whitelist_matches_doc():
    """Plan §10 freezes the whitelist; tighten or loosen with intent."""

    expected = frozenset(
        {
            "status",
            "progress_pct",
            "current_step",
            "output_json",
            "error_kind",
            "error_message",
            "error_hint_zh",
            "error_hint_en",
            "started_at",
            "finished_at",
            "cost_cny",
            "handoff_target",
            "mdrm_writes_json",
        }
    )
    assert expected == TASK_UPDATE_WHITELIST
    assert "pending" in VALID_TASK_STATUSES


# ---- subscriptions ---------------------------------------------------------


def test_subscription_upsert_idempotent(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    sid = asyncio.run(
        tm.upsert_subscription(
            {
                "id": "s1",
                "name": "AI 工具",
                "platforms": ["bilibili", "youtube"],
                "keywords": ["AI", "Cursor"],
                "time_window": "24h",
            }
        )
    )
    asyncio.run(
        tm.upsert_subscription(
            {
                "id": sid,
                "name": "AI 工具 v2",
                "platforms": ["bilibili"],
                "keywords": ["AI"],
                "time_window": "12h",
                "enabled": False,
                "refresh_interval_min": 30,
            }
        )
    )
    rows = asyncio.run(tm.list_subscriptions())
    assert len(rows) == 1
    assert rows[0]["name"] == "AI 工具 v2"
    assert rows[0]["platforms"] == ["bilibili"]
    assert rows[0]["enabled"] == 0
    assert rows[0]["refresh_interval_min"] == 30
    assert asyncio.run(tm.delete_subscription(sid)) == 1


# ---- trend_items -----------------------------------------------------------


def test_trend_item_upsert_dedupes_on_external_id(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    item_v1 = {
        "id": "t1",
        "platform": "bilibili",
        "external_id": "BV1xxx",
        "external_url": "https://b23.tv/BV1xxx",
        "title": "v1",
        "score": 5.0,
        "fetched_at": 1_700_000_000,
        "publish_at": 1_699_900_000,
        "view_count": 100,
        "like_count": 5,
    }
    item_v2 = {**item_v1, "id": "t1", "title": "v2", "score": 9.5}
    asyncio.run(tm.upsert_trend_item(item_v1))
    asyncio.run(tm.upsert_trend_item(item_v2))
    listed = asyncio.run(tm.list_trend_items(platforms=["bilibili"]))
    assert len(listed) == 1
    assert listed[0]["title"] == "v2"
    assert listed[0]["score"] == 9.5


def test_mark_item_saved_round_trip(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    item = {
        "id": "t2",
        "platform": "douyin",
        "external_id": "dy1",
        "external_url": "https://www.douyin.com/video/dy1",
        "title": "x",
        "score": 1.0,
        "fetched_at": 1,
        "publish_at": 1,
    }
    asyncio.run(tm.upsert_trend_item(item))
    asyncio.run(tm.mark_item_saved("t2", True))
    saved = asyncio.run(tm.list_trend_items(only_saved=True))
    assert len(saved) == 1
    assert saved[0]["id"] == "t2"


# ---- hook library ----------------------------------------------------------


def test_hook_library_insert_marks_write_flags(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    rid = asyncio.run(
        tm.insert_hook_library(
            {
                "id": "h1",
                "hook_type": "悬念",
                "hook_text": "你绝对没看过的 AI 玩法",
                "persona": "douyin_director",
                "platform": "douyin",
                "score": 9.1,
                "brand_keywords": ["AI"],
                "source_task_id": "tA",
            },
            write_result={"vector": "ok", "memory": "skipped"},
        )
    )
    assert rid == "h1"
    assert asyncio.run(tm.get_hook_library_count()) == 1
    asyncio.run(tm.clear_hook_library())
    assert asyncio.run(tm.get_hook_library_count()) == 0


# ---- cookies ---------------------------------------------------------------


def test_cookies_save_and_test_status(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    asyncio.run(tm.save_cookies("douyin", b"\x00encrypted\x00", expires_at=999))
    row = asyncio.run(tm.get_cookies("douyin"))
    assert row is not None
    assert row["encrypted"] == b"\x00encrypted\x00"
    assert row["expires_at"] == 999
    asyncio.run(tm.update_cookies_test("douyin", ok=True))
    statuses = asyncio.run(tm.list_cookies_status())
    assert any(s["platform"] == "douyin" and s["last_test_ok"] == 1 for s in statuses)


# ---- settings --------------------------------------------------------------


def test_settings_round_trip_json_safely(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    payload = {
        "engine_default": "auto",
        "platforms_enabled": ["bilibili", "douyin"],
        "nested": {"a": 1, "b": [True, None]},
    }
    asyncio.run(tm.set_setting("ui_prefs", payload))
    out = asyncio.run(tm.get_setting("ui_prefs"))
    assert out == payload
    all_settings = asyncio.run(tm.get_all_settings())
    assert all_settings["ui_prefs"] == payload


def test_get_setting_default_when_missing(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    assert asyncio.run(tm.get_setting("missing", default=42)) == 42


# ---- json round-trip safety -----------------------------------------------


def test_input_payload_roundtrip_handles_unicode(tm: IdeaTaskManager):
    asyncio.run(tm.init())
    payload = {"keywords": ["选题", "爆款"], "note": "你好🌟"}
    tid = asyncio.run(tm.insert_task(mode="radar_pull", input_payload=payload))
    task = asyncio.run(tm.get_task(tid))
    assert task["input_json"] == payload
    raw = json.dumps(task["input_json"], ensure_ascii=False)
    assert "选题" in raw
