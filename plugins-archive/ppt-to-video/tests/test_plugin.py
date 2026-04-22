"""End-to-end-ish tests for ``plugin.Plugin`` (ppt-to-video).

Mirrors the pattern of ``plugins/video-bg-remove/tests/test_plugin.py``:
stand up a fake :class:`PluginAPI`, patch every external dependency
(soffice, python-pptx, ffmpeg, TTS providers) so the worker pipeline
is fully deterministic, and drive the FastAPI routes via
:class:`fastapi.testclient.TestClient`.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

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
    """Build ``(plugin, api, fastapi_app, deck_path)`` with everything mocked.

    External deps replaced:

    * ``slide_engine.plan_video``   → returns a hand-built plan.
    * ``slide_engine.run_pipeline`` → writes a fake mp4, returns a result.
    * dep checks (``libreoffice_available`` / ``pptx_available`` /
      ``ffmpeg_available``) → all True by default.
    * ``Plugin._load_avatar_speaker_providers`` → returns ``None`` so we
      never hit the real TTS providers.
    """
    import slide_engine as se
    import plugin as plugin_mod
    from plugin import Plugin

    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"PK\x03\x04 fake")

    def _fake_plan_video(**kwargs):
        plan = se.SlidePlan(
            input_path=str(kwargs["input_path"]),
            output_path=str(kwargs["output_path"]),
            work_dir=str(kwargs["work_dir"]),
            voice=str(kwargs.get("voice", se.DEFAULT_VOICE)),
            tts_provider=str(kwargs.get("tts_provider", se.DEFAULT_TTS_PROVIDER)),
            silent_slide_sec=float(kwargs.get("silent_slide_sec", 2.0)),
            fps=int(kwargs.get("fps", 25)),
            crf=int(kwargs.get("crf", 20)),
            libx264_preset=str(kwargs.get("libx264_preset", "fast")),
            slides=[
                se.SlideMeta(index=1, image_path="i1.png", notes="hello"),
                se.SlideMeta(index=2, image_path="i2.png", notes=""),
            ],
        )
        return plan

    def _fake_run_pipeline(plan, *, tts_synth=None, ffmpeg_runner=None,
                           on_progress=None):
        Path(plan.output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(plan.output_path).write_bytes(b"\x00" * 64)
        return se.SlideVideoResult(
            plan=plan, output_path=plan.output_path,
            elapsed_sec=0.01, slide_count=plan.slide_count,
            audio_total_sec=3.0, output_size_bytes=64,
            tts_provider_used="injected" if tts_synth else "none",
            tts_fallbacks=plan.empty_notes_count,
        )

    monkeypatch.setattr(plugin_mod, "plan_video", _fake_plan_video)
    monkeypatch.setattr(plugin_mod, "run_pipeline", _fake_run_pipeline)
    monkeypatch.setattr(plugin_mod, "libreoffice_available", lambda: True)
    monkeypatch.setattr(plugin_mod, "pptx_available", lambda: True)
    monkeypatch.setattr(plugin_mod, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(plugin_mod, "resolve_libreoffice", lambda: "/usr/bin/soffice")

    monkeypatch.setattr(
        Plugin, "_load_avatar_speaker_providers", lambda self: None,
    )

    def _make() -> tuple[Any, FakePluginAPI, FastAPI, Path]:
        api = FakePluginAPI(tmp_path)
        p = Plugin()
        p.on_load(api)
        assert api.routes is not None
        app = FastAPI()
        app.include_router(api.routes)
        return p, api, app, deck

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
    _, api, _, _ = plugin_factory()
    names = {d["name"] for d in api.tool_defs}
    assert names == {
        "ppt_to_video_create",
        "ppt_to_video_status",
        "ppt_to_video_list",
        "ppt_to_video_cancel",
        "ppt_to_video_check_deps",
    }


def test_on_load_logs_load_message(plugin_factory) -> None:
    _, api, _, _ = plugin_factory()
    msgs = [m for _, m in api.logs]
    assert any("loaded" in m for m in msgs)


def test_on_load_creates_task_manager_with_data_dir(
    plugin_factory, tmp_path: Path,
) -> None:
    p, _, _, _ = plugin_factory()
    # Task manager points at get_data_dir(); the SQLite file is created
    # lazily on the first write — drive that through the public API.
    assert p._tm is not None
    assert tmp_path in Path(p._tm.db_path).parents or Path(p._tm.db_path).parent == tmp_path


# ── /healthz + /check-deps ────────────────────────────────────────────


def test_healthz_returns_dep_status(plugin_factory) -> None:
    _, _, app, _ = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["plugin"] == "ppt-to-video"
        deps = body["deps"]
        assert deps["soffice"] is True
        assert deps["pptx"] is True
        assert deps["ffmpeg"] is True


def test_check_deps_emits_install_hints_when_missing(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin as plugin_mod
    monkeypatch.setattr(plugin_mod, "resolve_libreoffice", lambda: None)
    monkeypatch.setattr(plugin_mod, "pptx_available", lambda: False)
    _, _, app, _ = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/check-deps").json()
        assert body["soffice"] is False
        assert body["pptx"] is False
        assert "LibreOffice" in body["soffice_install_hint"]
        assert "python-pptx" in body["pptx_install_hint"]


# ── /config ───────────────────────────────────────────────────────────


def test_config_get_returns_defaults(plugin_factory) -> None:
    _, _, app, _ = plugin_factory()
    with TestClient(app) as c:
        body = c.get("/config").json()
        assert body["default_voice"] == "zh-CN-XiaoxiaoNeural"
        assert body["default_silent_slide_sec"] == "2.0"


def test_config_post_persists_overrides(plugin_factory) -> None:
    _, _, app, _ = plugin_factory()
    with TestClient(app) as c:
        c.post("/config", json={"default_voice": "zh-CN-YunxiNeural"})
        body = c.get("/config").json()
        assert body["default_voice"] == "zh-CN-YunxiNeural"


# ── /preview ──────────────────────────────────────────────────────────


def test_preview_succeeds_without_running_pipeline(plugin_factory) -> None:
    _, _, app, _, = plugin_factory()
    _, _, app, deck = plugin_factory()  # rebuild to keep deck path
    with TestClient(app) as c:
        r = c.post("/preview", json={"input_path": str(deck)})
        assert r.status_code == 200
        body = r.json()
        assert body["plan"]["input_path"] == str(deck)
        assert body["plan"]["output_path"].endswith(".mp4")
        assert "deps" in body


def test_preview_rejects_unsupported_extension(plugin_factory, tmp_path: Path) -> None:
    _, _, app, _ = plugin_factory()
    bad = tmp_path / "doc.docx"
    bad.write_bytes(b"x")
    with TestClient(app) as c:
        r = c.post("/preview", json={"input_path": str(bad)})
        assert r.status_code == 400
        # ErrorCoach wraps the message into structured detail.
        body = r.json()
        assert "detail" in body


def test_preview_rejects_missing_file(plugin_factory, tmp_path: Path) -> None:
    _, _, app, _ = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview", json={"input_path": str(tmp_path / "nope.pptx")})
        assert r.status_code == 400


# ── /tasks (POST) ─────────────────────────────────────────────────────


def test_create_task_runs_through_mocked_pipeline(plugin_factory) -> None:
    _, _, app, deck = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": str(deck)})
        assert r.status_code == 200, r.text
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        assert rec["result"]["slide_count"] == 2
        assert rec["result"]["output_size_bytes"] == 64
        # 1/2 empty notes is exactly 50% — engine flags only > 50%, so green.
        assert rec["result"]["verification"]["verified"] is True
        assert rec["result"]["input_path"] == str(deck)


def test_create_task_requires_input_path(plugin_factory) -> None:
    _, _, app, _ = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"input_path": ""})
        assert r.status_code in (400, 422)  # pydantic vs gate may differ


def test_create_task_uses_custom_output_path(
    plugin_factory, tmp_path: Path,
) -> None:
    _, _, app, deck = plugin_factory()
    custom = tmp_path / "my-out.mp4"
    with TestClient(app) as c:
        r = c.post("/tasks", json={
            "input_path": str(deck), "output_path": str(custom),
        })
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        assert rec["result"]["output_path"] == str(custom)
        assert custom.is_file()


def test_create_task_records_failure_when_plan_raises(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin as plugin_mod
    def _boom(**_kwargs):
        raise RuntimeError("LibreOffice exited 1")
    monkeypatch.setattr(plugin_mod, "plan_video", _boom)
    _, _, app, deck = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={"input_path": str(deck)}).json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "failed"
        assert "exit" in rec["error_message"].lower() or rec["error_message"]


# ── /tasks (GET) ──────────────────────────────────────────────────────


def test_list_tasks_returns_recent_jobs(plugin_factory) -> None:
    _, _, app, deck = plugin_factory()
    with TestClient(app) as c:
        for _ in range(3):
            c.post("/tasks", json={"input_path": str(deck)})
        body = c.get("/tasks").json()
        assert body["total"] >= 3


def test_list_tasks_filters_by_status(plugin_factory) -> None:
    _, _, app, deck = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={"input_path": str(deck)}).json()["task_id"]
        _wait_until_done(c, tid)
        body = c.get("/tasks?status=succeeded").json()
        assert all(item["status"] == "succeeded" for item in body["items"])


def test_get_task_returns_404_for_unknown(plugin_factory) -> None:
    _, _, app, _ = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/does-not-exist")
        assert r.status_code == 404


# ── cancel + delete ───────────────────────────────────────────────────


def test_cancel_returns_404_for_finished_task(plugin_factory) -> None:
    _, _, app, deck = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={"input_path": str(deck)}).json()["task_id"]
        _wait_until_done(c, tid)
        r = c.post(f"/tasks/{tid}/cancel")
        assert r.status_code == 404


def test_delete_task_removes_record(plugin_factory) -> None:
    _, _, app, deck = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={"input_path": str(deck)}).json()["task_id"]
        _wait_until_done(c, tid)
        r = c.delete(f"/tasks/{tid}")
        assert r.status_code == 200
        assert c.get(f"/tasks/{tid}").status_code == 404


# ── /tasks/{id}/video ─────────────────────────────────────────────────


def test_serve_video_returns_file(plugin_factory) -> None:
    _, _, app, deck = plugin_factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={"input_path": str(deck)}).json()["task_id"]
        _wait_until_done(c, tid)
        r = c.get(f"/tasks/{tid}/video")
        assert r.status_code == 200
        assert r.headers["content-type"] == "video/mp4"
        assert len(r.content) == 64


def test_serve_video_404_when_no_output(plugin_factory) -> None:
    _, _, app, _ = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/missing/video")
        assert r.status_code == 404


# ── brain tool dispatcher ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_check_deps_renders_human_summary(plugin_factory) -> None:
    p, _, _, _ = plugin_factory()
    out = await p._handle_tool_call("ppt_to_video_check_deps", {})
    assert "soffice" in out.lower()
    assert "ffmpeg" in out.lower()


@pytest.mark.asyncio
async def test_tool_create_returns_chinese_confirmation(plugin_factory) -> None:
    p, _, _, deck = plugin_factory()
    out = await p._handle_tool_call(
        "ppt_to_video_create", {"input_path": str(deck)},
    )
    assert "任务" in out


@pytest.mark.asyncio
async def test_tool_status_returns_not_found_message(plugin_factory) -> None:
    p, _, _, _ = plugin_factory()
    out = await p._handle_tool_call(
        "ppt_to_video_status", {"task_id": "nope"},
    )
    assert "未找到" in out or "not found" in out.lower()


@pytest.mark.asyncio
async def test_tool_list_returns_blank_marker_when_empty(plugin_factory) -> None:
    p, _, _, _ = plugin_factory()
    out = await p._handle_tool_call("ppt_to_video_list", {})
    assert out == "(空)"


@pytest.mark.asyncio
async def test_unknown_tool_returns_marker(plugin_factory) -> None:
    p, _, _, _ = plugin_factory()
    out = await p._handle_tool_call("ppt_to_video_nope", {})
    assert "unknown tool" in out


# ── on_unload drain ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_unload_drains_workers(plugin_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio as _aio
    import plugin as plugin_mod
    # Slow down the pipeline so the worker is still running on unload.

    async def _slow_to_thread(fn, *args, **kwargs):
        await _aio.sleep(0.5)
        return fn(*args, **kwargs)

    monkeypatch.setattr(plugin_mod.asyncio, "to_thread", _slow_to_thread)

    p, _, app, deck = plugin_factory()
    with TestClient(app) as c:
        c.post("/tasks", json={"input_path": str(deck)})
    await p.on_unload()
    assert p._workers == {}
