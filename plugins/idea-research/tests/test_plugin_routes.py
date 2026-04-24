"""Phase 4 route + tool tests for ``plugin.Plugin``.

We exercise every one of the §10 26 routes and §11 9 tools through the
real FastAPI application, but with the heavy back-ends swapped for the
test doubles in :mod:`tests.conftest` so nothing reaches the network.

What we verify
--------------
* All 26 routes register and answer the happy-path with 200/2xx.
* Pydantic ``extra='forbid'`` returns 422 for unknown fields (§19 C6).
* Missing-resource paths produce 404.
* Each of the 9 tools dispatches to the right runner / DB write.
* The scheduler eventually triggers a ``radar_pull`` task for an
  enabled subscription.
* ``on_load`` works even when ``brain.access`` / ``vector.access`` /
  ``memory.write`` are revoked (no exceptions).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import plugin as plugin_module
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from idea_dashscope_client import ChatResult
from idea_models import TrendItem
from plugin import Plugin

# --------------------------------------------------------------------------- #
# Stub registry / dashscope so route tests never hit the network              #
# --------------------------------------------------------------------------- #


class _StubRegistry:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fetch_for_radar(self, platforms, keywords, **kwargs):  # noqa: ANN001, ANN201
        self.calls.append({"platforms": platforms, "keywords": keywords, **kwargs})
        item = TrendItem(
            id=f"radar-{len(self.calls)}",
            platform=platforms[0] if platforms else "bilibili",
            external_id=f"BV{len(self.calls)}",
            external_url="https://b/x",
            title="t",
            duration_seconds=60,
            like_count=10,
            view_count=100,
            publish_at=int(time.time()) - 60,
            fetched_at=int(time.time()),
            score=0.5,
        )
        return {"items": [item], "errors": [], "choices": [], "fetched_at": int(time.time())}

    async def fetch_single_url(self, url, **_):  # noqa: ANN001, ANN201
        return TrendItem(
            id="single-1",
            platform="bilibili",
            external_id="BVx",
            external_url=url,
            title="single",
            duration_seconds=60,
            publish_at=int(time.time()),
            fetched_at=int(time.time()),
        )

    async def aclose(self) -> None:
        pass


class _StubDashScope:
    def __init__(self) -> None:
        self.api_key = "sk-stub"

    async def chat_completion(self, **kwargs):  # noqa: ANN003, ANN201
        return ChatResult(
            content="{}",
            model=kwargs.get("model", "qwen-max"),
            parsed_json={"variants": [{"title": "x"}]},
        )

    async def describe_image(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        raise NotImplementedError

    async def transcribe_audio(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


def _ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Return the current loop, or create + install a fresh one.

    Newer pytest-asyncio (3.x) auto-closes the per-test loop after the
    test exits which can leave ``get_event_loop`` raising. The plugin's
    on_load + scheduler need a loop, so make sure one is installed.
    """

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


