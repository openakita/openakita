"""Tests for the v1.28 plugin unload / hot-reload hardening (Phases 1-4).

Covers:
  - Async ``on_unload`` is awaited on the main loop.
  - Sync ``on_unload`` that schedules cleanup via ``loop.create_task`` works.
  - Plugin-local submodules are evicted from ``sys.modules`` on unload.
  - ``api.spawn_task`` registers tasks; unload cancels & awaits them.
  - ``installer._robust_rmtree`` retries and clears read-only files.
  - ``installer.uninstall`` returns a structured dict (removed/partial/warnings).
  - ``installer.uninstall(purge_data=True, data_root=...)`` purges plugin_data.
  - ``PluginState.dev_mode`` round-trips through save/load.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from openakita.plugins import installer
from openakita.plugins.manager import PluginManager
from openakita.plugins.manifest import BASIC_PERMISSIONS
from openakita.plugins.state import PluginState

# pytest-asyncio is in auto mode (see pyproject.toml), so async test functions
# do not need the @pytest.mark.asyncio decorator.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_state_file(state_path: Path, plugin_states: dict[str, dict]) -> None:
    data: dict = {"plugins": {}, "active_backends": {}, "schema_version": 2}
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
    state_path.write_text(json.dumps(data), encoding="utf-8")


def _make_plugin(
    plugins_dir: Path,
    pid: str,
    body: str,
    *,
    perms: list[str] | None = None,
) -> Path:
    plugin_dir = plugins_dir / pid
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": pid,
        "name": pid,
        "version": "0.1.0",
        "type": "python",
        "permissions": perms or list(BASIC_PERMISSIONS),
    }
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(body, encoding="utf-8")
    return plugin_dir


def _build_pm(tmp_path: Path) -> PluginManager:
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"
    return PluginManager(plugins_dir, state_path=state_path)


# ---------------------------------------------------------------------------
# Fix-1: async on_unload
# ---------------------------------------------------------------------------


async def test_async_on_unload_is_awaited(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"
    marker = tmp_path / "async_unload.marker"
    body = textwrap.dedent(f"""\
        from pathlib import Path
        from openakita.plugins.api import PluginBase

        class Plugin(PluginBase):
            def on_load(self, api):
                self._api = api

            async def on_unload(self):
                Path({str(marker)!r}).write_text("ok", encoding="utf-8")
    """)
    _make_plugin(plugins_dir, "async-unload", body)
    _write_state_file(state_path, {"async-unload": {}})

    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()
    assert "async-unload" in {p["id"] for p in pm.list_loaded()}

    assert await pm.unload_plugin("async-unload") is True
    assert marker.read_text(encoding="utf-8") == "ok"


async def test_sync_on_unload_with_create_task_runs_to_completion(
    tmp_path: Path,
) -> None:
    """Legacy plugins that schedule cleanup via ``loop.create_task`` must work."""
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"
    marker = tmp_path / "sync_create_task.marker"
    body = textwrap.dedent(f"""\
        import asyncio
        from pathlib import Path
        from openakita.plugins.api import PluginBase

        async def _async_cleanup():
            Path({str(marker)!r}).write_text("ok", encoding="utf-8")

        class Plugin(PluginBase):
            def on_load(self, api):
                pass

            def on_unload(self):
                loop = asyncio.get_event_loop()
                loop.create_task(_async_cleanup())
    """)
    _make_plugin(plugins_dir, "legacy-unload", body)
    _write_state_file(state_path, {"legacy-unload": {}})

    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()
    assert await pm.unload_plugin("legacy-unload") is True
    # _invoke_on_unload must drain the create_task() before returning.
    assert marker.read_text(encoding="utf-8") == "ok"


# ---------------------------------------------------------------------------
# Fix-2: submodule cleanup
# ---------------------------------------------------------------------------


async def test_submodule_evicted_on_unload(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"
    plugin_dir = _make_plugin(
        plugins_dir,
        "with-submod",
        textwrap.dedent("""\
            from openakita.plugins.api import PluginBase
            from helper_lib import HELPER_VALUE  # plugin-local submodule

            class Plugin(PluginBase):
                def on_load(self, api):
                    api.log(f"helper={HELPER_VALUE}")
                def on_unload(self):
                    pass
        """),
    )
    (plugin_dir / "helper_lib.py").write_text(
        "HELPER_VALUE = 'first'\n", encoding="utf-8"
    )
    _write_state_file(state_path, {"with-submod": {}})

    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()
    assert "helper_lib" in sys.modules

    await pm.unload_plugin("with-submod")
    assert "helper_lib" not in sys.modules, (
        "Plugin-local submodules must be removed from sys.modules so a "
        "subsequent reinstall picks up fresh code instead of the cached one."
    )


# ---------------------------------------------------------------------------
# Fix-3: spawn_task is tracked & cancelled on unload
# ---------------------------------------------------------------------------


async def test_spawn_task_cancelled_on_unload(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"
    body = textwrap.dedent("""\
        import asyncio
        from openakita.plugins.api import PluginBase

        class Plugin(PluginBase):
            def on_load(self, api):
                self._api = api
                async def _loop():
                    while True:
                        await asyncio.sleep(0.05)
                api.spawn_task(_loop(), name="probe-loop")

            def on_unload(self):
                pass
    """)
    _make_plugin(plugins_dir, "spawner", body)
    _write_state_file(state_path, {"spawner": {}})

    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()

    loaded = pm.get_loaded("spawner")
    assert loaded is not None
    snapshot = loaded.api.list_spawned_tasks()
    assert any(t["name"] == "probe-loop" and not t["done"] for t in snapshot)

    await pm.unload_plugin("spawner")
    final = loaded.api.list_spawned_tasks()
    for t in final:
        assert t["done"] is True


# ---------------------------------------------------------------------------
# Fix-5: _robust_rmtree handles read-only files
# ---------------------------------------------------------------------------


def test_robust_rmtree_clears_readonly_files(tmp_path: Path) -> None:
    target = tmp_path / "ro-tree"
    target.mkdir()
    f = target / "ro.txt"
    f.write_text("x", encoding="utf-8")
    os.chmod(f, stat.S_IREAD)
    try:
        assert installer._robust_rmtree(target) is True
        assert not target.exists()
    finally:
        if target.exists():
            try:
                os.chmod(f, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass


def test_robust_rmtree_missing_path_is_success(tmp_path: Path) -> None:
    assert installer._robust_rmtree(tmp_path / "does-not-exist") is True


# ---------------------------------------------------------------------------
# Fix-4 / Fix-6 / Fix-7: uninstall() return shape
# ---------------------------------------------------------------------------


def test_uninstall_returns_dict_and_purges_data(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    data_root = tmp_path / "plugin_data"
    pid = "purge-me"
    plugin_dir = plugins_dir / pid
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "id": pid,
                "name": pid,
                "version": "0.1.0",
                "type": "python",
                "permissions": list(BASIC_PERMISSIONS),
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        "from openakita.plugins.api import PluginBase\n"
        "class Plugin(PluginBase):\n"
        "    def on_load(self, api): pass\n",
        encoding="utf-8",
    )
    plugin_data = data_root / pid
    plugin_data.mkdir(parents=True)
    (plugin_data / "store.db").write_bytes(b"sqlite-blob")

    result = installer.uninstall(
        pid, plugins_dir, purge_data=True, data_root=data_root
    )
    assert isinstance(result, dict)
    assert result["removed"] is True
    assert result["partial"] is False
    assert result["purged_data"] is True
    assert not plugin_dir.exists()
    assert not plugin_data.exists()


def test_uninstall_unknown_id_is_soft_failure(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    result = installer.uninstall("ghost", plugins_dir)
    assert result["removed"] is False
    assert result["partial"] is False
    assert any("not installed" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Phase 4: PluginState.dev_mode
# ---------------------------------------------------------------------------


def test_plugin_state_dev_mode_roundtrip(tmp_path: Path) -> None:
    state_path = tmp_path / "plugin_state.json"
    state = PluginState()
    assert state.dev_mode == "off"
    assert state.dev_mode_enabled is False

    state.set_dev_mode("symlink")
    assert state.dev_mode_enabled is True
    state.save(state_path)

    reloaded = PluginState.load(state_path)
    assert reloaded.dev_mode == "symlink"
    assert reloaded.dev_mode_enabled is True


def test_plugin_state_dev_mode_rejects_unknown() -> None:
    state = PluginState()
    with pytest.raises(ValueError):
        state.set_dev_mode("hard-link")


def test_plugin_state_dev_mode_unknown_in_file_falls_back_to_off(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "plugin_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "plugins": {},
                "active_backends": {},
                "dev_mode": "garbage",
            }
        ),
        encoding="utf-8",
    )
    loaded = PluginState.load(state_path)
    assert loaded.dev_mode == "off"


# ---------------------------------------------------------------------------
# Sanity: full install_from_path → unload → uninstall round-trip
# ---------------------------------------------------------------------------


async def test_full_lifecycle_via_install_from_path(tmp_path: Path) -> None:
    """End-to-end: install from path, load, unload, uninstall — no leaks."""
    src = tmp_path / "src" / "fake-plugin"
    src.mkdir(parents=True)
    (src / "plugin.json").write_text(
        json.dumps(
            {
                "id": "fake-plugin",
                "name": "Fake",
                "version": "0.1.0",
                "type": "python",
                "permissions": list(BASIC_PERMISSIONS),
            }
        ),
        encoding="utf-8",
    )
    (src / "plugin.py").write_text(
        textwrap.dedent("""\
            from openakita.plugins.api import PluginBase
            class Plugin(PluginBase):
                def on_load(self, api):
                    self._data = api.get_data_dir() / "x.bin"
                    self._data.write_bytes(b"hello")
                def on_unload(self):
                    pass
        """),
        encoding="utf-8",
    )

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    pid = installer.install_from_path(src, plugins_dir)
    assert pid == "fake-plugin"
    assert (plugins_dir / "fake-plugin" / "plugin.json").exists()

    state_path = tmp_path / "plugin_state.json"
    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()
    assert "fake-plugin" in {p["id"] for p in pm.list_loaded()}

    await pm.unload_plugin("fake-plugin")
    pm.state.remove_plugin("fake-plugin")
    pm.state.save(state_path)

    result = installer.uninstall(
        "fake-plugin",
        plugins_dir,
        purge_data=True,
        data_root=plugins_dir.parent / "plugin_data",
    )
    assert result["removed"] is True
    assert not (plugins_dir / "fake-plugin").exists()
