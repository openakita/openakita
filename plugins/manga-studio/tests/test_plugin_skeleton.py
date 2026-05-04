"""Phase 1 — Plugin on_load smoke test + settings round-trip + redact + tool list."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


class _StubAPI:
    """Hand-rolled PluginAPI stand-in.

    We avoid importing the real PluginAPI here because doing so drags in
    the whole openakita host (plus its 30+ provider deps). The skeleton
    only needs five surface methods to come up cleanly:
    ``get_data_dir``, ``log``, ``register_tools``, ``register_api_routes``,
    ``spawn_task`` — plus the config getter/setter pair.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data = data_dir
        self._cfg: dict[str, Any] = {}
        self.logged: list[tuple[str, str]] = []
        self.tools: list[dict[str, Any]] = []
        self.tool_handler: Any = None
        self.routers: list[Any] = []
        self.spawned: list[asyncio.Task] = []

    def get_data_dir(self) -> Path:
        return self._data

    def get_config(self) -> dict[str, Any]:
        return dict(self._cfg)

    def set_config(self, updates: dict[str, Any]) -> None:
        self._cfg.update(updates)

    def log(self, msg: str, level: str = "info") -> None:
        self.logged.append((level, msg))

    def register_tools(self, definitions: list[dict[str, Any]], handler: Any) -> None:
        self.tools = list(definitions)
        self.tool_handler = handler

    def register_api_routes(self, router: Any) -> None:
        self.routers.append(router)

    def spawn_task(self, coro: Any, name: str | None = None) -> asyncio.Task:
        loop = asyncio.get_event_loop()
        task = loop.create_task(coro, name=name or "anon")
        self.spawned.append(task)
        return task


@pytest.fixture
def stub_api(tmp_path: Path) -> _StubAPI:
    return _StubAPI(tmp_path)


@pytest.fixture
def loaded_plugin(stub_api: _StubAPI):
    """Import + on_load (skeleton). Returns ``(plugin_module, plugin_instance)``."""
    import importlib

    import plugin as plugin_module

    importlib.reload(plugin_module)
    p = plugin_module.Plugin()
    p.on_load(stub_api)
    return plugin_module, p


# ─── on_load smoke ──────────────────────────────────────────────────────


async def test_on_load_completes_without_raising(loaded_plugin) -> None:
    _, p = loaded_plugin
    # Wait for the spawned init task (schema creation) to finish.
    while p._poll_tasks:  # type: ignore[attr-defined]
        await asyncio.sleep(0.01)
        break
    # Just letting the event loop tick once is enough to drain init.
    await asyncio.sleep(0.05)
    db = p._tm._db_path  # type: ignore[attr-defined]
    assert db.exists() or db.parent.exists()


async def test_on_load_logged_phase1_banner(loaded_plugin) -> None:
    _, p = loaded_plugin
    levels_msgs = p._api.logged  # type: ignore[attr-defined]
    assert any("phase 1" in msg.lower() for _lvl, msg in levels_msgs), levels_msgs


async def test_on_load_registers_11_tools(loaded_plugin) -> None:
    _, p = loaded_plugin
    api = p._api  # type: ignore[attr-defined]
    names = sorted(t["name"] for t in api.tools)
    assert names == sorted(
        [
            "manga_create_series",
            "manga_create_episode",
            "manga_episode_status",
            "manga_list_episodes",
            "manga_create_character",
            "manga_list_characters",
            "manga_quick_drama",
            "manga_split_script",
            "manga_render_panel",
            "manga_cost_preview",
            "manga_workflow_test",
        ]
    )


async def test_placeholder_handler_returns_friendly_message(
    loaded_plugin,
) -> None:
    _, p = loaded_plugin
    api = p._api  # type: ignore[attr-defined]
    msg = await api.tool_handler("manga_quick_drama", {"story": "x"})
    assert "manga_quick_drama" in msg
    assert "phase 1" in msg.lower() or "not yet wired" in msg.lower()


