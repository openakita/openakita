"""Phase 4 plugin entry tests — 21 routes, 4 tools, healthz, no-handoff guards.

These tests cover the Phase 4 DoD checklist from
``docs/subtitle-craft-plan.md`` §11 + Gate 4:

- ``provides.tools`` is exactly 4 (no ``*_handoff_*``).
- 21 routes are wired and answer per their contract.
- ``/healthz`` returns the 4-field shape and never echoes the API key.
- ``on_unload`` invokes ``_PlaywrightSingleton.close()`` (mocked).
- All Pydantic request bodies declare ``ConfigDict(extra="forbid")``
  so typos surface as 422.
- Red-line grep guards for ``handoff`` literals and ``/handoff/``
  routes in ``plugin.py``.

We construct a ``MockPluginAPI`` rather than spinning up the full
host loader, since the goal here is to exercise *our* plugin code
in isolation. The few host-API surfaces we use are stubbed.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

PLUGIN_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# MockPluginAPI — minimal in-process stand-in for openakita.plugins.api
# ---------------------------------------------------------------------------


class MockPluginAPI:
    """Drop-in subset of ``PluginAPI`` exercised by ``Plugin.on_load``."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._tools: list[dict[str, Any]] = []
        self._tool_handler: Any = None
        self._router: APIRouter | None = None
        self._spawned: list[asyncio.Task[Any]] = []
        self.broadcast_calls: list[tuple[str, dict[str, Any]]] = []

    def get_data_dir(self) -> Path:
        return self._data_dir

    def register_api_routes(self, router: APIRouter) -> None:
        self._router = router

    def register_tools(self, defs: list[dict[str, Any]], *, handler: Any) -> None:
        self._tools = list(defs)
        self._tool_handler = handler

    def spawn_task(self, coro: Any, *, name: str = "") -> asyncio.Task[Any]:
        loop = asyncio.get_event_loop()
        task = loop.create_task(coro, name=name)
        self._spawned.append(task)
        return task

    def log(self, message: str, level: str = "info") -> None:
        pass

    def broadcast_ui_event(self, event_type: str, data: dict[str, Any]) -> None:
        self.broadcast_calls.append((event_type, data))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def loaded_plugin(tmp_path: Path):
    """Yield a fully-loaded ``Plugin`` + its ``MockPluginAPI`` and TestClient."""
    from plugin import Plugin

    api = MockPluginAPI(tmp_path)
    plugin = Plugin()
    plugin.on_load(api)

    # Wait for the spawned _async_init() to complete (it opens SQLite).
    if api._spawned:
        await asyncio.gather(*api._spawned, return_exceptions=True)

    assert api._router is not None, "Plugin must register an APIRouter"
    app = FastAPI()
    app.include_router(api._router)
    client = TestClient(app)

    try:
        yield plugin, api, client
    finally:
        await plugin.on_unload()


# ---------------------------------------------------------------------------
# Red-line guards
# ---------------------------------------------------------------------------


def test_no_handoff_route_in_plugin_source():
    """``/handoff/`` literal must not appear in ``plugin.py`` (red line)."""
    text = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    assert '"/handoff/' not in text
    assert "'/handoff/" not in text


def test_no_handoff_word_outside_docstring_in_plugin_source():
    """The word ``handoff`` may appear only inside docstrings/comments.

    We scan plugin.py and assert that any occurrence of ``handoff`` (case-
    insensitive) is on a line that is either a comment (``#``) or inside
    a triple-quoted docstring. A loose check is sufficient — the route
    literal check above is the strict guard.
    """
    text = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    lines = text.split("\n")
    in_doc = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.endswith('"""'):
            quote_count = line.count('"""')
            if quote_count % 2 == 1:
                in_doc = not in_doc
            continue
        if "handoff" in line.lower():
            assert in_doc or stripped.startswith("#"), (
                f"plugin.py:{i} mentions 'handoff' outside docstring/comment: {line!r}"
            )


def test_provides_tools_exactly_four():
    """``plugin.json`` must declare exactly 4 tools, none with 'handoff' prefix."""
    data = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    tools = data["provides"]["tools"]
    assert len(tools) == 4
    assert all("handoff" not in t for t in tools)


