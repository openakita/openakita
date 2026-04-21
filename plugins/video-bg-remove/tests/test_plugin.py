"""End-to-end-ish tests for ``plugin.Plugin`` (video-bg-remove).

Strategy mirrors ``plugins/video-color-grade/tests/test_plugin.py``:
stand up a fake :class:`PluginAPI`, patch the heavy parts (RVM
session, ffmpeg writer, frame iterator, video probe), then exercise
the HTTP routes via :class:`fastapi.testclient.TestClient`.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
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

    Patches every external dependency so the worker pipeline is
    deterministic and never touches ffmpeg / onnxruntime / disk I/O
    beyond the temp dir.
    """
    import matting_engine as me
    from plugin import Plugin
    import plugin as plugin_mod

    monkeypatch.setattr(
        me, "probe_video_meta",
        lambda *_a, **_kw: {"fps": 25.0, "width": 4, "height": 4, "duration_sec": 0.4},
    )
    monkeypatch.setattr(
        plugin_mod, "ffmpeg_available", lambda: True,
    )
    monkeypatch.setattr(
        plugin_mod, "onnxruntime_available", lambda: True,
    )
    monkeypatch.setattr(
        plugin_mod, "model_available", lambda _p: True,
    )

    def fake_iter(input_path, *, fps, width, height):
        for _ in range(int(fps * 0.4)):  # 10 frames @ 25fps for 0.4s
            yield np.zeros((height, width, 3), dtype=np.uint8)

    monkeypatch.setattr(me, "iter_video_frames", fake_iter)

    class FakeSession:
        def run(self, _outputs, inputs):
            src = inputs["src"]
            h, w = src.shape[2], src.shape[3]
            fgr = np.full((1, 3, h, w), 0.5, dtype=np.float32)
            pha = np.full((1, 1, h, w), 0.7, dtype=np.float32)
            rec = [np.zeros([1, 1, 1, 1], dtype=np.float32) for _ in range(4)]
            return [fgr, pha, *rec]

    monkeypatch.setattr(me, "load_rvm_session", lambda *_a, **_kw: FakeSession())

    # Replace the ffmpeg writer with a path-based one that touches the
    # output file so .stat() succeeds, but never spawns ffmpeg.
    def fake_open_writer(plan):
        Path(plan.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(plan.output_path).write_bytes(b"\x00" * 32)

        def _write(_arr) -> None:
            return None

        return _write, None

    def fake_close_writer(_proc, _plan) -> None:
        return None

    monkeypatch.setattr(me, "_open_ffmpeg_writer", fake_open_writer)
    monkeypatch.setattr(me, "_close_ffmpeg_writer", fake_close_writer)

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
        "video_bg_remove_create",
        "video_bg_remove_status",
        "video_bg_remove_list",
        "video_bg_remove_cancel",
        "video_bg_remove_check_deps",
    }


