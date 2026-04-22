"""End-to-end-ish tests for ``plugin.Plugin`` (bgm-mixer).

Strategy: stand up a fake :class:`openakita.plugins.api.PluginAPI`,
patch out ffmpeg + ffprobe so the test never touches the system, then
exercise the HTTP routes via :class:`fastapi.testclient.TestClient`.

What this file proves:

* ``on_load`` registers the documented tools / routes.
* ``/healthz`` reports ffmpeg + madmom availability honestly (we
  patch both).
* ``/preview`` builds a plan + ffmpeg command without touching the
  system.
* ``/tasks`` lifecycle: create → run (mocked mix) → succeeded record
  carries ``output_path`` + ``verification_json``.
* When ``mix_tracks`` raises (simulated ffmpeg failure), the worker
  marks the task FAILED and the error is rendered through
  :class:`ErrorCoach` (not a bare traceback).
* Brain tools return short user-facing strings (never raise).
* ``on_unload`` cancels in-flight workers cleanly.
"""

from __future__ import annotations

import asyncio
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
def plugin_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Return ``(plugin_instance, fake_api, app)`` triple, fully loaded.

    Every call also patches:
    * ``_safe_duration`` → returns canned durations (no ffprobe call)
    * ``mix_tracks``     → returns a fake :class:`MixResult` (no ffmpeg)
    """
    from plugin import Plugin
    import plugin as plugin_mod
    from mixer_engine import MixResult

    def fake_safe_duration(media_path: str, *, fallback: float = 0.0) -> float:
        # Voice files end in 'voice'/'.wav', bgm files end in 'bgm'/'.mp3' —
        # callers can override per-test by patching this fixture again.
        if "voice" in str(media_path):
            return 8.0
        if "bgm" in str(media_path):
            return 30.0
        return fallback

    def fake_mix_tracks(plan, *, output_path, timeout_sec=600.0):
        # Touch the output so file-existence checks succeed.
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"\x00" * 16)
        return MixResult(
            plan=plan,
            output_path=str(output_path),
            duration_sec=plan.bgm_trim_end_sec - plan.bgm_trim_start_sec,
            ffmpeg_cmd=["ffmpeg", "-y", "fake"],
            used_madmom=False,
            voice_active_ratio=0.6,
            snap_max_distance_sec=0.05,
            looped=plan.bgm_loop_count > 1,
        )

    monkeypatch.setattr(plugin_mod, "_safe_duration", fake_safe_duration)
    monkeypatch.setattr(plugin_mod, "mix_tracks", fake_mix_tracks)
    monkeypatch.setattr(plugin_mod, "ffmpeg_available", lambda: True)

    def make() -> tuple[Any, FakePluginAPI, FastAPI]:
        api = FakePluginAPI(tmp_path)
        p = Plugin()
        p.on_load(api)
        assert api.routes is not None
        app = FastAPI()
        app.include_router(api.routes)
        return p, api, app

    return make


# ── on_load contract ───────────────────────────────────────────────────


def test_on_load_registers_documented_tools(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    names = {d["name"] for d in api.tool_defs}
    assert names == {
        "bgm_mixer_create",
        "bgm_mixer_status",
        "bgm_mixer_list",
        "bgm_mixer_cancel",
        "bgm_mixer_preview",
    }


def test_on_load_logs_loaded_line(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    msgs = [m for _, m in api.logs]
    assert any("loaded" in m for m in msgs)


# ── /healthz / /preview / /config ──────────────────────────────────────


def test_healthz_reports_dependency_availability(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["plugin"] == "bgm-mixer"
        assert body["ffmpeg"] is True  # patched on
        # madmom may be either depending on host — only assert key present
        assert "madmom" in body


def test_preview_returns_plan_and_ffmpeg_cmd(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview", json={"voice_path": "voice.wav",
                                     "bgm_path": "bgm.mp3"})
        assert r.status_code == 200
        body = r.json()
        assert "plan" in body and "ffmpeg_cmd" in body
        assert body["plan"]["bgm_loop_count"] >= 1
        assert body["ffmpeg_cmd"][0] == "ffmpeg"


def test_preview_with_words_creates_sentences(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/preview", json={
            "voice_path": "voice.wav", "bgm_path": "bgm.mp3",
            "words": [{"text": "a", "start": 0.0, "end": 0.5},
                      {"text": "b", "start": 1.0, "end": 1.5}],
        })
        assert r.status_code == 200
        sents = r.json()["plan"]["sentences"]
        assert len(sents) >= 1


def test_config_get_then_set_round_trips(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r0 = c.get("/config")
        assert r0.status_code == 200
        assert r0.json()["default_duck_db"] == "-10"
        r1 = c.post("/config", json={"default_duck_db": -8})
        assert r1.status_code == 200
        assert r1.json()["default_duck_db"] == "-8"


# ── /tasks lifecycle ───────────────────────────────────────────────────


def _wait_until_done(client: TestClient, tid: str, *, timeout_sec: float = 5.0) -> dict:
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


def test_create_task_runs_through_mocked_pipeline(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"voice_path": "voice.wav",
                                   "bgm_path": "bgm.mp3"})
        assert r.status_code == 200
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        # output_path is in extras (subclass column).
        assert rec.get("output_path", "").endswith("mix.mp3")
        # Verification badge is surfaced via the ``result`` dict (the
        # API-facing payload); the plain ``verification_json`` column
        # is filtered out of TaskRecord.extra by BaseTaskManager.
        verification = rec["result"]["verification"]
        assert verification["verifier_id"] == "bgm_mixer_self_check"


def test_create_task_with_words_drives_ducking(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={
            "voice_path": "voice.wav", "bgm_path": "bgm.mp3",
            "words": [{"text": "hi", "start": 0.0, "end": 1.0}],
        })
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        plan = rec["result"]["plan"]
        assert plan["sentences"]


def test_create_task_with_missing_voice_file_fails_gracefully(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the voice file isn't on disk, the worker must record a
    FAILED status with a coached error — never let the task hang in
    RUNNING."""
    import plugin as plugin_mod

    monkeypatch.setattr(
        plugin_mod, "_safe_duration",
        lambda media_path, *, fallback=0.0: 0.0,
    )
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"voice_path": "voice.wav",
                                   "bgm_path": "bgm.mp3"})
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "failed"
        # ErrorCoach renders into result.error, status surfaces a problem.
        assert rec.get("error_message")


