"""End-to-end-ish tests for ``plugin.Plugin`` (smart-poster-grid).

Strategy mirrors ``plugins/video-color-grade/tests/test_plugin.py``:
stand up a fake :class:`PluginAPI`, patch out the sibling
poster-maker renderer so the test never touches Pillow, then exercise
the HTTP routes via :class:`fastapi.testclient.TestClient`.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


# ── fake host ─────────────────────────────────────────────────────────────


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
    """Return a factory that builds ``(plugin, api, fastapi_app)``.

    Patches the sibling ``poster-maker`` engine so each render writes
    a tiny PNG-shaped file on disk — no Pillow, no fonts, no network.
    """
    import grid_engine
    from plugin import Plugin

    class FakeEngine:
        @staticmethod
        def render_poster(*, template, text_values, background_image, output_path):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
            return output_path

    monkeypatch.setattr(grid_engine, "_poster_maker_engine", lambda: FakeEngine)

    def make() -> tuple[Any, FakePluginAPI, FastAPI]:
        api = FakePluginAPI(tmp_path)
        p = Plugin()
        p.on_load(api)
        assert api.routes is not None
        app = FastAPI()
        app.include_router(api.routes)
        return p, api, app

    return make


def _wait_until_done(client: TestClient, tid: str, *, timeout_sec: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        r = client.get(f"/tasks/{tid}")
        assert r.status_code == 200
        rec = r.json()
        if rec["status"] in {"succeeded", "failed", "cancelled"}:
            return rec
        time.sleep(0.05)
    raise AssertionError(f"task {tid} did not finish within {timeout_sec}s")


# ── on_load contract ───────────────────────────────────────────────────────


def test_on_load_registers_documented_tools(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    names = {d["name"] for d in api.tool_defs}
    assert names == {
        "smart_poster_grid_create",
        "smart_poster_grid_status",
        "smart_poster_grid_list",
        "smart_poster_grid_cancel",
        "smart_poster_grid_ratios",
    }


def test_on_load_logs_loaded_line(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msgs = [m for _, m in api.logs]
    assert any("loaded" in m for m in msgs)


# ── /healthz / /ratios / /preview / /config ───────────────────────────────


def test_healthz_lists_four_ratios(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["plugin"] == "smart-poster-grid"
        ids = {x["id"] for x in body["ratios"]}
        assert ids == {"1x1", "3x4", "9x16", "16x9"}


def test_ratios_endpoint_returns_dimensions(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/ratios")
        assert r.status_code == 200
        body = r.json()
        sizes = {x["id"]: (x["width"], x["height"]) for x in body["ratios"]}
        assert sizes["9x16"] == (1080, 1920)


def test_preview_returns_plan_for_default_ratios(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview", json={"text_values": {"title": "T"}})
        assert r.status_code == 200
        body = r.json()
        assert len(body["plan"]["ratios"]) == 4


def test_preview_returns_400_on_bad_ratio(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview", json={"ratio_ids": ["666x666"]})
        assert r.status_code == 400
        assert isinstance(r.json()["detail"], dict)


def test_config_get_then_set_round_trips(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r0 = c.get("/config")
        assert r0.status_code == 200
        assert "default_ratios_csv" in r0.json()
        r1 = c.post("/config", json={"render_timeout_sec": 1200})
        assert r1.status_code == 200
        assert r1.json()["render_timeout_sec"] == "1200"


# ── /tasks lifecycle ───────────────────────────────────────────────────────


def test_create_task_runs_through_mocked_pipeline(
    plugin_factory, tmp_path: Path,
) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"text_values": {"title": "Hello"}})
        assert r.status_code == 200
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        # 4 ratios → 4 successful renders.
        assert rec["result"]["succeeded_count"] == 4
        assert rec["result"]["failed_count"] == 0
        verification = rec["result"]["verification"]
        assert verification["verifier_id"] == "smart_poster_grid_self_check"


def test_create_task_with_explicit_subset_only_renders_those_ratios(
    plugin_factory,
) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"ratio_ids": ["1x1", "9x16"]})
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        assert rec["result"]["succeeded_count"] == 2
        ids = [r["ratio_id"] for r in rec["result"]["renders"]]
        assert ids == ["1x1", "9x16"]


def test_create_task_blocks_unknown_ratio_id(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"ratio_ids": ["99x99"]})
        assert r.status_code == 400


def test_create_task_when_renderer_raises_marks_failed(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renderer crashes for ALL ratios → task fails fast (every render
    raises, so succeeded_count = 0).  Note: the worker still SUCCEEDS at
    the task level because individual ratio failures are non-fatal —
    the verification envelope is what flags the user."""
    import grid_engine

    class BoomEngine:
        @staticmethod
        def render_poster(**_kw):
            raise RuntimeError("renderer exploded")

    monkeypatch.setattr(grid_engine, "_poster_maker_engine", lambda: BoomEngine)

    _, _, app = plugin_factory()  # fixture already patched, but BoomEngine wins (latest patch)
    with TestClient(app) as c:
        r = c.post("/tasks", json={"text_values": {}})
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        # Task itself succeeds (every render is wrapped in try/except),
        # but the result must report 4 failures and verified=False.
        assert rec["status"] == "succeeded"
        assert rec["result"]["succeeded_count"] == 0
        assert rec["result"]["failed_count"] == 4
        assert rec["result"]["verification"]["verified"] is False


