"""Tests for openakita.plugins.manager — PluginManager lifecycle."""

from __future__ import annotations

import json
import textwrap

from openakita.plugins.manager import PluginManager


def _make_plugin_dir(base, plugin_id, *, ptype="python", extra_manifest=None):
    """Create a minimal plugin directory with plugin.json and plugin.py."""
    d = base / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id.replace("-", " ").title(),
        "version": "1.0.0",
        "type": ptype,
        "permissions": ["tools.register"],
    }
    if extra_manifest:
        manifest.update(extra_manifest)

    (d / "plugin.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    if ptype == "python":
        (d / "plugin.py").write_text(
            textwrap.dedent("""\
                from openakita.plugins.api import PluginAPI, PluginBase

                class Plugin(PluginBase):
                    def on_load(self, api: PluginAPI) -> None:
                        api.log("loaded")

                    def on_unload(self) -> None:
                        pass
            """),
            encoding="utf-8",
        )
    return d


# ---------- Discovery ----------


class TestDiscovery:
    def test_discovers_plugin_dirs(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "p1")
        _make_plugin_dir(plugins_dir, "p2")
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        dirs = mgr._discover_plugins()
        names = [d.name for d in dirs]
        assert "p1" in names
        assert "p2" in names

    def test_skips_dirs_without_plugin_json(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "valid")
        (plugins_dir / "no-manifest").mkdir()
        (plugins_dir / "no-manifest" / "README.md").write_text("hi", encoding="utf-8")
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        dirs = mgr._discover_plugins()
        names = [d.name for d in dirs]
        assert "valid" in names
        assert "no-manifest" not in names

    def test_empty_dir(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        assert mgr._discover_plugins() == []

    def test_nonexistent_dir(self, tmp_path):
        plugins_dir = tmp_path / "nonexistent"
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        assert mgr._discover_plugins() == []


# ---------- load_all ----------


class TestLoadAll:
    async def test_empty_dir_no_errors(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        assert mgr.loaded_count == 0
        assert mgr.failed_count == 0

    async def test_loads_valid_python_plugin(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "hello")
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        assert mgr.loaded_count == 1
        loaded = mgr.list_loaded()
        assert len(loaded) == 1
        assert loaded[0]["id"] == "hello"

    async def test_skips_disabled_plugin(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "disabled-one")
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        mgr.state.disable("disabled-one", "test")
        await mgr.load_all()
        assert mgr.loaded_count == 0

    async def test_handles_load_exception_gracefully(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        d = plugins_dir / "bad-plugin"
        d.mkdir()
        (d / "plugin.json").write_text(
            json.dumps({
                "id": "bad-plugin",
                "name": "Bad",
                "version": "1.0.0",
                "type": "python",
            }),
            encoding="utf-8",
        )
        (d / "plugin.py").write_text(
            textwrap.dedent("""\
                from openakita.plugins.api import PluginAPI, PluginBase

                class Plugin(PluginBase):
                    def on_load(self, api: PluginAPI) -> None:
                        raise RuntimeError("I refuse to load!")

                    def on_unload(self) -> None:
                        pass
            """),
            encoding="utf-8",
        )
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        assert mgr.loaded_count == 0
        assert mgr.failed_count == 1
        failed = mgr.list_failed()
        assert "bad-plugin" in failed

    async def test_missing_module_error_mentions_dependency_paths(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        d = plugins_dir / "missing-dep"
        d.mkdir()
        (d / "plugin.json").write_text(
            json.dumps({
                "id": "missing-dep",
                "name": "Missing Dep",
                "version": "1.0.0",
                "type": "python",
                "requires": {"pip": ["missing-package>=1.0.0"]},
            }),
            encoding="utf-8",
        )
        (d / "plugin.py").write_text(
            "import definitely_missing_pkg_xyz\n",
            encoding="utf-8",
        )

        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()

        failed = mgr.list_failed()
        msg = failed["missing-dep"]
        assert "definitely_missing_pkg_xyz" in msg
        assert "requires.pip" in msg
        assert "deps" in msg
        assert "settings panel" in msg

    async def test_handles_manifest_error(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        d = plugins_dir / "bad-manifest"
        d.mkdir()
        (d / "plugin.json").write_text("{bad json", encoding="utf-8")
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        assert mgr.loaded_count == 0
        assert mgr.failed_count == 1

    async def test_handles_load_timeout(self, tmp_path, monkeypatch):
        """Verify that a plugin whose _load_single exceeds load_timeout is recorded as failed."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "slow-plugin", extra_manifest={
            "load_timeout": 0.01,
        })

        import asyncio as _asyncio

        orig_load_single = PluginManager._load_single

        async def _patched_load_single(self, manifest, plugin_dir):
            await _asyncio.sleep(5)
            return await orig_load_single(self, manifest, plugin_dir)

        monkeypatch.setattr(PluginManager, "_load_single", _patched_load_single)

        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        assert mgr.loaded_count == 0
        assert "slow-plugin" in mgr.list_failed()


# ---------- unload ----------


class TestUnload:
    async def test_unload_calls_cleanup(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "unload-me")
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        assert mgr.loaded_count == 1
        result = await mgr.unload_plugin("unload-me")
        assert result is True
        assert mgr.loaded_count == 0

    async def test_unload_nonexistent_returns_false(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        result = await mgr.unload_plugin("ghost")
        assert result is False


# ---------- disable ----------


class TestDisable:
    async def test_disable_marks_state_and_unloads(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "to-disable")
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        assert mgr.loaded_count == 1
        await mgr.disable_plugin("to-disable", reason="test")
        assert mgr.loaded_count == 0
        assert not mgr.state.is_enabled("to-disable")


# ---------- list_loaded / list_failed ----------


class TestListMethods:
    async def test_list_loaded_returns_correct_info(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "info-plugin")
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        loaded = mgr.list_loaded()
        assert len(loaded) == 1
        item = loaded[0]
        assert item["id"] == "info-plugin"
        assert item["version"] == "1.0.0"
        assert item["type"] == "python"
        assert "permission_level" in item

    async def test_list_failed_returns_errors(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        d = plugins_dir / "broken"
        d.mkdir()
        (d / "plugin.json").write_text("{invalid", encoding="utf-8")
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        failed = mgr.list_failed()
        assert "broken" in failed


# ---------- Permission resolution ----------


class TestPermissionResolution:
    async def test_basic_always_granted(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "basic-perms", extra_manifest={
            "permissions": ["tools.register", "hooks.basic"],
        })
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        loaded = mgr.get_loaded("basic-perms")
        assert loaded is not None
        granted = loaded.api._granted_permissions
        assert "tools.register" in granted

    async def test_advanced_not_granted_without_approval(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "adv-perms", extra_manifest={
            "permissions": ["tools.register", "brain.access"],
        })
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        loaded = mgr.get_loaded("adv-perms")
        assert loaded is not None
        assert "brain.access" not in loaded.api._granted_permissions

    async def test_advanced_granted_when_previously_approved(self, tmp_path):
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_plugin_dir(plugins_dir, "approved-perms", extra_manifest={
            "permissions": ["tools.register", "brain.access"],
        })
        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        entry = mgr.state.ensure_entry("approved-perms")
        entry.granted_permissions = ["brain.access"]
        await mgr.load_all()
        loaded = mgr.get_loaded("approved-perms")
        assert loaded is not None
        assert "brain.access" in loaded.api._granted_permissions


# ---------- Edge cases: partial registration cleanup ----------


class TestPartialRegistrationCleanup:
    async def test_on_load_crash_cleans_up_registered_hooks(self, tmp_path):
        """If on_load registers hooks then crashes, hooks must be cleaned up."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        d = plugins_dir / "crash-partial"
        d.mkdir()
        (d / "plugin.json").write_text(json.dumps({
            "id": "crash-partial",
            "name": "Crash Partial",
            "version": "1.0.0",
            "type": "python",
            "permissions": ["tools.register", "hooks.basic"],
        }))
        (d / "plugin.py").write_text(textwrap.dedent("""\
            from openakita.plugins.api import PluginAPI, PluginBase

            class Plugin(PluginBase):
                def on_load(self, api: PluginAPI) -> None:
                    api.register_hook("on_init", lambda: None)
                    raise RuntimeError("intentional crash after partial registration")

                def on_unload(self) -> None:
                    pass
        """))

        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        assert mgr.get_loaded("crash-partial") is None
        assert "crash-partial" in mgr.list_failed()
        assert len(mgr.hook_registry.get_hooks("on_init")) == 0

    async def test_on_load_crash_cleans_up_registered_tools(self, tmp_path):
        """If on_load registers tools then crashes, tools must be cleaned up."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        d = plugins_dir / "crash-tools"
        d.mkdir()
        (d / "plugin.json").write_text(json.dumps({
            "id": "crash-tools",
            "name": "Crash Tools",
            "version": "1.0.0",
            "type": "python",
            "permissions": ["tools.register"],
        }))
        (d / "plugin.py").write_text(textwrap.dedent("""\
            from openakita.plugins.api import PluginAPI, PluginBase

            class Plugin(PluginBase):
                def on_load(self, api: PluginAPI) -> None:
                    api.register_tools(
                        [{"name": "my_tool", "description": "test"}],
                        lambda name, args: "ok"
                    )
                    raise RuntimeError("crash after tool registration")

                def on_unload(self) -> None:
                    pass
        """))

        from unittest.mock import MagicMock
        mock_registry = MagicMock()
        tool_defs: list[dict] = []
        mgr = PluginManager(
            plugins_dir,
            state_path=tmp_path / "state.json",
            host_refs={
                "tool_registry": mock_registry,
                "tool_definitions": tool_defs,
            },
        )
        await mgr.load_all()
        assert mgr.get_loaded("crash-tools") is None
        assert len(tool_defs) == 0

    async def test_syntax_error_plugin_no_syspath_leak(self, tmp_path):
        """Plugin with syntax error must not leak sys.path entries."""
        import sys

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        d = plugins_dir / "bad-syntax"
        d.mkdir()
        (d / "plugin.json").write_text(json.dumps({
            "id": "bad-syntax",
            "name": "Bad Syntax",
            "version": "1.0.0",
            "type": "python",
            "permissions": ["tools.register"],
        }))
        (d / "plugin.py").write_text("def broken(\n")

        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        assert mgr.get_loaded("bad-syntax") is None
        assert str(d) not in sys.path
        assert "openakita_plugin_bad_syntax" not in sys.modules