def test_create_task_when_ffmpeg_raises_marks_failed(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin as plugin_mod

    def boom(_plan, **_kw):
        raise RuntimeError("ffmpeg blew up")

    monkeypatch.setattr(plugin_mod, "mix_tracks", boom)
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"voice_path": "voice.wav",
                                   "bgm_path": "bgm.mp3"})
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "failed"
        assert "ffmpeg" in (rec.get("error_message") or "").lower() or rec.get("error_message")


def test_create_task_blocks_empty_paths(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"voice_path": "", "bgm_path": ""})
        # Pydantic catches min_length=1 BEFORE QualityGates runs.
        assert r.status_code in (400, 422)


def test_get_task_returns_404_for_unknown(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.get("/tasks/missing")
        assert r.status_code == 404
        body = r.json()
        assert isinstance(body["detail"], dict)


def test_cancel_finished_task_returns_404(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"voice_path": "voice.wav",
                                   "bgm_path": "bgm.mp3"})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.post(f"/tasks/{tid}/cancel")
        assert r2.status_code == 404


def test_delete_task_then_get_returns_404(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"voice_path": "voice.wav",
                                   "bgm_path": "bgm.mp3"})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.delete(f"/tasks/{tid}")
        assert r2.status_code == 200
        r3 = c.get(f"/tasks/{tid}")
        assert r3.status_code == 404


def test_serve_audio_returns_file(plugin_factory) -> None:
    _, _, app = plugin_factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={"voice_path": "voice.wav",
                                   "bgm_path": "bgm.mp3"})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
        r2 = c.get(f"/tasks/{tid}/audio")
        assert r2.status_code == 200
        assert len(r2.content) > 0


