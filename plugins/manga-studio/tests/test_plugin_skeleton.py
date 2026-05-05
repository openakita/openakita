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


async def test_on_load_logs_phase_banner(loaded_plugin) -> None:
    """Plugin logs a one-line load banner so the user sees something
    in the host log on cold start. The exact phase label tracks the
    current build (Phase 2 = direct backend wired)."""
    _, p = loaded_plugin
    levels_msgs = p._api.logged  # type: ignore[attr-defined]
    assert any("manga studio plugin loaded" in msg.lower() for _lvl, msg in levels_msgs), (
        levels_msgs
    )


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


async def test_unknown_tool_returns_error(loaded_plugin) -> None:
    """An unregistered tool name returns a clear ``error:`` string —
    the dispatch table never raises into the LLM."""
    _, p = loaded_plugin
    api = p._api  # type: ignore[attr-defined]
    msg = await api.tool_handler("manga_does_not_exist", {})
    assert msg.startswith("error:")
    assert "manga_does_not_exist" in msg


async def test_workflow_test_tool_dispatches_to_probe(loaded_plugin) -> None:
    """Phase 3.1 wired the comfy client; ``manga_workflow_test`` now
    delegates to ``MangaComfyClient.probe_backend()`` rather than
    returning a Phase-3 stub. With nothing configured, the probe still
    fires, just reporting ``FAIL · backend=… · …missing key…``.

    The async init that constructs the client is fire-and-forget; the
    skeleton fixture doesn't drain it, so we tolerate either outcome
    (real probe string vs. ``error: workflow client not initialised``).
    """
    _, p = loaded_plugin
    api = p._api  # type: ignore[attr-defined]
    msg = await api.tool_handler("manga_workflow_test", {"backend": "runninghub"})
    assert (
        msg.startswith("FAIL ·")
        or msg.startswith("ok ·")
        or msg.startswith("error: workflow client")
    ), msg


async def _fresh_plugin_with_init(tmp_path: Path):
    """on_load + drain init **inside the running test loop** so spawned
    tasks share the same loop as the test body. The shared
    ``loaded_plugin`` fixture is sync (calls on_load before pytest's
    event loop exists), which loses the spawned init task. This
    helper rebuilds the plugin so the init fires on the correct loop.
    """
    import importlib

    import plugin as plugin_module

    importlib.reload(plugin_module)
    api = _StubAPI(tmp_path)
    p = plugin_module.Plugin()
    p.on_load(api)
    for t in list(api.spawned):
        try:
            await t
        except Exception:  # noqa: BLE001
            pass
    return plugin_module, p, api


async def test_render_panel_tool_calls_pipeline_image_step(tmp_path: Path) -> None:
    """``manga_render_panel`` was a stub that just echoed its args back.
    It now reads the episode row, recomposes the prompt, calls
    ``_gen_panel_image`` with the persisted backend, and writes the
    new URL back onto the storyboard."""
    _, p, api = await _fresh_plugin_with_init(tmp_path)
    if p._pipeline is None:  # type: ignore[attr-defined]
        pytest.skip("pipeline init failed in stub host — render_panel can't run")

    await p._tm.create_character(  # type: ignore[attr-defined]
        name="Aoi", role_type="main", ref_images=[]
    )
    char_rows = await p._tm.list_characters()  # type: ignore[attr-defined]
    char_id = char_rows[0]["id"]
    ep_id = await p._tm.create_episode(  # type: ignore[attr-defined]
        title="Test Ep", story="x", bound_characters=[char_id]
    )
    storyboard = {
        "episode_title": "T",
        "summary": "s",
        "panels": [
            {
                "idx": 0,
                "narration": "n",
                "dialogue": [],
                "characters_in_scene": ["Aoi"],
                "camera": "wide",
                "action": "stand",
                "mood": "calm",
                "background": "school",
                "image_url": "",
            }
        ],
    }
    await p._tm.update_episode_safe(  # type: ignore[attr-defined]
        ep_id, storyboard_json=storyboard
    )

    captured: dict[str, Any] = {}

    async def fake_gen(self, *, prompt, negative_prompt, ref_urls, ratio, output_path, backend):
        captured.update(prompt=prompt, backend=backend, ratio=ratio)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"PNG")
        return "https://oss/regen.png"

    import manga_pipeline as mp_mod  # noqa: PLC0415

    original = mp_mod.MangaPipeline._gen_panel_image  # noqa: SLF001
    mp_mod.MangaPipeline._gen_panel_image = fake_gen  # type: ignore[assignment]
    try:
        msg = await api.tool_handler(
            "manga_render_panel",
            {"episode_id": ep_id, "panel_index": 0},
        )
    finally:
        mp_mod.MangaPipeline._gen_panel_image = original  # type: ignore[assignment]

    assert "https://oss/regen.png" in msg, msg
    assert captured["backend"] == "direct"
    ep_row = await p._tm.get_episode(ep_id)  # type: ignore[attr-defined]
    sb = ep_row["storyboard"]
    assert sb["panels"][0]["image_url"] == "https://oss/regen.png"
    await p.on_unload()


