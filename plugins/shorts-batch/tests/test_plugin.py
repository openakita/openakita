"""End-to-end tests for ``plugin.Plugin`` (shorts-batch)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


class FakePluginAPI:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self.routes: APIRouter | None = None
        self.tool_defs: list[dict] = []
        self.tool_handler: Callable | None = None
        self.logs: list[tuple[str, str]] = []

    def get_data_dir(self) -> Path:
        return self._data_dir

    def register_api_routes(self, router: APIRouter) -> None:
        self.routes = router

    def register_tools(self, definitions: list[dict], handler: Callable) -> None:
        self.tool_defs = list(definitions)
        self.tool_handler = handler

    def log(self, msg: str, level: str = "info") -> None:
        self.logs.append((level, msg))

    def emit_event(self, *_a: Any, **_kw: Any) -> None:
        return None

    def emit_ui_event(self, *_a: Any, **_kw: Any) -> None:
        return None


@pytest.fixture
def plugin_factory(tmp_path: Path):
    from plugin import Plugin

    def _make() -> tuple[Any, FakePluginAPI, FastAPI]:
        api = FakePluginAPI(tmp_path)
        p = Plugin()
        p.on_load(api)
        assert api.routes is not None
        app = FastAPI()
        app.include_router(api.routes)
        return p, api, app

    return _make


def _wait_until_done(client: TestClient, tid: str, *, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/tasks/{tid}")
        assert r.status_code == 200
        rec = r.json()
        if rec["status"] in {"succeeded", "failed", "cancelled"}:
            return rec
        time.sleep(0.05)
    raise AssertionError(f"task {tid} did not finish in {timeout}s")


# ── on_load ────────────────────────────────────────────────────────────


def test_on_load_registers_documented_tools(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    names = {d["name"] for d in api.tool_defs}
    assert names == {
        "shorts_batch_create",
        "shorts_batch_status",
        "shorts_batch_list",
        "shorts_batch_cancel",
        "shorts_batch_preview_risk",
    }


def test_on_load_logs_load_message(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msgs = [m for _, m in api.logs]
    assert any("loaded" in m for m in msgs)


def test_on_load_attaches_default_planner_and_renderer(plugin_factory) -> None:
    p, _, _ = plugin_factory()
    assert callable(p._planner)
    assert callable(p._renderer)


# ── /healthz + /config ────────────────────────────────────────────────


def test_healthz_lists_aspects(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/healthz").json()
        assert body["plugin"] == "shorts-batch"
        assert "9:16" in body["allowed_aspects"]


def test_config_get_returns_defaults(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/config").json()
        assert body["default_aspect"] == "9:16"
        assert body["default_style"] == "vlog"


def test_config_post_persists_overrides(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        c.post("/config", json={"default_style": "news"})
        body = c.get("/config").json()
        assert body["default_style"] == "news"


# ── /preview-risk ─────────────────────────────────────────────────────


def test_preview_risk_returns_per_brief_verdict(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.post("/preview-risk", json={
            "briefs": [{"topic": "cats", "duration_sec": 10.0}],
        }).json()
        assert len(body["items"]) == 1
        assert body["items"][0]["risk"]["verdict"] in {"low", "medium", "high"}
        assert body["total_estimated_cost_usd"] > 0


def test_preview_risk_rejects_invalid_aspect(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview-risk", json={
            "briefs": [{"topic": "x", "target_aspect": "3:2"}],
        })
        assert r.status_code == 400


def test_preview_risk_rejects_empty_brief_list(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview-risk", json={"briefs": []})
        assert r.status_code == 422


# ── /tasks (POST → worker → success) ──────────────────────────────────


def test_create_task_runs_to_success(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={
            "briefs": [
                {"topic": "cats", "duration_sec": 10.0},
                {"topic": "dogs", "duration_sec": 10.0},
            ],
        })
        assert r.status_code == 200, r.text
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded", rec
        assert rec["result"]["total"] == 2
        assert rec["result"]["succeeded"] == 2


def test_create_task_records_failure_count_when_renderer_blows_up(
    plugin_factory,
) -> None:
    p, _, app = plugin_factory()

    def _explode(_plan):
        raise RuntimeError("renderer dead")

    p.set_renderer(_explode)
    with TestClient(app) as c:
        tid = c.post("/tasks", json={
            "briefs": [{"topic": "cats", "duration_sec": 10.0}],
        }).json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"  # batch survives even if all fail
        assert rec["result"]["failed"] == 1
        # Verification should flag the failure.
        assert rec["result"]["verification"]["verified"] is False


def test_create_task_blocks_high_risk_when_threshold_set(plugin_factory) -> None:
    p, _, app = plugin_factory()

    def _high_risk_planner(_brief):
        return [
            {"shot_type": "wide", "duration": 10.0, "description": "still 1"},
            {"shot_type": "wide", "duration": 10.0, "description": "still 2"},
            {"shot_type": "wide", "duration": 10.0, "description": "still 3"},
        ]

    p.set_planner(_high_risk_planner)
    with TestClient(app) as c:
        tid = c.post("/tasks", json={
            "briefs": [{"topic": "x", "duration_sec": 30.0}],
            "risk_block_threshold": "high",
        }).json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        # Render was skipped → all failed.
        assert rec["result"]["failed"] == 1
        assert "block" in rec["result"]["results"][0]["error"].lower()


def test_create_task_rejects_empty_brief_list(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"briefs": []})
        assert r.status_code == 422


def test_create_task_records_failure_when_brief_invalid(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        # ``target_aspect`` is invalid → planning will raise → task fails.
        tid = c.post("/tasks", json={
            "briefs": [{"topic": "x", "target_aspect": "3:2"}],
        }).json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "failed"
        assert rec["error_message"]


# ── /tasks (GET) ──────────────────────────────────────────────────────


def test_list_tasks_returns_recent_jobs(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        for _ in range(3):
            c.post("/tasks", json={
                "briefs": [{"topic": "cats", "duration_sec": 10.0}],
            })
        body = c.get("/tasks").json()
        assert body["total"] >= 3


def test_get_task_returns_404_for_missing(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        assert c.get("/tasks/nope").status_code == 404


def test_delete_task_works(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={
            "briefs": [{"topic": "cats", "duration_sec": 10.0}],
        }).json()["task_id"]
        _wait_until_done(c, tid)
        assert c.delete(f"/tasks/{tid}").status_code == 200
        assert c.get(f"/tasks/{tid}").status_code == 404


def test_cancel_returns_404_for_missing(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        assert c.post("/tasks/nope/cancel").status_code == 404


# ── brain tools ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brain_tool_preview_risk_returns_summary(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    out = await api.tool_handler("shorts_batch_preview_risk", {
        "briefs": [{"topic": "cats", "duration_sec": 10.0}],
    })
    assert "cats" in out
    assert "风险" in out


@pytest.mark.asyncio
async def test_brain_tool_status_for_missing_task(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    out = await api.tool_handler("shorts_batch_status", {"task_id": "nope"})
    assert "未找到" in out


@pytest.mark.asyncio
async def test_brain_tool_list_returns_empty_when_no_tasks(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    out = await api.tool_handler("shorts_batch_list", {})
    assert out == "(空)"


@pytest.mark.asyncio
async def test_brain_tool_unknown_returns_message(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    out = await api.tool_handler("does_not_exist", {})
    assert "unknown tool" in out