def test_pydantic_bodies_use_extra_forbid():
    """Every request Body class declares ``model_config = ConfigDict(extra='forbid')``.

    Catches the C6 reverse example (silent-param-ignore) at module load time.
    """
    import plugin as plugin_module

    body_classes = [
        plugin_module.CreateTaskBody,
        plugin_module.CostPreviewBody,
        plugin_module.ConfigUpdateBody,
        plugin_module.CustomStyleBody,
    ]
    for cls in body_classes:
        cfg = getattr(cls, "model_config", None)
        assert cfg is not None, f"{cls.__name__} missing model_config"
        # Pydantic v2 stores extra setting under "extra" key.
        assert cfg.get("extra") == "forbid", (
            f"{cls.__name__} must declare model_config = ConfigDict(extra='forbid')"
        )


def test_create_task_body_rejects_unknown_field():
    from plugin import CreateTaskBody

    with pytest.raises(ValidationError):
        CreateTaskBody.model_validate({"mode": "burn", "bogus_extra_field": 1})


# ---------------------------------------------------------------------------
# Route registration coverage
# ---------------------------------------------------------------------------


_EXPECTED_ROUTES: set[tuple[str, str]] = {
    ("POST", "/tasks"),
    ("GET", "/tasks"),
    ("GET", "/tasks/{task_id}"),
    ("DELETE", "/tasks/{task_id}"),
    ("POST", "/tasks/{task_id}/cancel"),
    ("POST", "/tasks/{task_id}/retry"),
    ("GET", "/tasks/{task_id}/download"),
    ("GET", "/tasks/{task_id}/download_video"),
    ("GET", "/tasks/{task_id}/preview_srt"),
    ("POST", "/upload"),
    ("GET", "/library/transcripts"),
    ("GET", "/library/srts"),
    ("GET", "/library/styles"),
    ("POST", "/library/styles"),
    ("DELETE", "/library/styles/{style_id}"),
    ("POST", "/cost-preview"),
    ("GET", "/settings"),
    ("PUT", "/settings"),
    ("GET", "/storage/stats"),
    ("GET", "/modes"),
    ("GET", "/healthz"),
}


@pytest.mark.asyncio
async def test_21_routes_registered(loaded_plugin):
    _, api, _ = loaded_plugin
    routes_seen: set[tuple[str, str]] = set()
    for r in api._router.routes:
        path = getattr(r, "path", "")
        for method in getattr(r, "methods", set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes_seen.add((method, path))

    missing = _EXPECTED_ROUTES - routes_seen
    extra_business = (
        routes_seen
        - _EXPECTED_ROUTES
        - {("GET", "/uploads/{rel_path:path}")}  # added by add_upload_preview_route
    )
    assert not missing, f"Missing expected routes: {missing}"
    # We don't fail on extras (the upload-preview route is fine), but we do
    # fail if any new path looks like /handoff/.
    assert not any("handoff" in m[1] for m in extra_business), extra_business


@pytest.mark.asyncio
async def test_no_handoff_routes_registered(loaded_plugin):
    _, api, _ = loaded_plugin
    for r in api._router.routes:
        path = getattr(r, "path", "")
        assert "handoff" not in path.lower()


# ---------------------------------------------------------------------------
# /healthz contract — 4 fields, no leak of api key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthz_returns_four_canonical_fields(loaded_plugin):
    _, _, client = loaded_plugin
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "ffmpeg_ok",
        "playwright_ok",
        "playwright_browser_ready",
        "dashscope_api_key_present",
    }
    for v in body.values():
        assert isinstance(v, bool)


@pytest.mark.asyncio
async def test_healthz_does_not_leak_api_key(loaded_plugin):
    plugin, _, client = loaded_plugin
    secret = "sk-test-LEAK-1234567890"
    await plugin._tm.set_config("dashscope_api_key", secret)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dashscope_api_key_present"] is True
    # The literal key must NOT appear in the response payload.
    assert secret not in resp.text


