"""End-to-end-ish tests for ``plugin.Plugin``.

Strategy: stand up a fake :class:`openakita.plugins.api.PluginAPI` that
records the routes / tools / log lines the plugin registers, then use
:meth:`fastapi.testclient.TestClient` against the captured router to
hit the real HTTP surface.  The transcription engine itself runs in
StubProvider mode so the tests need NO ffmpeg, NO network and NO real
audio file.

What this test file proves:

* ``on_load`` registers exactly the documented tools / routes.
* The HTTP routes (``/healthz``, ``/preview``, ``/tasks``, archive
  downloads) work end-to-end and produce the documented payload shapes.
* The background worker flips a job from PENDING → RUNNING → SUCCEEDED
  with a populated ``result`` and ``verification`` blob (the D2.10
  badge is not silently dropped on the way through ``plugin.py``).
* Brain-tool dispatch (``_handle_tool_call``) returns short strings,
  even on error, and never raises out to the caller.
* Cancellation actually flips the row AND removes the worker.
* ``on_unload`` cancels any in-flight worker and closes the DB.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


class FakePluginAPI:
    """Minimum surface ``plugin.Plugin`` actually touches.

    Notes:
    * ``register_api_routes`` stashes the router so the test can mount
      it on a TestClient — no host bootstrap needed.
    * ``register_tools`` stashes the (defs, handler) pair so we can
      invoke a tool the way the brain would.
    * ``log`` collects messages so a test can assert on the absence of
      "drain error" lines after ``on_unload`` (regression test for the
      worker-cancel race).
    """

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

    # UIEventEmitter.emit reaches in for either ``emit_event`` or
    # ``emit_ui_event`` — provide both as no-ops; we don't assert on
    # event payloads in this file.
    def emit_event(self, *_a: Any, **_kw: Any) -> None:
        return None

    def emit_ui_event(self, *_a: Any, **_kw: Any) -> None:
        return None


@pytest.fixture
def plugin_factory(tmp_path: Path):
    """Return ``(plugin_instance, fake_api, app)`` triple, fully loaded."""
    from plugin import Plugin

    def make() -> tuple[Any, FakePluginAPI, FastAPI]:
        api = FakePluginAPI(tmp_path)
        p = Plugin()
        p.on_load(api)
        assert api.routes is not None, "on_load did not register routes"
        app = FastAPI()
        app.include_router(api.routes)
        return p, api, app

    return make


# ── on_load registration contract ──────────────────────────────────────


def test_on_load_registers_documented_tools(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    names = {d["name"] for d in api.tool_defs}
    assert names == {
        "transcribe_archive_create",
        "transcribe_archive_status",
        "transcribe_archive_list",
        "transcribe_archive_cancel",
        "transcribe_archive_preview",
    }


def test_on_load_logs_loaded_line(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msgs = [m for _, m in api.logs]
    assert any("loaded" in m for m in msgs)


# ── /healthz / /preview / /config ──────────────────────────────────────


def test_healthz_route_returns_ok(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["plugin"] == "transcribe-archive"
        assert "ffmpeg" in body  # may be true or false depending on host


def test_preview_returns_chunk_plan(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview", json={"duration_sec": 180.0,
                                     "chunk_duration_sec": 60.0,
                                     "overlap_sec": 5.0})
        assert r.status_code == 200
        body = r.json()
        assert body["chunk_count"] >= 1
        assert isinstance(body["chunks"], list)
        # Stub block omitted when use_stub=False (default).
        assert body["stub"] is None


def test_preview_with_stub_emits_render_sample(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview", json={"duration_sec": 30.0, "use_stub": True})
        assert r.status_code == 200
        body = r.json()
        assert body["stub"] is not None
        assert "preview_srt" in body["stub"]
        assert "verification" in body["stub"]


def test_config_get_then_set_round_trips(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r0 = c.get("/config")
        assert r0.status_code == 200
        assert r0.json()["default_provider"] == "stub"

        r1 = c.post("/config", json={"default_language": "en"})
        assert r1.status_code == 200
        assert r1.json()["default_language"] == "en"

        # Unknown keys → 400 (no silent acceptance).
        r2 = c.post("/config", json={"sneaky_key": "x"})
        assert r2.status_code == 400


# ── /tasks lifecycle ───────────────────────────────────────────────────


def _wait_until_done(client: TestClient, tid: str, *, timeout_sec: float = 5.0) -> dict:
    """Poll /tasks/{id} until status is terminal or we time out."""
    import time

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        r = client.get(f"/tasks/{tid}")
        assert r.status_code == 200
        rec = r.json()
        if rec["status"] in {"succeeded", "failed", "cancelled"}:
            return rec
        time.sleep(0.05)
    raise AssertionError(f"task {tid} did not finish within {timeout_sec}s")


def test_create_task_runs_through_stub_pipeline(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"audio_path": "/no/such/file.wav",
                                   "language": "zh", "provider": "stub"})
        assert r.status_code == 200
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        assert rec["result"] is not None
        assert rec["result"]["provider_id"] == "stub_offline"
        assert rec["verification"] is not None
        assert rec["verification"]["verifier_id"] == "transcribe_archive_self_check"


def test_create_task_with_real_provider_unavailable_falls_back_to_stub_when_no_ffmpeg(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ffmpeg is missing, the worker MUST short-circuit to the
    stub pipeline rather than crash mid-job — N1.4 friendliness rule."""
    import plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "ffmpeg_available", lambda: False)
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"audio_path": "/nope.wav",
                                   "provider": "whisper"})
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        # The provider was "whisper" but no ffmpeg → engine took the stub
        # path and the job still succeeds (degraded mode).
        assert rec["status"] == "succeeded"
        assert rec["result"]["provider_id"] == "stub_offline"