def test_serve_audio_returns_404_when_not_finished(plugin_factory) -> None:
    """Asking for the file before the worker writes it should 404
    rather than streaming a 0-byte placeholder."""
    p, _, app = plugin_factory()

    async def insert_pending() -> str:
        return await p._tm.create_task(
            params={"voice_path": "voice.wav", "bgm_path": "bgm.mp3"},
            extra={"voice_path": "voice.wav", "bgm_path": "bgm.mp3"},
        )

    tid = asyncio.run(insert_pending())
    with TestClient(app) as c:
        r = c.get(f"/tasks/{tid}/audio")
        assert r.status_code == 404


# ── brain tools ────────────────────────────────────────────────────────


def test_brain_tool_status_returns_string(plugin_factory) -> None:
    _, api, app = plugin_factory()
    handler = api.tool_handler
    with TestClient(app) as c:
        r = c.post("/tasks", json={"voice_path": "voice.wav",
                                   "bgm_path": "bgm.mp3"})
        tid = r.json()["task_id"]
        _wait_until_done(c, tid)
    out = asyncio.run(handler("bgm_mixer_status", {"task_id": tid}))
    assert "succeeded" in out


def test_brain_tool_unknown_returns_string_not_raises(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    handler = api.tool_handler
    out = asyncio.run(handler("not_a_tool", {}))
    assert "unknown" in out.lower()


def test_brain_tool_create_with_bad_input_renders_friendly_string(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    handler = api.tool_handler
    out = asyncio.run(handler("bgm_mixer_create", {}))  # missing required
    assert out.startswith("[")  # cause_category prefix from ErrorCoach


def test_brain_tool_preview_returns_summary(plugin_factory) -> None:
    _, api, _ = plugin_factory()
    handler = api.tool_handler
    out = asyncio.run(handler("bgm_mixer_preview",
                              {"voice_path": "voice.wav",
                               "bgm_path": "bgm.mp3"}))
    assert "BGM" in out or "ducking" in out


# ── tracker selection ─────────────────────────────────────────────────


def test_select_tracker_unknown_name_raises(plugin_factory) -> None:
    p, _, _ = plugin_factory()
    with pytest.raises(ValueError):
        p._select_tracker("not_a_tracker", bpm_hint=120.0)


def test_select_tracker_auto_falls_back_to_stub_when_madmom_missing(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "_madmom_available", lambda: False)
    p, _, _ = plugin_factory()
    tracker = p._select_tracker("auto", bpm_hint=120.0)
    assert tracker.tracker_id == "stub"


def test_select_tracker_madmom_falls_back_silently(
    plugin_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User asked for madmom but it's not installed — the plugin
    should log + fall back to stub rather than crash mid-job."""
    import plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "_madmom_available", lambda: False)
    p, _, _ = plugin_factory()
    tracker = p._select_tracker("madmom", bpm_hint=100.0)
    assert tracker.tracker_id == "stub"


# ── on_unload ────────────────────────────────────────────────────────


def test_on_unload_cancels_inflight_workers(plugin_factory) -> None:
    p, api, _ = plugin_factory()

    async def stage() -> None:
        tid = await p._tm.create_task(
            params={"voice_path": "voice.wav", "bgm_path": "bgm.mp3"},
            extra={"voice_path": "voice.wav", "bgm_path": "bgm.mp3"},
        )

        async def slow_run(_tid):
            await asyncio.sleep(10.0)

        worker = asyncio.create_task(slow_run(tid))
        p._workers[tid] = worker
        worker.add_done_callback(lambda _t, k=tid: p._workers.pop(k, None))

    asyncio.run(stage())
    asyncio.run(p.on_unload())
    assert p._workers == {}
    drain_warnings = [m for lvl, m in api.logs
                      if lvl == "warning" and "drain error" in m]
    assert drain_warnings == []
