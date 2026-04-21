"""End-to-end tests for ``plugin.Plugin`` (dub-it)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from openakita_plugin_sdk.contrib import ReviewIssue, ReviewReport


def _ok_review(source: str = "x.mp4") -> ReviewReport:
    return ReviewReport(
        source=source, kind="video",
        metadata={"width": 1920, "height": 1080, "duration_sec": 60.0,
                   "fps": 30.0, "codec": "h264"},
        issues=(),
    )


def _failed_review(source: str = "x.mp4") -> ReviewReport:
    return ReviewReport(
        source=source, kind="video", metadata={},
        issues=(ReviewIssue(
            code="video.too_short", severity="error",
            message="too short", metric="duration_sec",
            actual=1.0, expected=">=3.0",
        ),),
    )


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
def plugin_factory(tmp_path: Path, monkeypatch):
    """Builds a Plugin with stub backends + ffmpeg mocked at engine level."""
    from plugin import Plugin
    import dub_engine

    # Default: every review passes; tests that need failure override.
    monkeypatch.setattr(
        dub_engine, "review_video",
        lambda *_a, **_kw: _ok_review(),
    )

    calls: list[list[str]] = []

    async def fake_ffmpeg(cmd: list[str], *, timeout_sec: float, **_kw):
        calls.append(list(cmd))
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKEMP4DATA" * 100)
        return type("R", (), {"returncode": 0, "duration_sec": 0.01})()

    # Patch the symbol the engine imports from the SDK.
    import openakita_plugin_sdk.contrib as sdk_contrib
    monkeypatch.setattr(sdk_contrib, "run_ffmpeg", fake_ffmpeg, raising=False)

    def _make() -> tuple[Any, FakePluginAPI, FastAPI, list[list[str]]]:
        api = FakePluginAPI(tmp_path)
        # Make sure the source video exists for plan_dub.
        src = tmp_path / "src.mp4"
        src.write_bytes(b"x" * 1024)
        p = Plugin()
        p.on_load(api)
        app = FastAPI()
        app.include_router(api.routes)
        return p, api, app, calls

    return _make, calls


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
    factory, _ = plugin_factory
    _, api, _, _ = factory()
    names = {d["name"] for d in api.tool_defs}
    assert names == {
        "dub_it_create",
        "dub_it_status",
        "dub_it_list",
        "dub_it_cancel",
        "dub_it_review_source",
        "dub_it_check_deps",
    }


def test_on_load_logs_load_message(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, api, _, _ = factory()
    msgs = [m for _, m in api.logs]
    assert any("loaded" in m for m in msgs)


def test_on_load_attaches_default_backends(plugin_factory) -> None:
    factory, _ = plugin_factory
    p, _, _, _ = factory()
    assert callable(p._transcribe)
    assert callable(p._translate)
    assert callable(p._synthesize)


# ── /healthz + /config ────────────────────────────────────────────────


def test_healthz_lists_target_languages(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        body = c.get("/healthz").json()
        assert body["plugin"] == "dub-it"
        assert "zh-CN" in body["allowed_target_languages"]
        assert body["default_output_format"] == "mp4"


def test_config_get_returns_defaults(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        body = c.get("/config").json()
        assert body["default_target_language"] == "zh-CN"
        assert body["default_output_format"] == "mp4"


def test_config_post_persists_overrides(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        c.post("/config", json={"default_target_language": "en"})
        body = c.get("/config").json()
        assert body["default_target_language"] == "en"


# ── /check-deps ───────────────────────────────────────────────────────


def test_check_deps_returns_known_keys(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        body = c.get("/check-deps").json()
        assert set(body["present"].keys()) == {"ffmpeg", "ffprobe"}


# ── /review ───────────────────────────────────────────────────────────


def test_review_returns_report_for_existing_file(plugin_factory, tmp_path: Path) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        body = c.post("/review", json={
            "source_video": str(tmp_path / "src.mp4"),
        }).json()
        assert body["passed"] is True
        assert body["kind"] == "video"


# ── /tasks (POST → worker → success) ──────────────────────────────────


def test_create_task_runs_to_success(plugin_factory, tmp_path: Path) -> None:
    factory, calls = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={
            "source_video": str(tmp_path / "src.mp4"),
            "target_language": "en",
        })
        assert r.status_code == 200, r.text
        tid = r.json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded", rec
        assert rec["result"]["bytes_output"] > 0
        assert len(rec["result"]["segments"]) >= 1
        # Two ffmpeg invocations: extract + mux.
        assert len(calls) == 2


def test_create_task_marks_failed_when_review_fails(
    plugin_factory, tmp_path: Path, monkeypatch,
) -> None:
    factory, _ = plugin_factory
    import dub_engine
    monkeypatch.setattr(dub_engine, "review_video",
                          lambda *_a, **_kw: _failed_review(str(tmp_path / "src.mp4")))
    _, _, app, _ = factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={
            "source_video": str(tmp_path / "src.mp4"),
            "target_language": "en",
        }).json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "failed"
        assert "video.too_short" in (rec.get("error_message") or "")


def test_create_task_records_failure_for_invalid_target_language(
    plugin_factory, tmp_path: Path,
) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={
            "source_video": str(tmp_path / "src.mp4"),
            "target_language": "xx",
        }).json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "failed"


def test_create_task_rejects_missing_required_fields(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        r = c.post("/tasks", json={})
        assert r.status_code == 422


# ── /tasks (GET) ──────────────────────────────────────────────────────


def test_list_tasks_returns_recent_jobs(plugin_factory, tmp_path: Path) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        for _ in range(2):
            c.post("/tasks", json={
                "source_video": str(tmp_path / "src.mp4"),
                "target_language": "en",
            })
        body = c.get("/tasks").json()
        assert body["total"] >= 2


def test_get_task_returns_404_for_missing(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        assert c.get("/tasks/nope").status_code == 404


def test_delete_task_round_trip(plugin_factory, tmp_path: Path) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={
            "source_video": str(tmp_path / "src.mp4"),
            "target_language": "en",
        }).json()["task_id"]
        _wait_until_done(c, tid)
        assert c.delete(f"/tasks/{tid}").status_code == 200
        assert c.get(f"/tasks/{tid}").status_code == 404


def test_cancel_returns_404_for_missing(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        assert c.post("/tasks/nope/cancel").status_code == 404


def test_serve_output_streams_back_bytes(plugin_factory, tmp_path: Path) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        tid = c.post("/tasks", json={
            "source_video": str(tmp_path / "src.mp4"),
            "target_language": "en",
        }).json()["task_id"]
        rec = _wait_until_done(c, tid)
        assert rec["status"] == "succeeded"
        r = c.get(f"/tasks/{tid}/output")
        assert r.status_code == 200
        assert len(r.content) > 0


def test_serve_output_returns_404_when_no_task(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, _, app, _ = factory()
    with TestClient(app) as c:
        assert c.get("/tasks/nope/output").status_code == 404


# ── brain tools ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brain_tool_check_deps(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, api, _, _ = factory()
    out = await api.tool_handler("dub_it_check_deps", {})
    assert ("ffmpeg" in out) or ("已就绪" in out)


@pytest.mark.asyncio
async def test_brain_tool_review_source_returns_summary(
    plugin_factory, tmp_path: Path,
) -> None:
    factory, _ = plugin_factory
    _, api, _, _ = factory()
    out = await api.tool_handler("dub_it_review_source", {
        "source_video": str(tmp_path / "src.mp4"),
    })
    assert "通过" in out or "kind=" in out


@pytest.mark.asyncio
async def test_brain_tool_status_for_missing_task(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, api, _, _ = factory()
    out = await api.tool_handler("dub_it_status", {"task_id": "nope"})
    assert "未找到" in out


@pytest.mark.asyncio
async def test_brain_tool_list_returns_empty(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, api, _, _ = factory()
    out = await api.tool_handler("dub_it_list", {})
    assert out == "(空)"


@pytest.mark.asyncio
async def test_brain_tool_unknown_returns_message(plugin_factory) -> None:
    factory, _ = plugin_factory
    _, api, _, _ = factory()
    out = await api.tool_handler("does_not_exist", {})
    assert "unknown tool" in out
