"""Integration tests for the full plugin lifecycle.

Run with: pytest tests/integration/test_plugins/test_lifecycle.py --noconftest -v
"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path
from typing import Any

import pytest

from openakita.plugins.api import PluginPermissionError
from openakita.plugins.hooks import HookRegistry
from openakita.plugins.manager import PluginManager
from openakita.plugins.manifest import BASIC_PERMISSIONS
from openakita.plugins.sandbox import PluginErrorTracker

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples" / "plugins"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_plugin(
    plugins_dir: Path,
    plugin_id: str,
    *,
    plugin_type: str = "python",
    permissions: list[str] | None = None,
    on_load_body: str = "pass",
    on_unload_body: str = "pass",
    extra_manifest: dict[str, Any] | None = None,
    raise_on_load: bool = False,
) -> Path:
    """Create a minimal plugin directory with plugin.json and plugin.py."""
    plugin_dir = plugins_dir / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "id": plugin_id,
        "name": plugin_id.replace("-", " ").title(),
        "version": "0.1.0",
        "type": plugin_type,
        "permissions": permissions or list(BASIC_PERMISSIONS),
    }
    if extra_manifest:
        manifest.update(extra_manifest)

    (plugin_dir / "plugin.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    if plugin_type == "python":
        if raise_on_load:
            code = textwrap.dedent("""\
                from openakita.plugins.api import PluginAPI, PluginBase

                class Plugin(PluginBase):
                    def on_load(self, api: PluginAPI) -> None:
                        raise RuntimeError("intentional crash in on_load")

                    def on_unload(self) -> None:
                        pass
            """)
        else:
            code = textwrap.dedent(f"""\
                from openakita.plugins.api import PluginAPI, PluginBase

                class Plugin(PluginBase):
                    def on_load(self, api: PluginAPI) -> None:
                        {on_load_body}

                    def on_unload(self) -> None:
                        {on_unload_body}
            """)
        (plugin_dir / "plugin.py").write_text(code, encoding="utf-8")

    return plugin_dir


def _write_state_file(state_path: Path, plugin_states: dict[str, dict]) -> None:
    """Write a plugin_state.json with pre-approved permissions."""
    data: dict[str, Any] = {"plugins": {}, "active_backends": {}}
    for pid, entry in plugin_states.items():
        data["plugins"][pid] = {
            "enabled": entry.get("enabled", True),
            "granted_permissions": entry.get("granted_permissions", []),
            "installed_at": 0,
            "disabled_reason": "",
            "error_count": 0,
            "last_error": "",
            "last_error_time": 0,
        }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_all_example_plugins(tmp_path: Path) -> None:
    """Copy all 8 example plugins and verify discovery + loading.

    Plugins with only basic permissions (hello-tool) load cleanly.
    Non-python plugins (translate-skill, github-mcp) are discovered and loaded
    even without host refs.  Plugins requiring advanced permissions that aren't
    pre-approved land in the failed list — this is expected.
    """
    assert EXAMPLES_DIR.is_dir(), f"examples/plugins not found at {EXAMPLES_DIR}"

    plugins_dir = tmp_path / "plugins"
    shutil.copytree(EXAMPLES_DIR, plugins_dir)

    state_path = tmp_path / "plugin_state.json"
    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()

    loaded_ids = {p["id"] for p in pm.list_loaded()}
    failed_ids = set(pm.list_failed().keys())

    assert "hello-tool" in loaded_ids, (
        f"hello-tool should load (basic perms). loaded={loaded_ids}, failed={pm.list_failed()}"
    )

    assert "translate-skill" in loaded_ids, (
        f"translate-skill (skill type) should be discovered. loaded={loaded_ids}"
    )
    assert "github-mcp" in loaded_ids, (
        f"github-mcp (mcp type) should be discovered. loaded={loaded_ids}"
    )

    # Plugins needing advanced/system perms (echo-channel, qdrant-memory, etc.)
    # or hitting import/compat issues (message-logger, ollama-provider) end up
    # in the failed list — the important thing is they don't crash the manager.
    all_discovered = loaded_ids | failed_ids
    assert len(all_discovered) == 11, (
        f"Expected all 11 plugins discovered, got {len(all_discovered)}: {all_discovered}"
    )
    assert pm.loaded_count >= 3, (
        f"Expected at least 3 plugins loaded, got {pm.loaded_count}"
    )


@pytest.mark.asyncio
async def test_plugin_load_and_unload(tmp_path: Path) -> None:
    """Load a valid plugin, verify it's loaded, unload it, verify it's gone."""
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"

    _create_test_plugin(
        plugins_dir,
        "test-basic",
        permissions=["tools.register"],
        on_load_body=(
            'api.register_tools('
            '[{"type": "function", "function": {"name": "noop", '
            '"description": "no-op", "parameters": {"type": "object", "properties": {}}}}], '
            'lambda n, p: "ok")'
        ),
    )

    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()

    loaded_ids = [p["id"] for p in pm.list_loaded()]
    assert "test-basic" in loaded_ids
    assert pm.loaded_count == 1

    unloaded = await pm.unload_plugin("test-basic")
    assert unloaded is True
    assert pm.loaded_count == 0
    assert "test-basic" not in [p["id"] for p in pm.list_loaded()]


@pytest.mark.asyncio
async def test_crash_isolation(tmp_path: Path) -> None:
    """A crashing plugin must not prevent a good plugin from loading."""
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"

    _create_test_plugin(
        plugins_dir,
        "good-plugin",
        permissions=["tools.register"],
        on_load_body='api.log("good plugin loaded")',
    )

    _create_test_plugin(
        plugins_dir,
        "bad-plugin",
        permissions=["tools.register"],
        raise_on_load=True,
    )

    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()

    loaded_ids = {p["id"] for p in pm.list_loaded()}
    failed = pm.list_failed()

    assert "good-plugin" in loaded_ids, f"Good plugin should load. loaded={loaded_ids}"
    assert "bad-plugin" in failed, f"Bad plugin should be in failed. failed={failed}"
    assert "RuntimeError" in failed["bad-plugin"]


@pytest.mark.asyncio
async def test_hook_dispatch_isolation(tmp_path: Path) -> None:
    """One crashing hook callback must not block the other."""
    tracker = PluginErrorTracker()
    registry = HookRegistry(error_tracker=tracker)

    async def good_callback(**kwargs: Any) -> str:
        return "good-result"

    async def bad_callback(**kwargs: Any) -> str:
        raise ValueError("intentional hook crash")

    registry.register("on_init", bad_callback, plugin_id="crasher")
    registry.register("on_init", good_callback, plugin_id="good-one")

    results = await registry.dispatch("on_init")

    assert "good-result" in results, f"Good callback result missing. results={results}"
    assert len(results) == 1, "Only the good callback should produce a result"


@pytest.mark.asyncio
async def test_error_accumulation_auto_disable() -> None:
    """After 10+ errors within the window, a plugin's callbacks are skipped."""
    tracker = PluginErrorTracker()
    registry = HookRegistry(error_tracker=tracker)

    call_count = 0

    async def always_fail(**kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("always fails")

    registry.register("on_init", always_fail, plugin_id="flaky")

    for _i in range(11):
        await registry.dispatch("on_init")

    assert tracker.is_disabled("flaky"), "Plugin should be auto-disabled after 10+ errors"

    call_count_before = call_count
    await registry.dispatch("on_init")
    assert call_count == call_count_before, (
        "Callback should be skipped after auto-disable"
    )


@pytest.mark.asyncio
async def test_permission_boundary(tmp_path: Path) -> None:
    """A plugin with only basic permissions must not access advanced APIs."""
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"

    _create_test_plugin(
        plugins_dir,
        "basic-only",
        permissions=["tools.register", "log"],
        on_load_body='api.log("loaded with basic perms")',
    )

    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()

    loaded = pm.get_loaded("basic-only")
    assert loaded is not None, "basic-only plugin should be loaded"

    assert loaded.api.get_brain() is None, "get_brain should return None without permission"
    assert loaded.api.get_memory_manager() is None, "get_memory_manager should return None"
    assert loaded.api.get_settings() is None, "get_settings should return None"
    assert "brain.access" in loaded.api._pending_permissions


@pytest.mark.asyncio
async def test_tool_definitions_chain(tmp_path):
    """Plugin tools must appear in both tool_definitions and tool_catalog."""
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"

    tool_definitions: list[dict] = []

    class FakeCatalog:
        def __init__(self):
            self._tools: dict[str, dict] = {}
            self._cached_catalog = None

        def add_tool(self, tool: dict):
            self._tools[tool["name"]] = tool
            self._cached_catalog = None

        def remove_tool(self, tool_name: str) -> bool:
            if tool_name in self._tools:
                del self._tools[tool_name]
                self._cached_catalog = None
                return True
            return False

    catalog = FakeCatalog()

    class FakeHandlerRegistry:
        def __init__(self):
            self.registered: dict[str, Any] = {}

        def register(self, handler_name, handler, tool_names=None):
            self.registered[handler_name] = {
                "handler": handler, "tool_names": tool_names
            }

        def unregister(self, handler_name):
            self.registered.pop(handler_name, None)

    registry = FakeHandlerRegistry()

    _create_test_plugin(
        plugins_dir,
        "tool-test",
        on_load_body=(
            "api.register_tools("
            "[{'name': 'plugin_hello', 'description': 'say hello', 'input_schema': {}}], "
            "lambda name, params: 'hello')"
        ),
    )

    pm = PluginManager(
        plugins_dir=plugins_dir,
        state_path=state_path,
        host_refs={
            "tool_registry": registry,
            "tool_definitions": tool_definitions,
            "tool_catalog": catalog,
        },
    )
    await pm.load_all()

    assert pm.loaded_count == 1

    assert any(d.get("name") == "plugin_hello" for d in tool_definitions), \
        "Plugin tool definition must be added to tool_definitions list"

    assert "plugin_hello" in catalog._tools, \
        "Plugin tool must be added to tool_catalog"

    assert "plugin_tool-test" in registry.registered, \
        "Plugin handler must be registered in handler_registry"

    await pm.unload_plugin("tool-test")

    assert not any(d.get("name") == "plugin_hello" for d in tool_definitions), \
        "Plugin tool must be removed from tool_definitions on unload"

    assert "plugin_hello" not in catalog._tools, \
        "Plugin tool must be removed from tool_catalog on unload"


@pytest.mark.asyncio
async def test_llm_provider_cleanup_chain(tmp_path):
    """LLM provider class must have __plugin_id__ set for cleanup."""
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"

    plugin_dir = plugins_dir / "llm-test"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    (plugin_dir / "plugin.json").write_text(json.dumps({
        "id": "llm-test",
        "name": "LLM Test",
        "version": "0.1.0",
        "type": "python",
        "permissions": list(BASIC_PERMISSIONS) + ["llm.register"],
    }), encoding="utf-8")

    (plugin_dir / "plugin.py").write_text(textwrap.dedent("""\
        from openakita.plugins.api import PluginAPI, PluginBase

        class FakeProvider:
            pass

        class Plugin(PluginBase):
            def on_load(self, api: PluginAPI) -> None:
                api.register_llm_provider("fake_proto", FakeProvider)

            def on_unload(self) -> None:
                pass
    """), encoding="utf-8")

    _write_state_file(state_path, {
        "llm-test": {"granted_permissions": list(BASIC_PERMISSIONS) + ["llm.register"]},
    })

    from openakita.plugins import PLUGIN_PROVIDER_MAP
    PLUGIN_PROVIDER_MAP.clear()

    pm = PluginManager(
        plugins_dir=plugins_dir,
        state_path=state_path,
        host_refs={},
    )
    await pm.load_all()

    assert "fake_proto" in PLUGIN_PROVIDER_MAP
    cls = PLUGIN_PROVIDER_MAP["fake_proto"]
    assert getattr(cls, "__plugin_id__", None) == "llm-test", \
        "__plugin_id__ must be set on provider class for cleanup tracking"

    await pm.unload_plugin("llm-test")

    assert "fake_proto" not in PLUGIN_PROVIDER_MAP, \
        "Provider must be cleaned up from PLUGIN_PROVIDER_MAP on unload"


@pytest.mark.asyncio
async def test_memory_backends_chain(tmp_path):
    """memory_backends host_ref must be populated by register_memory_backend."""
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"

    memory_backends: dict = {}

    plugin_dir = plugins_dir / "mem-test"
    plugin_dir.mkdir(parents=True, exist_ok=True)

    (plugin_dir / "plugin.json").write_text(json.dumps({
        "id": "mem-test",
        "name": "Memory Test",
        "version": "0.1.0",
        "type": "python",
        "permissions": list(BASIC_PERMISSIONS) + ["memory.write"],
    }), encoding="utf-8")

    (plugin_dir / "plugin.py").write_text(textwrap.dedent("""\
        from openakita.plugins.api import PluginAPI, PluginBase

        class FakeBackend:
            async def store(self, key, value):
                pass
            async def retrieve(self, key):
                return None

        class Plugin(PluginBase):
            def on_load(self, api: PluginAPI) -> None:
                api.register_memory_backend(FakeBackend())

            def on_unload(self) -> None:
                pass
    """), encoding="utf-8")

    _write_state_file(state_path, {
        "mem-test": {"granted_permissions": list(BASIC_PERMISSIONS) + ["memory.write"]},
    })

    pm = PluginManager(
        plugins_dir=plugins_dir,
        state_path=state_path,
        host_refs={"memory_backends": memory_backends},
    )
    await pm.load_all()

    assert "mem-test" in memory_backends, \
        "Plugin memory backend must be registered in memory_backends dict"

    await pm.unload_plugin("mem-test")

    assert "mem-test" not in memory_backends, \
        "Plugin memory backend must be cleaned up on unload"


@pytest.mark.asyncio
async def test_gateway_late_wiring(tmp_path):
    """Gateway added to host_refs after plugin load must be visible to plugins."""
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"

    _create_test_plugin(plugins_dir, "gw-test")

    host_refs: dict = {"gateway": None}

    pm = PluginManager(
        plugins_dir=plugins_dir,
        state_path=state_path,
        host_refs=host_refs,
    )
    await pm.load_all()
    assert pm.loaded_count == 1

    class FakeGateway:
        def get_adapter(self, channel):
            return None

    host_refs["gateway"] = FakeGateway()

    loaded = pm.get_loaded("gw-test")
    assert loaded is not None
    assert loaded.api._host.get("gateway") is not None, \
        "Mutating host_refs dict must propagate to existing PluginAPI instances"