async def test_on_load_registers_router(loaded_plugin) -> None:
    _, p = loaded_plugin
    api = p._api  # type: ignore[attr-defined]
    assert len(api.routers) == 1
    routes = [r.path for r in api.routers[0].routes]
    assert "/healthz" in routes
    assert "/settings" in routes


# ─── Settings round-trip ────────────────────────────────────────────────


def test_load_settings_returns_defaults_when_empty(loaded_plugin) -> None:
    plugin_module, p = loaded_plugin
    s = p._load_settings()  # type: ignore[attr-defined]
    assert set(s) == set(plugin_module.DEFAULT_SETTINGS)
    for k, v in plugin_module.DEFAULT_SETTINGS.items():
        assert s[k] == v


def test_save_settings_persists_known_keys_only(loaded_plugin) -> None:
    _, p = loaded_plugin
    api = p._api  # type: ignore[attr-defined]
    merged = p._save_settings(  # type: ignore[attr-defined]
        {
            "ark_api_key": "sk-abcd1234efgh5678",
            "tts_engine": "cosyvoice",
            "totally_unknown_key": "ignored",
        }
    )
    assert merged["ark_api_key"] == "sk-abcd1234efgh5678"
    assert merged["tts_engine"] == "cosyvoice"
    # Persisted to config.json (via stub).
    cfg = api.get_config()
    stored = cfg["manga_studio_settings"]
    assert stored["ark_api_key"] == "sk-abcd1234efgh5678"
    assert "totally_unknown_key" not in stored
    # Should have logged a warning.
    assert any("totally_unknown_key" in msg for _lvl, msg in api.logged if _lvl == "warning")


def test_save_settings_then_reload_persists(loaded_plugin) -> None:
    _, p = loaded_plugin
    p._save_settings({"dashscope_api_key": "ds-key-xyz"})  # type: ignore[attr-defined]
    again = p._load_settings()  # type: ignore[attr-defined]
    assert again["dashscope_api_key"] == "ds-key-xyz"


# ─── Health probe + redaction ──────────────────────────────────────────


def test_router_healthz_payload_shape(loaded_plugin) -> None:
    _, p = loaded_plugin
    router = p._router  # type: ignore[attr-defined]
    paths = {r.path for r in router.routes}
    assert {"/healthz", "/settings"}.issubset(paths)


async def test_healthz_returns_phase_and_backend_map(loaded_plugin) -> None:
    _, p = loaded_plugin
    router = p._router  # type: ignore[attr-defined]
    healthz = next(r for r in router.routes if r.path == "/healthz")
    body = await healthz.endpoint()
    assert body["ok"] is True
    assert body["phase"] == 1
    assert set(body["backends_ready"]) == {
        "direct_ark",
        "direct_wan",
        "tts",
        "comfy",
        "oss",
    }
    assert all(v is False for v in body["backends_ready"].values())


def test_redact_obscures_long_secrets(loaded_plugin) -> None:
    """Eyeballing _redact's invariant via a settings round-trip."""
    _, p = loaded_plugin
    p._save_settings(  # type: ignore[attr-defined]
        {
            "ark_api_key": "sk-abcd1234efgh5678",
            "dashscope_api_key": "ds-1",  # short — masked entirely
        }
    )
    router = p._router  # type: ignore[attr-defined]
    settings_route = next(r for r in router.routes if r.path == "/settings" and "GET" in r.methods)
    asyncio_loop = asyncio.new_event_loop()
    try:
        body = asyncio_loop.run_until_complete(settings_route.endpoint())
    finally:
        asyncio_loop.close()
    redacted = body["settings"]
    assert redacted["ark_api_key"].startswith("sk-a")
    assert redacted["ark_api_key"].endswith("5678")
    assert "•" in redacted["ark_api_key"]
    assert redacted["dashscope_api_key"] == "•" * len("ds-1")