def test_get_task_returns_404_for_unknown(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/missing")
        assert r.status_code == 404
        assert isinstance(r.json()["detail"], dict)


def test_list_tasks_returns_items(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        c.post("/tasks", json={"text_values": {}})
        r = c.get("/tasks")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert body["total"] >= 1


def test_cancel_finished_task_returns_404(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"text_values": {}})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.post(f"/tasks/{tid}/cancel")
        assert r2.status_code == 404


def test_delete_task_then_get_returns_404(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"text_values": {}})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.delete(f"/tasks/{tid}")
        assert r2.status_code == 200
        r3 = c.get(f"/tasks/{tid}")
        assert r3.status_code == 404


def test_serve_poster_returns_file_for_each_ratio(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"text_values": {}})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        for rid in ["1x1", "3x4", "9x16", "16x9"]:
            r2 = c.get(f"/tasks/{tid}/poster/{rid}")
            assert r2.status_code == 200
            # Tiny PNG-shaped payload from our fake renderer.
            assert r2.content.startswith(b"\x89PNG")


def test_serve_poster_404_for_unknown_ratio(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"text_values": {}})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.get(f"/tasks/{tid}/poster/99x99")
        assert r2.status_code == 404


def test_serve_poster_404_for_unknown_task(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/missing/poster/1x1")
        assert r.status_code == 404


def test_result_blob_persists_renders_and_verification(
    plugin_factory,
) -> None:
    """Dual-storage pattern: the API-facing ``result`` blob must hold
    both the per-ratio renders and the verification envelope so the UI
    can render the gallery and the trust pill in one round-trip."""
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"text_values": {}})
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        renders = rec["result"]["renders"]
        assert len(renders) == 4
        for r in renders:
            assert r["output_path"] and Path(r["output_path"]).is_file()
        # verification pill ride-alongs in the same blob.
        verification = rec["result"]["verification"]
        assert verification["verified"] is True


def test_extra_columns_persist_typed_columns(plugin_factory) -> None:
    """Non-``_json`` extra columns ARE surfaced at the top-level of
    the task dict (SDK contract).  ``output_dir`` is the canonical
    handle — without it the UI can't link to the gallery folder."""
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"text_values": {}})
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec.get("output_dir"), "output_dir must be in extras"
        assert Path(rec["output_dir"]).is_dir()


# ── brain tools ────────────────────────────────────────────────────────────


def test_brain_tool_create_returns_short_string(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("smart_poster_grid_create", {"text_values": {}}),
    )
    assert "已创建" in msg


def test_brain_tool_status_returns_string_for_unknown(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("smart_poster_grid_status", {"task_id": "nope"}),
    )
    assert msg == "未找到该任务"


def test_brain_tool_list_handles_empty(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("smart_poster_grid_list", {}),
    )
    assert msg == "(空)"


def test_brain_tool_ratios_lists_all_four(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("smart_poster_grid_ratios", {}),
    )
    for rid in ("1x1", "3x4", "9x16", "16x9"):
        assert rid in msg


def test_brain_tool_unknown_tool_returns_diag(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("not_a_tool", {}),
    )
    assert "unknown tool" in msg