@pytest.mark.asyncio
async def test_settings_get_masks_api_key(loaded_plugin):
    plugin, _, client = loaded_plugin
    secret = "sk-test-MASK-abcdef0123"
    await plugin._tm.set_config("dashscope_api_key", secret)
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "dashscope_api_key" not in body
    assert body["dashscope_api_key_present"] is True
    # masked must not contain the raw secret
    masked = body["dashscope_api_key_masked"]
    assert secret not in masked
    assert masked.endswith(secret[-4:])


# ---------------------------------------------------------------------------
# /modes returns 4 modes + canonical 9 error_kinds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modes_returns_four_modes_and_nine_error_kinds(loaded_plugin):
    _, _, client = loaded_plugin
    resp = client.get("/modes")
    assert resp.status_code == 200
    body = resp.json()
    mode_ids = {m["id"] for m in body["modes"]}
    assert mode_ids == {"auto_subtitle", "translate", "repair", "burn"}
    assert set(body["error_kinds"]) == {
        "network",
        "timeout",
        "auth",
        "quota",
        "moderation",
        "dependency",
        "format",
        "duration",
        "unknown",
    }


# ---------------------------------------------------------------------------
# /cost-preview — independent estimator route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_preview_auto_subtitle(loaded_plugin):
    _, _, client = loaded_plugin
    resp = client.post(
        "/cost-preview",
        json={"mode": "auto_subtitle", "duration_sec": 60.0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "total_cny" in body
    assert "items" in body
    assert body["total_cny"] > 0


@pytest.mark.asyncio
async def test_cost_preview_rejects_unknown_field(loaded_plugin):
    _, _, client = loaded_plugin
    resp = client.post(
        "/cost-preview",
        json={"mode": "auto_subtitle", "bogus": 1},
    )
    # extra="forbid" → 422 from pydantic validation.
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /tasks lifecycle — create / get / delete (with mocked pipeline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_and_list(loaded_plugin, tmp_path):
    plugin, _, client = loaded_plugin

    # Create a fake SRT input so the validator is happy for "repair" mode.
    srt = tmp_path / "input.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello world\n",
        encoding="utf-8",
    )

    # Patch run_pipeline to a no-op so we don't actually run the pipeline.
    with patch("plugin.run_pipeline", new=AsyncMock(return_value=None)):
        resp = client.post(
            "/tasks",
            json={"mode": "repair", "srt_path": str(srt)},
        )
        assert resp.status_code == 200, resp.text
        task = resp.json()
        assert task["mode"] == "repair"
        # Allow the spawned pipeline task to finish.
        await asyncio.sleep(0.01)

        list_resp = client.get("/tasks")
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_create_task_rejects_missing_srt_for_repair(loaded_plugin):
    _, _, client = loaded_plugin
    resp = client.post("/tasks", json={"mode": "repair"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_task_rejects_unknown_mode(loaded_plugin):
    _, _, client = loaded_plugin
    resp = client.post("/tasks", json={"mode": "no_such_mode", "source_path": "x.mp4"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /tasks/{id}/cancel marks the task as canceled when no pipeline is running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_unknown_task_marks_canceled(loaded_plugin):
    plugin, _, client = loaded_plugin
    # Create a task row directly.
    task = await plugin._tm.create_task(mode="repair")
    resp = client.post(f"/tasks/{task['id']}/cancel")
    assert resp.status_code == 200
    refreshed = await plugin._tm.get_task(task["id"])
    assert refreshed is not None
    assert refreshed["status"] == "canceled"


# ---------------------------------------------------------------------------
# /library/styles round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_styles_builtin_then_add_then_delete(loaded_plugin):
    _, _, client = loaded_plugin
    # 1. Built-in styles present.
    list_resp = client.get("/library/styles")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert len(body["builtin"]) >= 5
    assert body["custom"] == []

    # 2. Add custom style.
    add_resp = client.post(
        "/library/styles",
        json={"label": "My Style", "font_size": 30},
    )
    assert add_resp.status_code == 200
    style = add_resp.json()
    style_id = style["id"]

    # 3. List again, verify present.
    list_resp = client.get("/library/styles")
    custom = list_resp.json()["custom"]
    assert any(s["id"] == style_id for s in custom)

    # 4. Delete.
    del_resp = client.delete(f"/library/styles/{style_id}")
    assert del_resp.status_code == 200

    # 5. Verify gone.
    list_resp = client.get("/library/styles")
    custom = list_resp.json()["custom"]
    assert not any(s["id"] == style_id for s in custom)


@pytest.mark.asyncio
async def test_delete_unknown_style_returns_404(loaded_plugin):
    _, _, client = loaded_plugin
    resp = client.delete("/library/styles/not_a_real_id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tool handler — 4 tool names, all return strings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_registered_count_and_names(loaded_plugin):
    _, api, _ = loaded_plugin
    names = {t["name"] for t in api._tools}
    assert names == {
        "subtitle_craft_create",
        "subtitle_craft_status",
        "subtitle_craft_list",
        "subtitle_craft_cancel",
    }


@pytest.mark.asyncio
async def test_tool_status_unknown_task(loaded_plugin):
    _, api, _ = loaded_plugin
    out = await api._tool_handler("subtitle_craft_status", {"task_id": "nope"})
    assert "not found" in out.lower()


@pytest.mark.asyncio
async def test_tool_list_returns_total(loaded_plugin):
    _, api, _ = loaded_plugin
    out = await api._tool_handler("subtitle_craft_list", {"limit": 5})
    assert "Total" in out


@pytest.mark.asyncio
async def test_tool_unknown_name_returns_diagnostic(loaded_plugin):
    _, api, _ = loaded_plugin
    out = await api._tool_handler("subtitle_craft_xxx", {})
    assert "Unknown tool" in out


# ---------------------------------------------------------------------------
# on_unload closes the Playwright singleton (P0-13/P0-14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_unload_closes_playwright_singleton(tmp_path):
    from plugin import Plugin

    api = MockPluginAPI(tmp_path)
    plugin = Plugin()
    plugin.on_load(api)
    if api._spawned:
        await asyncio.gather(*api._spawned, return_exceptions=True)

    with patch("plugin._PlaywrightSingleton.close", new=AsyncMock(return_value=None)) as mock_close:
        await plugin.on_unload()
        mock_close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Settings — set + get, then change API key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_put_persists_and_swaps_asr_client(loaded_plugin):
    plugin, _, client = loaded_plugin
    assert plugin._asr is None  # no key on init
    resp = client.put(
        "/settings",
        json={"updates": {"dashscope_api_key": "sk-new-fake-key-1234567890"}},
    )
    assert resp.status_code == 200
    assert plugin._asr is not None  # built on first non-empty key
    # Clear the key → asr should drop.
    resp = client.put("/settings", json={"updates": {"dashscope_api_key": ""}})
    assert resp.status_code == 200
    assert plugin._asr is None


# ---------------------------------------------------------------------------
# Storage stats — empty data dir gives a sensible payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_stats_returns_dict(loaded_plugin):
    _, _, client = loaded_plugin
    resp = client.get("/storage/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "total_files" in body
    assert "total_bytes" in body


# ---------------------------------------------------------------------------
# SSE event name invariant (red line #21) — _emit always uses task_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_forwards_event_name_to_broadcast(loaded_plugin):
    plugin, api, _ = loaded_plugin
    plugin._emit("task_update", {"task_id": "abc", "status": "running"})
    assert api.broadcast_calls
    name, payload = api.broadcast_calls[-1]
    assert name == "task_update"
    assert payload["task_id"] == "abc"


# ---------------------------------------------------------------------------
# Polling: orphan-task reaper backoff schedule sanity-check (no real sleep)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_loop_uses_three_stage_backoff_intervals(loaded_plugin):
    """The poll loop's interval ladder must be 3 → 10 → 30 seconds."""
    src = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    # Strict-ish check: we expect the literal numbers in the polling
    # function so a future refactor can't silently bypass the contract.
    assert re.search(r"interval\s*=\s*3\.0", src)
    assert re.search(r"interval\s*=\s*10\.0", src)
    assert re.search(r"interval\s*=\s*30\.0", src)