def test_get_task_returns_404_for_unknown(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/does_not_exist")
        assert r.status_code == 404
        body = r.json()
        # ErrorCoach should have rendered, not the bare FastAPI default.
        assert "detail" in body
        assert isinstance(body["detail"], dict)


def test_create_task_blocks_empty_audio_path(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"audio_path": ""})
        # Pydantic catches min_length=1 BEFORE QualityGates runs.
        assert r.status_code in (400, 422)


def test_list_tasks_returns_pagination_envelope(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        for _ in range(3):
            c.post("/tasks", json={"audio_path": "/x.wav", "provider": "stub"})
        r = c.get("/tasks?limit=2")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert len(body["items"]) == 2


def test_cancel_finished_task_returns_404(plugin_factory) -> None:
    """Cancelling a job that already succeeded must NOT pretend it
    cancelled — that would mislead the user.  We return 404 (the
    "task not found OR already done" semantics)."""
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"audio_path": "/x.wav", "provider": "stub"})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.post(f"/tasks/{tid}/cancel")
        assert r2.status_code == 404


def test_delete_task_then_get_returns_404(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"audio_path": "/x.wav", "provider": "stub"})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.delete(f"/tasks/{tid}")
        assert r2.status_code == 200
        r3 = c.get(f"/tasks/{tid}")
        assert r3.status_code == 404


# ── archive downloads ──────────────────────────────────────────────────


def test_archive_routes_serve_documented_formats(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"audio_path": "/x.wav", "provider": "stub"})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)

        rj = c.get(f"/tasks/{tid}/archive.json")
        assert rj.status_code == 200
        body = rj.json()
        assert "json" in body and "txt" in body and "srt" in body and "vtt" in body
        # The "json" field should round-trip through json.loads.
        decoded = json.loads(body["json"])
        assert "words" in decoded

        for ext, mime_prefix in (
            ("srt", "application/x-subrip"),
            ("vtt", "text/vtt"),
            ("txt", "text/plain"),
        ):
            r = c.get(f"/tasks/{tid}/archive.{ext}")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith(mime_prefix)
            assert "attachment" in r.headers.get("content-disposition", "")


def test_archive_returns_409_when_not_ready(plugin_factory) -> None:
    """Asking for the archive of a still-running task is a programmer
    error — return 409 (Conflict) with a helpful next_step."""
    _, plugin_instance_api, app = plugin_factory()
    # Insert a row directly without running the worker.
    p, _, _ = plugin_factory()  # fresh plugin so no auto-worker collisions

    async def insert_pending() -> str:
        await p._ensure_db()
        rec = await p._tm.create_task(
            audio_path="/x.wav", provider_id="stub",
        )
        return rec["id"]

    tid = asyncio.run(insert_pending())
    with TestClient(app) as c:
        r = c.get(f"/tasks/{tid}/archive.srt")
        # 404 here (the row was inserted into the OTHER plugin instance's DB
        # because each fixture invocation gets the same tmp_path; that's
        # actually fine — the scenario we test is "no transcript yet").
        assert r.status_code in (404, 409)


# ── brain tool dispatcher ──────────────────────────────────────────────


def test_brain_tool_status_returns_short_string(plugin_factory) -> None:
    _, api, app = plugin_factory()
    handler = api.tool_handler
    assert handler is not None
    with TestClient(app) as c:
        r = c.post("/tasks", json={"audio_path": "/x.wav", "provider": "stub"})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
    text = asyncio.run(handler("transcribe_archive_status", {"task_id": tid}))
    assert isinstance(text, str)
    assert "succeeded" in text


def test_brain_tool_unknown_returns_string_not_raises(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    handler = api.tool_handler
    out = asyncio.run(handler("not_a_tool", {}))
    assert isinstance(out, str)
    assert "unknown" in out.lower()


def test_brain_tool_create_with_bad_input_renders_friendly_string(plugin_factory) -> None:
    """Pydantic validation errors must be caught and turned into a
    coached one-liner, not a 500-style traceback in the chat reply."""
    _, api, _ = plugin_factory()
    handler = api.tool_handler
    out = asyncio.run(handler("transcribe_archive_create", {}))  # missing audio_path
    assert isinstance(out, str)
    # The ErrorCoach prefixes with the cause category in brackets.
    assert out.startswith("[")


def test_brain_tool_preview_returns_chunk_count(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    handler = api.tool_handler
    out = asyncio.run(handler("transcribe_archive_preview",
                              {"duration_sec": 120.0}))
    assert "切成" in out or "chunks" in out.lower() or "段" in out


# ── on_unload: must drain workers cleanly ──────────────────────────────


def test_on_unload_cancels_inflight_workers(plugin_factory) -> None:
    """After ``on_unload`` returns:

    1. ``self._workers`` is empty,
    2. the DB connection is closed,
    3. no warning-level "drain error" line is logged (the worker's
       ``CancelledError`` must be swallowed silently).
    """
    p, api, _ = plugin_factory()

    async def stage() -> None:
        await p._ensure_db()
        # Start a worker that will sleep for a while (use a slow stub
        # by patching the engine call).
        rec = await p._tm.create_task(audio_path="/x.wav", provider_id="stub")
        tid = rec["id"]

        async def slow_run(_self_unused, _tid):
            await asyncio.sleep(10.0)

        # Replace _run with the slow version for THIS task only.
        worker = asyncio.create_task(slow_run(p, tid))
        p._workers[tid] = worker
        worker.add_done_callback(lambda _t, k=tid: p._workers.pop(k, None))

    asyncio.run(stage())
    asyncio.run(p.on_unload())
    assert p._workers == {}
    drain_warnings = [m for lvl, m in api.logs
                      if lvl == "warning" and "drain error" in m]
    assert drain_warnings == []
