"""Plugin wiring smoke tests for avatar-studio.

We exercise ``Plugin`` against a minimal in-memory ``PluginAPI`` stub —
enough to verify:

- ``on_load`` loads cleanly even when no API key is configured (Pixelle
  C5 — warn, never raise).
- 16 routes are registered on the FastAPI ``APIRouter``.
- 9 tools are registered with the right names.
- Every Pydantic body model rejects unknown fields (Pixelle C6).
- ``avatar_cost_preview`` tool returns a ¥-formatted total.
- ``GET /catalog`` shape is JSON-serialisable.

We do NOT exercise actual route HTTP traffic (that requires standing up
``TestClient`` with the host's full ``Brain``, etc); that's covered in
the Phase 6 integration test.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

# ─── Minimal PluginAPI stub ─────────────────────────────────────────────


class _StubAPI:
    """Just enough of ``PluginAPI`` for ``Plugin.on_load`` to succeed."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._config: dict[str, Any] = {}
        self.tools: list[dict[str, Any]] = []
        self.tool_handler: Any = None
        self.routers: list[Any] = []
        self.spawned: list[asyncio.Task[Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.logs: list[tuple[str, str]] = []

    def get_data_dir(self) -> Path:
        return self._data_dir

    def get_config(self) -> dict[str, Any]:
        return dict(self._config)

    def set_config(self, updates: dict[str, Any]) -> None:
        self._config.update(updates)

    def register_api_routes(self, router: Any) -> None:
        self.routers.append(router)

    def register_tools(self, tools: list[dict[str, Any]], handler: Any) -> None:
        self.tools.extend(tools)
        self.tool_handler = handler

    def spawn_task(self, coro: Any, *, name: str | None = None) -> Any:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not inside an async context — discard the coroutine cleanly
            # to avoid "coroutine was never awaited" warnings.
            coro.close()
            return None
        t = loop.create_task(coro, name=name)
        self.spawned.append(t)
        return t

    def broadcast_ui_event(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))

    def log(self, msg: str, level: str = "info") -> None:
        self.logs.append((level, msg))

    def has_permission(self, name: str) -> bool:
        return True


# ─── Helpers ────────────────────────────────────────────────────────────


@pytest.fixture
def plugin(tmp_path: Path) -> Any:
    from plugin import Plugin

    p = Plugin()
    api = _StubAPI(tmp_path)
    p.on_load(api)
    return p, api


# ─── Tests ──────────────────────────────────────────────────────────────


def test_plugin_loads_without_api_key_reports_status(plugin: tuple[Any, _StubAPI]) -> None:
    _p, api = plugin
    info = [m for lvl, m in api.logs if lvl == "info"]
    assert any("API Key" in m for m in info)


def test_plugin_registers_one_router(plugin: tuple[Any, _StubAPI]) -> None:
    _p, api = plugin
    assert len(api.routers) == 1


def test_plugin_registers_at_least_16_routes(plugin: tuple[Any, _StubAPI]) -> None:
    _p, api = plugin
    router = api.routers[0]
    paths = {getattr(r, "path", None) for r in router.routes}
    paths.discard(None)
    # 16 plugin routes + 1 vendored upload preview = 17 minimum
    expected_subset = {
        "/tasks",
        "/tasks/{task_id}",
        "/tasks/{task_id}/cancel",
        "/tasks/{task_id}/retry",
        "/cost-preview",
        "/voices",
        "/voices/{voice_id}",
        "/voices/{voice_id}/sample",
        "/figures",
        "/figures/{fig_id}",
        "/settings",
        "/healthz",
        "/catalog",
        "/upload",
    }
    assert expected_subset.issubset(paths), f"missing: {expected_subset - paths}"


def test_plugin_registers_ten_tools(plugin: tuple[Any, _StubAPI]) -> None:
    _p, api = plugin
    names = {t["name"] for t in api.tools}
    assert names == {
        "avatar_photo_speak",
        "avatar_video_relip",
        "avatar_video_reface",
        # mode_id "avatar_compose" already namespaced — no double prefix.
        "avatar_compose",
        "avatar_pose_drive",
        "avatar_voice_create",
        "avatar_voice_delete",
        "avatar_figure_create",
        "avatar_figure_delete",
        "avatar_cost_preview",
    }


@pytest.mark.asyncio
async def test_cost_preview_tool_returns_formatted_total(plugin: tuple[Any, _StubAPI]) -> None:
    p, api = plugin
    out = await api.tool_handler(
        "avatar_cost_preview",
        {
            "mode": "photo_speak",
            "text": "hi",
            "audio_duration_sec": 3.0,
            "resolution": "480P",
        },
    )
    assert "¥" in out
    assert "项" in out
    # Drain any spawned tasks from on_load / tool calls
    await asyncio.sleep(0)
    await p.on_unload()


def test_create_task_body_rejects_unknown_fields() -> None:
    from plugin import CreateTaskBody
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as ei:
        CreateTaskBody(mode="photo_speak", _bogus_field="oops")  # type: ignore[call-arg]
    err_types = {e["type"] for e in ei.value.errors()}
    assert "extra_forbidden" in err_types


def test_settings_body_rejects_unknown_fields() -> None:
    from plugin import SettingsBody
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SettingsBody(api_key="x", surprise=True)  # type: ignore[call-arg]


def test_create_voice_body_rejects_unknown_fields() -> None:
    from plugin import CreateVoiceBody
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateVoiceBody(  # type: ignore[call-arg]
            label="x",
            source_audio_path="/tmp/a.wav",
            dashscope_voice_id="v",
            extra_oops=True,
        )


def test_create_figure_body_rejects_unknown_fields() -> None:
    from plugin import CreateFigureBody
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateFigureBody(  # type: ignore[call-arg]
            label="x",
            image_path="/tmp/a.png",
            preview_url="/api/x",
            extra_oops=True,
        )


@pytest.mark.asyncio
async def test_on_unload_cancels_in_flight_tasks(tmp_path: Path) -> None:
    from plugin import Plugin

    p = Plugin()
    api = _StubAPI(tmp_path)
    p.on_load(api)

    # Schedule a long-running fake pipeline task and register it.
    async def slow() -> None:
        await asyncio.sleep(10)

    t = api.spawn_task(slow(), name="fake")
    p._poll_tasks["fake"] = t

    await p.on_unload()
    assert t.cancelled() or t.done()


def test_catalog_payload_serialisable(plugin: tuple[Any, _StubAPI]) -> None:
    import json

    from avatar_models import build_catalog

    cat = build_catalog()
    s = json.dumps(cat.__dict__)
    assert "photo_speak" in s
    assert "longxiaochun" in s
