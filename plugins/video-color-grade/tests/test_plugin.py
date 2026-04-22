"""End-to-end-ish tests for ``plugin.Plugin`` (video-color-grade).

Strategy mirrors ``plugins/bgm-mixer/tests/test_plugin.py``: stand up a
fake :class:`PluginAPI`, patch out every ffmpeg / ffprobe sub-call so
the test never touches the system, then exercise the HTTP routes via
:class:`fastapi.testclient.TestClient`.

Coverage:

* ``on_load`` registers the documented tools / routes.
* ``/healthz`` reports ffmpeg availability (we patch the probe on).
* ``/preview`` returns a plan + ffmpeg argv for both auto and preset modes.
* ``/tasks`` lifecycle: create → run (mocked) → succeeded record carries
  ``output_path`` + ``verification`` in ``result``.
* ``/tasks/{id}/video`` serves the encoded file once succeeded.
* When the worker's ``apply_grade`` raises (simulated ffmpeg failure),
  the task lands in FAILED with an ErrorCoach-rendered message.
* Brain tools return short strings (never raise).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from openakita_plugin_sdk.contrib import FFmpegResult, GradeStats


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

    Patches every external dependency so the worker pipeline is fully
    deterministic:

    * ``probe_video_duration_sec`` → 30s
    * ``sample_signalstats_sync``  → balanced 8-bit stats
    * ``run_ffmpeg_sync``           → drops a 16-byte fake file at the
      output path so the post-render stat() succeeds
    * ``ffmpeg_available``          → True
    """
    from plugin import Plugin
    import plugin as plugin_mod
    import grade_engine

    monkeypatch.setattr(grade_engine, "probe_video_duration_sec", lambda *a, **k: 30.0)
    monkeypatch.setattr(plugin_mod, "probe_video_duration_sec", lambda *a, **k: 30.0)
    monkeypatch.setattr(plugin_mod, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(grade_engine, "resolve_binary", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        grade_engine, "sample_signalstats_sync",
        lambda *a, **k: GradeStats(
            y_mean=0.5, y_range=0.72, sat_mean=0.25, bit_depth=8, samples=10,
        ),
    )

    def fake_run_ffmpeg(cmd, *, timeout_sec, check=True, capture=True, input_bytes=None):
        # Worker writes "graded.mp4" under data_dir/outputs/<id>/ — find
        # the output path (last argv) and create the file so apply_grade
        # can stat() it.
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00" * 16)
        return FFmpegResult(cmd=list(cmd), returncode=0, stdout="", stderr="", duration_sec=1.5)

    monkeypatch.setattr(grade_engine, "run_ffmpeg_sync", fake_run_ffmpeg)

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
        "video_color_grade_create",
        "video_color_grade_status",
        "video_color_grade_list",
        "video_color_grade_cancel",
        "video_color_grade_preview",
    }


def test_on_load_logs_loaded_line(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msgs = [m for _, m in api.logs]
    assert any("loaded" in m for m in msgs)


# ── /healthz / /preview / /config ──────────────────────────────────────────


def test_healthz_lists_modes(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["plugin"] == "video-color-grade"
        assert body["ffmpeg"] is True
        assert "auto" in body["modes"]
        assert "preset:warm_cinematic" in body["modes"]


def test_preview_auto_mode_returns_plan_and_cmd(plugin_factory, tmp_path: Path) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/preview", json={"input_path": str(src)})
        assert r.status_code == 200
        body = r.json()
        assert body["plan"]["mode"] == "auto"
        assert body["ffmpeg_cmd"][0].endswith("ffmpeg")
        assert "auto" in body["available_modes"]


def test_preview_preset_mode_skips_sampling(
    plugin_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preset mode must NOT call sample_signalstats."""
    import grade_engine

    def must_not_call(*a, **k):
        raise AssertionError("preset mode must not sample")

    monkeypatch.setattr(grade_engine, "sample_signalstats_sync", must_not_call)

    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/preview", json={
            "input_path": str(src), "mode": "preset:warm_cinematic",
        })
        assert r.status_code == 200
        assert "colorbalance" in r.json()["plan"]["filter_string"]


def test_config_get_then_set_round_trips(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r0 = c.get("/config")
        assert r0.status_code == 200
        assert r0.json()["default_clamp_pct"] == "0.08"
        r1 = c.post("/config", json={"default_clamp_pct": 0.05})
        assert r1.status_code == 200
        assert r1.json()["default_clamp_pct"] == "0.05"


# ── /tasks lifecycle ───────────────────────────────────────────────────────


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
        # Output path lives in extras (subclass column) AND in the API
        # ``result`` payload (dual-storage pattern, matches bgm-mixer).
        assert rec.get("output_path", "").endswith("graded.mp4")
        verification = rec["result"]["verification"]
        assert verification["verifier_id"] == "video_color_grade_self_check"
        assert "plan" in rec["result"]


def test_create_task_preset_mode_persists_filter(
    plugin_factory, tmp_path: Path,
) -> None:
    _, _, app = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    with TestClient(app) as c:
        r = c.post("/tasks", json={
            "input_path": str(src), "mode": "preset:warm_cinematic",
        })
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        plan = rec["result"]["plan"]
        assert plan["mode"] == "preset:warm_cinematic"
        assert "colorbalance" in plan["filter_string"]


def test_create_task_blocks_empty_input_path(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": ""})
        # Pydantic catches min_length=1 first — 422 is the FastAPI default.
        assert r.status_code in (400, 422)


def test_create_task_with_missing_file_fails_gracefully(
    plugin_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the input file isn't on disk AND ffprobe says duration=0,
    the worker must record a FAILED status, never hang in RUNNING."""
    import plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "probe_video_duration_sec", lambda *a, **k: 0.0)
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": str(tmp_path / "missing.mp4")})
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "failed"
        assert rec.get("error_message")


def test_create_task_when_ffmpeg_raises_marks_failed(
    plugin_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import grade_engine

    def boom(_plan, **_kw):
        raise RuntimeError("ffmpeg blew up")

    monkeypatch.setattr(grade_engine, "apply_grade", boom)
    monkeypatch.setattr("plugin.apply_grade", boom)

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
        # 16-byte fake payload from our run_ffmpeg stub.
        assert len(r2.content) == 16


def test_serve_video_404_when_no_output(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/nope/video")
        assert r.status_code == 404


# ── brain tools ────────────────────────────────────────────────────────────


def test_brain_tool_create_returns_short_string(plugin_factory, tmp_path: Path) -> None:
    plugin, api, _ = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    import asyncio

    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("video_color_grade_create", {"input_path": str(src)}),
    )
    assert "已创建" in msg


def test_brain_tool_status_returns_string_for_unknown(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    import asyncio

    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("video_color_grade_status", {"task_id": "nope"}),
    )
    assert msg == "未找到该任务"


def test_brain_tool_list_handles_empty(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    import asyncio

    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("video_color_grade_list", {}),
    )
    assert msg == "(空)"


def test_brain_tool_preview_returns_filter(plugin_factory, tmp_path: Path) -> None:
    _, api, _ = plugin_factory()
    src = tmp_path / "v.mp4"
    src.write_bytes(b"fake")
    import asyncio

    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("video_color_grade_preview", {"input_path": str(src)}),
    )
    assert "auto" in msg or "无须" in msg


def test_brain_tool_unknown_tool_returns_diag(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    import asyncio

    msg = asyncio.get_event_loop().run_until_complete(
        api.tool_handler("not_a_tool", {}),
    )
    assert "unknown tool" in msg
