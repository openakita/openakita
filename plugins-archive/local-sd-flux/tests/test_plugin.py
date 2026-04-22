"""End-to-end-ish tests for ``plugin.Plugin`` (local-sd-flux).

Mirrors ``plugins/ppt-to-video/tests/test_plugin.py``: stand up a fake
:class:`PluginAPI`, patch ComfyUI HTTP calls so the worker pipeline is
deterministic, and drive the FastAPI routes via
:class:`fastapi.testclient.TestClient`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


# ── fake host ─────────────────────────────────────────────────────────


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
def plugin_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build ``(plugin, api, fastapi_app)`` with ComfyUI fully mocked."""
    import comfy_client
    import image_engine as ie
    import plugin as plugin_mod
    from plugin import Plugin

    # Replace ``run_image`` so the worker writes a fake image and skips
    # the real poll/download loop entirely.
    async def _fake_run_image(plan, *, client, sleep=None, on_progress=None):
        Path(plan.output_dir).mkdir(parents=True, exist_ok=True)
        out = Path(plan.output_dir) / "fake-prompt-id_a.png"
        out.write_bytes(b"\x89PNG\x00fake-image")
        return ie.ImageResult(
            plan=plan,
            prompt_id="fake-prompt-id",
            image_paths=[str(out)],
            elapsed_sec=0.05,
            polls=2,
            bytes_total=len(b"\x89PNG\x00fake-image"),
            raw_history={"outputs": {"7": {"images": []}}},
        )

    monkeypatch.setattr(plugin_mod, "run_image", _fake_run_image)
    # Build ``ComfyClient`` instances normally — the fake ``run_image``
    # never actually calls them, but ``_check_server`` and ``_cancel``
    # do.  Stub those interactions so we don't open a real socket.
    real_init = comfy_client.ComfyClient.__init__
    monkeypatch.setattr(
        comfy_client.ComfyClient, "system_stats",
        AsyncMock(return_value={"devices": [{"vram_total": 1, "vram_free": 1}]}),
    )
    monkeypatch.setattr(
        comfy_client.ComfyClient, "cancel_task",
        AsyncMock(return_value=True),
    )

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


# ── on_load contract ──────────────────────────────────────────────────


