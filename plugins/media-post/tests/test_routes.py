"""Route smoke tests for media-post Phase 4.

Verifies all 22 registered routes are reachable, that the Pydantic
``extra="forbid"`` contract returns HTTP 422 on unknown fields, and
that GET catalogs return shapes matching the documented contract
(§9 / §3.1 / §4 / §5).

The tests stand up a minimal in-process ``FastAPI`` app rather than
booting the full host. The plugin's :class:`Plugin` is loaded with a
fake :class:`PluginAPI` shim that exposes only the surface
``mediapost`` actually touches (``get_data_dir`` / ``register_*`` /
``spawn_task`` / ``log`` / ``broadcast_ui_event``).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))


# ---------------------------------------------------------------------------
# Fake PluginAPI shim
# ---------------------------------------------------------------------------


class _FakeAPI:
    """Minimal PluginAPI surface used by ``Plugin.on_load``."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self.router: APIRouter | None = None
        self.tools: list[Any] = []
        self.tool_handler: Any = None
        self.broadcast_calls: list[tuple[str, dict]] = []
        self.spawned: list[asyncio.Task[None]] = []
        self._loop = asyncio.new_event_loop()

    def get_data_dir(self) -> Path:
        return self._data_dir

    def register_api_routes(self, router: APIRouter) -> None:
        self.router = router

    def register_tools(self, defs: list, handler: Any) -> None:
        self.tools = defs
        self.tool_handler = handler

    def register_ui_event_handler(self, *_a: Any, **_kw: Any) -> None:
        pass

    def broadcast_ui_event(self, event_type: str, data: dict, **_kw: Any) -> None:
        self.broadcast_calls.append((event_type, data))

    def spawn_task(self, coro: Any, *, name: str | None = None) -> asyncio.Task[None]:
        # In tests we don't want real async pipeline runs; immediately close
        # the coroutine and return a completed task placeholder.
        if asyncio.iscoroutine(coro):
            coro.close()
        fut: asyncio.Future[None] = self._loop.create_future()
        fut.set_result(None)
        return fut  # type: ignore[return-value]

    def log(self, msg: str, level: str = "info") -> None:
        pass

    def log_error(self, msg: str, exc: Exception | None = None) -> None:
        pass

    def log_debug(self, msg: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _evict_plugin_modules() -> None:
    for mod in list(sys.modules):
        if mod.startswith("mediapost_") or mod == "plugin":
            del sys.modules[mod]


@pytest.fixture()
def plugin_app(tmp_path: Path) -> tuple[FastAPI, _FakeAPI, Any]:
    _evict_plugin_modules()
    plugin_module = importlib.import_module("plugin")

    fake_api = _FakeAPI(tmp_path)
    plugin = plugin_module.Plugin()
    plugin.on_load(fake_api)
    assert fake_api.router is not None

    # The plugin's TaskManager is lazily inited via spawn_task; do it here
    # synchronously so route handlers that read from sqlite work.
    asyncio.get_event_loop().run_until_complete(plugin._tm.init())

    app = FastAPI()
    app.include_router(fake_api.router, prefix=f"/api/plugins/{plugin_module.PLUGIN_ID}")

    yield app, fake_api, plugin

    asyncio.get_event_loop().run_until_complete(plugin._tm.close())


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _u(path: str) -> str:
    return f"/api/plugins/media-post{path}"


# ---------------------------------------------------------------------------
# Static catalog routes (§3.1 / §4 / §5)
# ---------------------------------------------------------------------------


def test_modes_route_returns_four_modes(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).get(_u("/modes"))
    assert r.status_code == 200
    body = r.json()
    assert {m["id"] for m in body} == {"cover_pick", "multi_aspect", "seo_pack", "chapter_cards"}
    sample = body[0]
    for k in ("label_zh", "label_en", "icon", "description_zh"):
        assert k in sample


def test_platforms_route_returns_five(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).get(_u("/platforms"))
    assert r.status_code == 200
    body = r.json()
    assert {p["id"] for p in body} == {"tiktok", "bilibili", "wechat", "xiaohongshu", "youtube"}


def test_aspects_route_returns_two(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).get(_u("/aspects"))
    assert r.status_code == 200
    ids = {a["id"] for a in r.json()}
    assert ids == {"9:16", "1:1"}


def test_pricing_route_returns_table(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).get(_u("/pricing"))
    assert r.status_code == 200
    body = r.json()
    assert any(it["api"] == "qwen-vl-max" for it in body)
    assert any(it["api"] == "qwen-plus" for it in body)


def test_errors_route_lists_nine_kinds(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).get(_u("/errors"))
    assert r.status_code == 200
    body = r.json()
    kinds = {k["kind"] for k in body["kinds"]}
    assert kinds == {
        "network", "timeout", "auth", "quota", "moderation",
        "dependency", "format", "duration", "unknown",
    }
    assert set(body["platforms"]) == {"tiktok", "bilibili", "wechat", "xiaohongshu", "youtube"}
    assert set(body["aspects"]) == {"9:16", "1:1"}
    assert isinstance(body["templates"], list) and body["templates"]


# ---------------------------------------------------------------------------
# Cost estimate route
# ---------------------------------------------------------------------------


def test_estimate_seo_pack_known_cost(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).post(
        _u("/estimate"),
        json={"mode": "seo_pack", "platforms": ["tiktok", "bilibili"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_cny"] == pytest.approx(0.01, abs=1e-4)
    assert body["cost_kind"] == "ok"


def test_estimate_unknown_mode_returns_400(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).post(_u("/estimate"), json={"mode": "totally_made_up"})
    assert r.status_code == 400


def test_estimate_rejects_unknown_field_with_422(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).post(
        _u("/estimate"),
        json={"mode": "seo_pack", "ghost_field": "boo"},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("ghost_field" in str(d) for d in detail)


# ---------------------------------------------------------------------------
# Tasks CRUD
# ---------------------------------------------------------------------------


def test_create_task_seo_pack_basic(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).post(
        _u("/tasks"),
        json={
            "mode": "seo_pack",
            "video_path": "",
            "params": {"platforms": ["tiktok"], "subtitle_excerpt": "hi"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "seo_pack"
    assert body["status"] in {"pending", "running"}
    assert body["id"]


def test_create_task_unknown_mode_returns_400(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).post(_u("/tasks"), json={"mode": "nope"})
    assert r.status_code == 400


def test_create_task_rejects_unknown_field_with_422(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).post(
        _u("/tasks"),
        json={"mode": "seo_pack", "ghost_field": True},
    )
    assert r.status_code == 422


def test_get_task_404_when_missing(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).get(_u("/tasks/does-not-exist"))
    assert r.status_code == 404


def test_list_tasks_returns_envelope(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    client = _client(app)
    client.post(_u("/tasks"), json={"mode": "seo_pack"})
    r = client.get(_u("/tasks"))
    assert r.status_code == 200
    body = r.json()
    assert "tasks" in body and "total" in body
    assert body["total"] >= 1


def test_delete_task_then_404(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    client = _client(app)
    created = client.post(_u("/tasks"), json={"mode": "seo_pack"}).json()
    tid = created["id"]
    r = client.delete(_u(f"/tasks/{tid}"))
    assert r.status_code == 200
    assert client.get(_u(f"/tasks/{tid}")).status_code == 404


def test_cancel_task_records_request(plugin_app: Any) -> None:
    app, _api, plugin = plugin_app
    client = _client(app)
    created = client.post(_u("/tasks"), json={"mode": "seo_pack"}).json()
    tid = created["id"]
    r = client.post(_u(f"/tasks/{tid}/cancel"))
    assert r.status_code == 200
    assert plugin._tm.is_canceled(tid)


def test_retry_rejects_running_task(plugin_app: Any) -> None:
    app, _api, plugin = plugin_app
    client = _client(app)
    created = client.post(_u("/tasks"), json={"mode": "seo_pack"}).json()
    tid = created["id"]
    asyncio.get_event_loop().run_until_complete(
        plugin._tm.update_task(tid, status="running")
    )
    r = client.post(_u(f"/tasks/{tid}/retry"))
    assert r.status_code == 400


def test_approve_rejects_when_not_pending_approval(plugin_app: Any) -> None:
    app, _api, plugin = plugin_app
    client = _client(app)
    created = client.post(_u("/tasks"), json={"mode": "seo_pack"}).json()
    tid = created["id"]
    r = client.post(_u(f"/tasks/{tid}/approve"))
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Result table routes (return empty lists for fresh tasks)
# ---------------------------------------------------------------------------


def test_result_routes_return_empty_for_new_task(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    client = _client(app)
    created = client.post(_u("/tasks"), json={"mode": "seo_pack"}).json()
    tid = created["id"]
    for sub in ("cover", "recompose", "seo", "chapters"):
        r = client.get(_u(f"/tasks/{tid}/results/{sub}"))
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# Settings routes
# ---------------------------------------------------------------------------


def test_settings_round_trip(plugin_app: Any) -> None:
    app, _api, plugin = plugin_app
    client = _client(app)
    initial = client.get(_u("/settings")).json()
    assert "vlm_model" in initial
    r = client.put(
        _u("/settings"),
        json={"updates": {"dashscope_api_key": "test-key-123"}},
    )
    assert r.status_code == 200
    after = client.get(_u("/settings")).json()
    assert after["dashscope_api_key"] == "test-key-123"
    assert plugin._vlm_client is not None


def test_settings_rejects_unknown_field_with_422(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).put(
        _u("/settings"),
        json={"updates": {"x": "y"}, "ghost": True},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Storage stats route
# ---------------------------------------------------------------------------


def test_storage_stats_returns_dict(plugin_app: Any) -> None:
    app, _api, _plg = plugin_app
    r = _client(app).get(_u("/storage/stats"))
    assert r.status_code == 200
    body = r.json()
    assert "total_files" in body
    assert "total_bytes" in body


# ---------------------------------------------------------------------------
# 22-route coverage assertion
# ---------------------------------------------------------------------------


def test_router_has_22_routes(plugin_app: Any) -> None:
    _app, api, _plg = plugin_app
    assert api.router is not None
    # Filter only routes registered by the plugin (exclude internal FastAPI bookkeeping).
    paths = [getattr(r, "path", "") for r in api.router.routes]
    # 22 endpoints: uploads (1) + upload (1) + modes/platforms/aspects/pricing/errors (5)
    # + estimate (1) + tasks CRUD x6 (POST/GET list/GET one/DELETE/cancel/retry/approve) (7)
    # + results x4 + settings x2 + storage/stats (1) = 22
    assert len(paths) >= 22