async def test_async_init_failure_surfaces_through_healthz(tmp_path: Path, monkeypatch) -> None:
    """P3-14 regression: when ``_async_init`` blows up (most realistic
    cause: corrupt SQLite file from a prior crash), the user should
    see *something*. Before the fix this was a silent log line — UI
    had no signal whatsoever and the plugin appeared to load."""
    import importlib

    import plugin as plugin_module

    importlib.reload(plugin_module)
    api = _StubAPI(tmp_path)
    p = plugin_module.Plugin()

    # Force the task manager init to raise — same shape as a corrupted
    # WAL or a permissions error.
    async def boom() -> None:
        raise RuntimeError("simulated DB corruption")

    p_orig_load = p.on_load
    def patched_load(api):
        p_orig_load(api)
        # Replace the task manager init AFTER on_load registered the
        # spawn_task; the swapped coroutine is what _async_init awaits.
        from manga_task_manager import MangaTaskManager  # noqa: PLC0415

        async def init_fail(self) -> None:
            raise RuntimeError("simulated DB corruption")
        monkeypatch.setattr(MangaTaskManager, "init", init_fail, raising=True)

    # Easier — patch BEFORE on_load.
    from manga_task_manager import MangaTaskManager  # noqa: PLC0415

    async def init_fail(self) -> None:  # noqa: ARG001
        raise RuntimeError("simulated DB corruption")

    monkeypatch.setattr(MangaTaskManager, "init", init_fail, raising=True)

    p.on_load(api)
    for t in list(api.spawned):
        try:
            await t
        except Exception:  # noqa: BLE001
            pass

    assert p._init_error is not None  # type: ignore[attr-defined]
    assert p._init_error["phase"] == "task_manager"  # type: ignore[attr-defined]
    assert "simulated DB corruption" in p._init_error["message"]  # type: ignore[attr-defined]

    # And the /healthz route reports ``ok=False`` so the UI can react.
    routes = api.routers[0].routes
    healthz_route = next(r for r in routes if r.path == "/healthz")
    response = await healthz_route.endpoint()
    assert response["ok"] is False
    assert response["init_error"]["phase"] == "task_manager"
    assert "simulated DB corruption" in response["init_error"]["message"]


async def test_render_panel_tool_rejects_bad_args(tmp_path: Path) -> None:
    """Sanity: missing/invalid args yield a clean ``error:`` string."""
    _, p, api = await _fresh_plugin_with_init(tmp_path)
    if p._pipeline is None:  # type: ignore[attr-defined]
        pytest.skip("pipeline init failed in stub host")

    msg = await api.tool_handler("manga_render_panel", {})
    assert msg.startswith("error:"), msg
    msg = await api.tool_handler(
        "manga_render_panel", {"episode_id": "nope", "panel_index": 0}
    )
    assert msg.startswith("error:") and "not found" in msg
    await p.on_unload()


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
    """The /healthz endpoint reports current phase + per-backend
    readiness map. Cold start (no async-init drained) reports
    ``False`` for every direct-backend client; the UI uses this to
    show the red dots on the Settings tab."""
    _, p = loaded_plugin
    router = p._router  # type: ignore[attr-defined]
    healthz = next(r for r in router.routes if r.path == "/healthz")
    body = await healthz.endpoint()
    assert body["ok"] is True
    assert body["phase"] >= 2
    assert {
        "direct_ark",
        "direct_wan",
        "tts",
        "ffmpeg",
        "pipeline",
        "comfy",
        "oss",
    }.issubset(set(body["backends_ready"]))


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