def test_on_load_registers_documented_tools(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    names = {d["name"] for d in api.tool_defs}
    assert names == {
        "local_sd_flux_create",
        "local_sd_flux_status",
        "local_sd_flux_list",
        "local_sd_flux_cancel",
        "local_sd_flux_check_deps",
        "local_sd_flux_rank_providers",
    }


def test_on_load_logs_load_message(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msgs = [m for _, m in api.logs]
    assert any("loaded" in m for m in msgs)


def test_on_load_creates_task_manager_with_data_dir(
    plugin_factory, tmp_path: Path,
) -> None:
    p, _, _ = plugin_factory()
    assert p._tm is not None
    assert tmp_path in Path(p._tm.db_path).parents \
        or Path(p._tm.db_path).parent == tmp_path


# ── /healthz + /check-deps ────────────────────────────────────────────


def test_healthz_lists_presets(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/healthz").json()
        assert body["plugin"] == "local-sd-flux"
        assert "sd15_basic" in body["deps"]["presets"]
        assert body["deps"]["preset_count"] == 3


def test_check_deps_includes_default_base_url(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/check-deps").json()
        assert body["default_base_url"] == "http://127.0.0.1:8188"
        assert "sdxl_basic" in body["presets"]


def test_check_server_reports_ok_when_stats_ok(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/check-server").json()
        assert body["ok"] is True
        assert "devices" in body


def test_check_server_reports_failure_message(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import comfy_client
    monkeypatch.setattr(
        comfy_client.ComfyClient, "system_stats",
        AsyncMock(side_effect=RuntimeError("connection refused")),
    )
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/check-server").json()
        assert body["ok"] is False
        assert "connection" in body["message"].lower()


# ── /presets ──────────────────────────────────────────────────────────


def test_presets_endpoint_returns_three_specs(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/presets").json()
        assert len(body["items"]) == 3
        ids = {item["id"] for item in body["items"]}
        assert ids == {"sd15_basic", "sdxl_basic", "flux_basic"}


# ── /config ───────────────────────────────────────────────────────────


def test_config_get_returns_defaults(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/config").json()
        assert body["default_preset_id"] == "sdxl_basic"
        assert body["default_base_url"] == "http://127.0.0.1:8188"


def test_config_post_persists_overrides(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        c.post("/config", json={"default_preset_id": "flux_basic"})
        body = c.get("/config").json()
        assert body["default_preset_id"] == "flux_basic"


# ── /preview ──────────────────────────────────────────────────────────


def test_preview_returns_plan_summary(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        body = c.post("/preview", json={"prompt": "a wizard cat"}).json()
        assert body["plan"]["preset_id"] == "sdxl_basic"
        assert "presets" in body


def test_preview_rejects_unknown_preset(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview", json={"prompt": "x", "preset_id": "nope"})
        assert r.status_code == 400
        body = r.json()
        assert "detail" in body


def test_preview_rejects_empty_prompt(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        # Pydantic min_length=1 rejects with 422.
        r = c.post("/preview", json={"prompt": ""})
        assert r.status_code in (400, 422)


# ── /rank-providers ───────────────────────────────────────────────────


def test_rank_providers_endpoint(plugin_factory) -> None:
    _, _, app = plugin_factory()
    cands = [
        {"id": "a", "label": "A", "base_url": "http://a",
         "quality": 0.9, "speed": 0.9, "cost": 0.9, "reliability": 0.9,
         "control": 0.9, "latency": 0.9, "compatibility": 0.9},
        {"id": "b", "label": "B", "base_url": "http://b",
         "quality": 0.1, "speed": 0.1, "cost": 0.1, "reliability": 0.1,
         "control": 0.1, "latency": 0.1, "compatibility": 0.1},
    ]
    with TestClient(app) as c:
        body = c.post("/rank-providers", json={"candidates": cands}).json()
        assert body["items"][0]["label"] == "A"
        assert body["items"][0]["total"] > body["items"][1]["total"]


# ── /tasks (POST) ─────────────────────────────────────────────────────


def test_create_task_runs_through_mocked_pipeline(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"prompt": "a wizard cat"})
        assert r.status_code == 200, r.text
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded", rec
        assert rec["result"]["image_count"] == 1
        assert rec["result"]["bytes_total"] > 0
        assert rec["result"]["verification"]["verified"] is True


def test_create_task_rejects_empty_prompt(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"prompt": ""})
        assert r.status_code in (400, 422)


def test_create_task_records_failure_when_run_image_raises(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin as plugin_mod

    async def _boom(*_a, **_kw):
        raise RuntimeError("ComfyUI rejected workflow: missing checkpoint")

    monkeypatch.setattr(plugin_mod, "run_image", _boom)
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={"prompt": "x"}).json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "failed"
        assert rec["error_message"]


# ── /tasks (GET) ──────────────────────────────────────────────────────


def test_list_tasks_returns_recent_jobs(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        for _ in range(3):
            c.post("/tasks", json={"prompt": "abc"})
        body = c.get("/tasks").json()
        assert body["total"] >= 3


def test_get_task_returns_404_for_missing(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/does-not-exist")
        assert r.status_code == 404


def test_delete_task_removes_row(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={"prompt": "x"}).json()["task_id"]
        _wait_until_done(c, tid)
        r = c.delete(f"/tasks/{tid}")
        assert r.status_code == 200
        # Now the task is gone.
        assert c.get(f"/tasks/{tid}").status_code == 404


def test_cancel_task_returns_404_for_missing(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks/does-not-exist/cancel")
        assert r.status_code == 404


# ── /tasks/{id}/image/{idx} ────────────────────────────────────────────


def test_serve_image_streams_back_bytes(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={"prompt": "cat"}).json()["task_id"]
        _wait_until_done(c, tid)
        r = c.get(f"/tasks/{tid}/image/0")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")
        assert b"fake-image" in r.content


def test_serve_image_404_for_out_of_range(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={"prompt": "cat"}).json()["task_id"]
        _wait_until_done(c, tid)
        r = c.get(f"/tasks/{tid}/image/99")
        assert r.status_code == 404


def test_serve_image_404_for_missing_task(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/no-such/image/0")
        assert r.status_code == 404


# ── brain tool dispatcher ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brain_tool_check_deps_returns_summary(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    out = await api.tool_handler("local_sd_flux_check_deps", {})
    assert "presets" in out
    assert "default_base_url" in out


@pytest.mark.asyncio
async def test_brain_tool_rank_providers(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    out = await api.tool_handler("local_sd_flux_rank_providers", {
        "candidates": [
            {"id": "a", "label": "A", "base_url": "http://a",
             "quality": 0.9, "speed": 0.9, "cost": 0.9, "reliability": 0.9,
             "control": 0.9, "latency": 0.9, "compatibility": 0.9},
        ],
    })
    assert "A" in out


@pytest.mark.asyncio
async def test_brain_tool_status_for_missing(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    out = await api.tool_handler("local_sd_flux_status", {"task_id": "missing"})
    assert "未找到" in out or "未找到" in out  # Chinese label


@pytest.mark.asyncio
async def test_brain_tool_unknown_returns_message(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    out = await api.tool_handler("does_not_exist", {})
    assert "unknown tool" in out