def test_on_load_logs_loaded_line(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msgs = [m for _, m in api.logs]
    assert any("loaded" in m for m in msgs)


# ── /healthz / /check-deps ─────────────────────────────────────────────


def test_healthz_reports_dep_status(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["plugin"] == "video-bg-remove"
        deps = body["deps"]
        assert deps["onnxruntime"] is True
        assert deps["ffmpeg"] is True
        assert deps["model_present"] is True


def test_check_deps_returns_hint_when_model_missing(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin as plugin_mod
    monkeypatch.setattr(plugin_mod, "model_available", lambda _p: False)

    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/check-deps")
        assert r.status_code == 200
        body = r.json()
        assert body["model_present"] is False
        assert "Download" in (body["model_download_hint"] or "")


def test_config_get_then_set_round_trips(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r0 = c.get("/config")
        assert r0.status_code == 200
        assert r0.json()["default_background_kind"] == "color"
        r1 = c.post("/config", json={"default_downsample_ratio": 0.5})
        assert r1.status_code == 200
        assert r1.json()["default_downsample_ratio"] == "0.5"


# ── /preview ───────────────────────────────────────────────────────────


def test_preview_returns_plan_and_deps(plugin_factory, tmp_path: Path) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/preview", json={"input_path": str(src)})
        assert r.status_code == 200
        body = r.json()
        assert body["plan"]["fps"] == 25.0
        assert body["plan"]["background"]["kind"] == "color"
        assert body["deps"]["onnxruntime"] is True


def test_preview_400_for_bad_background(plugin_factory, tmp_path: Path) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/preview", json={
            "input_path": str(src),
            "background": {"kind": "image", "image_path": str(tmp_path / "missing.png")},
        })
        assert r.status_code == 400


# ── /tasks lifecycle ───────────────────────────────────────────────────


def test_create_task_runs_through_mocked_pipeline(
    plugin_factory, tmp_path: Path,
) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": str(src)})
        assert r.status_code == 200
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        # Output path lives in extras (subclass column) AND in result.
        assert rec.get("output_path", "").endswith("matted.mp4")
        verification = rec["result"]["verification"]
        assert verification["verifier_id"] == "video_bg_remove_self_check"
        # mean_alpha should reflect our forced 0.7 fake.
        assert 0.65 <= rec["result"]["mean_alpha"] <= 0.75


def test_create_task_transparent_uses_mov_extension(
    plugin_factory, tmp_path: Path,
) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/tasks", json={
            "input_path": str(src),
            "background": {"kind": "transparent"},
        })
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        assert rec.get("output_path", "").endswith("matted.mov")


def test_create_task_blocks_empty_input_path(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": ""})
        assert r.status_code in (400, 422)


def test_create_task_when_run_matting_raises_marks_failed(
    plugin_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import matting_engine as me
    monkeypatch.setattr(me, "run_matting", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("plugin.run_matting", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": str(src)})
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "failed"
        assert rec.get("error_message")


def test_get_task_returns_404_for_unknown(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/missing")
        assert r.status_code == 404
        assert isinstance(r.json()["detail"], dict)


def test_list_tasks_returns_items(plugin_factory, tmp_path: Path) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        c.post("/tasks", json={"input_path": str(src)})
        r = c.get("/tasks")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert body["total"] >= 1


def test_cancel_finished_task_returns_404(plugin_factory, tmp_path: Path) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": str(src)})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.post(f"/tasks/{tid}/cancel")
        assert r2.status_code == 404


def test_delete_task_then_get_returns_404(plugin_factory, tmp_path: Path) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": str(src)})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.delete(f"/tasks/{tid}")
        assert r2.status_code == 200
        r3 = c.get(f"/tasks/{tid}")
        assert r3.status_code == 404


def test_serve_video_returns_file(plugin_factory, tmp_path: Path) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": str(src)})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.get(f"/tasks/{tid}/video")
        assert r2.status_code == 200
        assert len(r2.content) == 32  # bytes from fake_open_writer


def test_serve_video_404_when_no_output(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/nope/video")
        assert r.status_code == 404


# ── brain tools ────────────────────────────────────────────────────────


def test_brain_tool_create_returns_short_string(plugin_factory, tmp_path: Path) -> None:
    _, api, _ = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("video_bg_remove_create", {"input_path": str(src)}),
    )
    assert "已创建" in msg


def test_brain_tool_status_returns_string_for_unknown(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("video_bg_remove_status", {"task_id": "nope"}),
    )
    assert msg == "未找到该任务"


def test_brain_tool_list_handles_empty(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("video_bg_remove_list", {}),
    )
    assert msg == "(空)"


def test_brain_tool_check_deps_summarizes_status(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("video_bg_remove_check_deps", {}),
    )
    assert "onnxruntime" in msg
    assert "ffmpeg" in msg
    assert "model" in msg


def test_brain_tool_unknown_tool_returns_diag(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("not_a_tool", {}),
    )
    assert "unknown tool" in msg