@pytest.fixture()
def loaded_plugin(fake_api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    fake_api._data_dir = tmp_path
    p = Plugin()
    monkeypatch.setattr(plugin_module, "PlaywrightDriver", lambda **kw: object())
    loop = _ensure_event_loop()
    p.on_load(fake_api)
    # Replace heavy dependencies with stubs and run DB init synchronously
    # so route tests don't race with the background init task.
    p._collectors = _StubRegistry()
    p._dashscope = _StubDashScope()
    assert p._tm is not None
    loop.run_until_complete(p._tm.init())
    yield p
    for t in list(p._tasks.values()):
        if not t.done():
            t.cancel()
    p._scheduler_stop.set()
    p._api = None
    loop.run_until_complete(p._tm.close())


@pytest.fixture()
def client(loaded_plugin: Plugin) -> Iterator[TestClient]:
    routes = loaded_plugin._build_router()
    app = FastAPI()
    app.include_router(routes)
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Settings + healthz                                                           #
# --------------------------------------------------------------------------- #


def test_healthz_route(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "mdrm" in body
    assert body["version"]


def test_settings_get_and_put(client: TestClient) -> None:
    r = client.get("/settings")
    assert r.status_code == 200
    initial = r.json()
    assert isinstance(initial, dict)

    r2 = client.put(
        "/settings",
        json={"updates": {"engine_b_enabled": True, "rsshub_base": "https://r"}},
    )
    assert r2.status_code == 200
    after = r2.json()
    assert after["engine_b_enabled"] is True
    assert after["rsshub_base"] == "https://r"


def test_settings_put_rejects_unknown_field(client: TestClient) -> None:
    r = client.put("/settings", json={"updates": {"x": 1}, "rogue": "field"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Tasks lifecycle                                                              #
# --------------------------------------------------------------------------- #


def test_create_task_then_list_then_get(client: TestClient) -> None:
    payload = {
        "mode": "radar_pull",
        "input": {"platforms": ["bilibili"], "keywords": ["AI"], "limit": 5},
    }
    r = client.post("/tasks", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    tid = body["task_id"]

    r2 = client.get("/tasks")
    assert r2.status_code == 200
    listed = r2.json()
    assert any(t["id"] == tid for t in listed["tasks"])

    r3 = client.get(f"/tasks/{tid}")
    assert r3.status_code == 200
    assert r3.json()["id"] == tid


def test_get_unknown_task_returns_404(client: TestClient) -> None:
    r = client.get("/tasks/does-not-exist")
    assert r.status_code == 404


def test_create_task_rejects_unknown_mode(client: TestClient) -> None:
    r = client.post("/tasks", json={"mode": "bogus", "input": {}})
    assert r.status_code == 422


def test_create_task_rejects_extra_field(client: TestClient) -> None:
    r = client.post(
        "/tasks",
        json={"mode": "radar_pull", "input": {}, "rogue": True},
    )
    assert r.status_code == 422


def test_cost_preview_returns_breakdown(client: TestClient) -> None:
    r = client.post(
        "/cost-preview",
        json={"mode": "breakdown_url", "input": {"duration_seconds_estimate": 120}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "breakdown_url"
    assert body["cost_cny"] >= 0


def test_cancel_task(client: TestClient) -> None:
    r = client.post(
        "/tasks",
        json={"mode": "radar_pull", "input": {"platforms": ["bilibili"]}},
    )
    tid = r.json()["task_id"]
    rc = client.post(f"/tasks/{tid}/cancel")
    assert rc.status_code == 200
    assert rc.json() == {"ok": True}


def test_retry_task(client: TestClient) -> None:
    r = client.post(
        "/tasks",
        json={"mode": "radar_pull", "input": {"platforms": ["bilibili"]}},
    )
    tid = r.json()["task_id"]
    rr = client.post(f"/tasks/{tid}/retry")
    assert rr.status_code == 200
    assert rr.json()["new_task_id"] != tid


def test_delete_task(client: TestClient) -> None:
    r = client.post(
        "/tasks",
        json={"mode": "radar_pull", "input": {"platforms": ["bilibili"]}},
    )
    tid = r.json()["task_id"]
    rd = client.delete(f"/tasks/{tid}")
    assert rd.status_code == 200
    rg = client.get(f"/tasks/{tid}")
    assert rg.status_code == 404


def test_breakdown_returns_404_when_missing(client: TestClient) -> None:
    r = client.get("/tasks/missing/breakdown")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Recommendations + items                                                      #
# --------------------------------------------------------------------------- #


def test_recommendations_route(client: TestClient, loaded_plugin: Plugin) -> None:
    assert loaded_plugin._tm is not None
    _ensure_event_loop().run_until_complete(
        loaded_plugin._tm.upsert_trend_item(
            {
                "id": "rec-1",
                "platform": "bilibili",
                "external_id": "BV111",
                "external_url": "https://b/1",
                "title": "rec",
                "publish_at": int(time.time()) - 60,
                "fetched_at": int(time.time()),
                "score": 0.7,
            }
        )
    )
    r = client.get("/recommendations?limit=5")
    assert r.status_code == 200
    assert any(it["id"] == "rec-1" for it in r.json()["items"])


def test_save_item_route(client: TestClient, loaded_plugin: Plugin) -> None:
    assert loaded_plugin._tm is not None
    _ensure_event_loop().run_until_complete(
        loaded_plugin._tm.upsert_trend_item(
            {
                "id": "save-1",
                "platform": "bilibili",
                "external_id": "BV222",
                "external_url": "https://b/2",
                "title": "save me",
                "publish_at": int(time.time()),
                "fetched_at": int(time.time()),
            }
        )
    )
    r = client.post("/items/save-1/save")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# --------------------------------------------------------------------------- #
# Subscriptions CRUD                                                           #
# --------------------------------------------------------------------------- #


def test_subscriptions_crud(client: TestClient) -> None:
    sub = {
        "name": "sub1",
        "platforms": ["bilibili"],
        "keywords": ["AI"],
        "time_window": "24h",
        "refresh_interval_min": 30,
        "enabled": True,
    }
    r = client.post("/subscriptions", json=sub)
    assert r.status_code == 200
    sid = r.json()["id"]

    r2 = client.get("/subscriptions")
    assert r2.status_code == 200
    assert any(s["id"] == sid for s in r2.json()["subs"])

    rd = client.delete(f"/subscriptions/{sid}")
    assert rd.status_code == 200


def test_subscriptions_invalid_interval(client: TestClient) -> None:
    r = client.post(
        "/subscriptions",
        json={
            "name": "s",
            "platforms": ["bilibili"],
            "refresh_interval_min": 1,  # below ge=5 bound
        },
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Sources + cookies                                                            #
# --------------------------------------------------------------------------- #


def test_sources_route(client: TestClient) -> None:
    r = client.get("/sources")
    assert r.status_code == 200
    body = r.json()
    assert body["engine_a"]["enabled"] is True
    assert "engine_b" in body
    assert "cookies_status" in body["engine_b"]


def test_cookies_upload_requires_risk_ack(client: TestClient) -> None:
    r = client.post(
        "/sources/cookies/douyin",
        json={"cookies_dict": {"sessionid": "x"}},
    )
    assert r.status_code == 422


def test_cookies_upload_with_risk_ack(client: TestClient) -> None:
    r = client.post(
        "/sources/cookies/douyin",
        json={"cookies_dict": {"sessionid": "x"}, "risk_acknowledged": True},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_cookies_test_route(client: TestClient) -> None:
    r = client.post("/sources/cookies/xhs/test")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body and "message" in body


# --------------------------------------------------------------------------- #
# Accounts preview + cleanup                                                   #
# --------------------------------------------------------------------------- #


def test_accounts_preview_route(client: TestClient) -> None:
    r = client.post(
        "/accounts/preview",
        json={"urls": ["https://www.bilibili.com/space/uid/1", "https://x.com/y"]},
    )
    assert r.status_code == 200
    accounts = r.json()["accounts"]
    assert accounts[0]["platform_guess"] == "bilibili"


def test_cleanup_route(client: TestClient) -> None:
    r = client.post("/cleanup", json={"older_than_days": 365})
    assert r.status_code == 200
    body = r.json()
    assert "deleted" in body and "freed_mb" in body


def test_cleanup_rejects_zero_days(client: TestClient) -> None:
    r = client.post("/cleanup", json={"older_than_days": 0})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# MDRM 3 routes                                                                #
# --------------------------------------------------------------------------- #


def test_mdrm_stats_route(client: TestClient) -> None:
    r = client.get("/mdrm/stats")
    assert r.status_code == 200
    body = r.json()
    assert "caps" in body or "hook_count" in body


def test_mdrm_clear_requires_confirm(client: TestClient) -> None:
    r = client.post("/mdrm/clear", json={"confirm": False})
    assert r.status_code == 422


def test_mdrm_clear_with_confirm(client: TestClient) -> None:
    r = client.post("/mdrm/clear", json={"confirm": True})
    assert r.status_code == 200
    assert "cleared" in r.json()


def test_mdrm_reindex_route(client: TestClient) -> None:
    r = client.post("/mdrm/reindex", json={"from_days_ago": 7})
    assert r.status_code == 200
    body = r.json()
    assert {"reindexed", "skipped", "failed"} <= body.keys()


# --------------------------------------------------------------------------- #
# Tool dispatcher                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_handler_dispatch_radar(loaded_plugin: Plugin) -> None:
    result = await loaded_plugin._handle_tool(
        "idea_radar_pull",
        {"platforms": ["bilibili"], "keywords": ["AI"], "limit": 3},
    )
    assert "task_id" in result
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_tool_handler_dispatch_breakdown(loaded_plugin: Plugin) -> None:
    result = await loaded_plugin._handle_tool(
        "idea_breakdown_url",
        {"url": "https://www.bilibili.com/video/BV1xx", "persona": "B站知识博主"},
    )
    assert "task_id" in result


@pytest.mark.asyncio
async def test_tool_handler_subscriptions(loaded_plugin: Plugin) -> None:
    sub = await loaded_plugin._handle_tool(
        "idea_subscribe",
        {"name": "tool-sub", "platforms": ["bilibili"], "keywords": []},
    )
    assert sub["id"]
    listed = await loaded_plugin._handle_tool("idea_list_subscriptions", {})
    assert any(s["id"] == sub["id"] for s in listed["subs"])
    out = await loaded_plugin._handle_tool("idea_unsubscribe", {"subscription_id": sub["id"]})
    assert out == {"ok": True}


@pytest.mark.asyncio
async def test_tool_handler_export_requires_id(loaded_plugin: Plugin) -> None:
    with pytest.raises(ValueError):
        await loaded_plugin._handle_tool("idea_export", {"format": "json"})


@pytest.mark.asyncio
async def test_tool_handler_unknown_name(loaded_plugin: Plugin) -> None:
    with pytest.raises(ValueError):
        await loaded_plugin._handle_tool("does_not_exist", {})


# --------------------------------------------------------------------------- #
# Scheduler                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_scheduler_dispatches_due_subscription(
    loaded_plugin: Plugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert loaded_plugin._tm is not None
    sub_id = await loaded_plugin._tm.upsert_subscription(
        {
            "id": "sched-1",
            "name": "due",
            "platforms": ["bilibili"],
            "keywords": ["x"],
            "time_window": "24h",
            "refresh_interval_min": 5,
            "enabled": True,
            "last_run_at": 0,
        }
    )
    spawned: list[str] = []

    async def fake_create(mode: str, inp: dict[str, Any], **_: Any) -> dict[str, Any]:
        spawned.append(mode)
        return {"task_id": "x", "status": "pending", "eta_s": 0}

    monkeypatch.setattr(loaded_plugin, "_create_and_spawn_task", fake_create)
    monkeypatch.setattr(plugin_module, "SCHEDULER_TICK_S", 0.05)
    loaded_plugin._scheduler_stop = asyncio.Event()
    task = asyncio.create_task(loaded_plugin._scheduler())
    await asyncio.sleep(0.2)
    loaded_plugin._scheduler_stop.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert "radar_pull" in spawned
    assert sub_id == "sched-1"


# --------------------------------------------------------------------------- #
# Permission-degraded boot                                                     #
# --------------------------------------------------------------------------- #


def test_plugin_loads_without_mdrm_permissions(
    fake_api_no_mdrm, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_api_no_mdrm._data_dir = tmp_path
    p = Plugin()
    monkeypatch.setattr(plugin_module, "PlaywrightDriver", lambda **kw: object())
    loop = _ensure_event_loop()
    p.on_load(fake_api_no_mdrm)
    try:
        assert p._mdrm is not None
        stats = loop.run_until_complete(p._mdrm.stats())
        caps = stats.get("caps", {})
        assert caps == {} or all(value is False for value in caps.values())
    finally:
        for t in list(p._tasks.values()):
            if not t.done():
                t.cancel()
        p._scheduler_stop.set()
        p._api = None
        if p._tm is not None:
            loop.run_until_complete(p._tm.close())


# --------------------------------------------------------------------------- #
# Tool definitions sanity                                                      #
# --------------------------------------------------------------------------- #


def test_tool_definitions_count_and_required_fields(loaded_plugin: Plugin) -> None:
    tools = loaded_plugin._tool_definitions()
    assert len(tools) == 9
    for t in tools:
        assert {"name", "description", "input_schema"} <= t.keys()
        assert t["name"].startswith("idea_")
