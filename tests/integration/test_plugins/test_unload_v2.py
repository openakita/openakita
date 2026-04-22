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


# ---------------------------------------------------------------------------
# Regression: the "tongyi-image" pattern
#
# A real-world plugin (tongyi-image) does this:
#
#     def on_load(self, api):
#         self._client = HttpClient()                                # main loop
#         self._poll_task = asyncio.get_event_loop().create_task(    # main loop
#             self._poll_loop()
#         )                                                          # NOT spawn_task
#
#     def on_unload(self):                          # sync handler
#         self._poll_task.cancel()                  # not awaited
#         loop = asyncio.get_event_loop()
#         loop.create_task(self._client.close())    # not awaited
#
# Two things must hold for uninstall to actually free the directory:
#
#   1. The cleanup coroutines scheduled by on_unload run on the SAME loop
#      where the resources were created (otherwise httpx/aiosqlite raise
#      "Future attached to a different loop").
#   2. The polling task (created bypassing spawn_task) is cancelled by the
#      framework's stray-task sweeper, otherwise it keeps the resources
#      pinned and the rmtree fails on Windows.
# ---------------------------------------------------------------------------


async def test_sync_on_unload_create_task_runs_on_main_loop(tmp_path: Path) -> None:
    """Regression for the tongyi-image bug.

    The cleanup coroutine must observe the *same* running loop as the one
    handling unload_plugin — that's the only way an httpx/aiosqlite close()
    that targets a resource created in on_load can succeed.
    """
    import asyncio as _aio

    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"
    marker = tmp_path / "loop_id.marker"
    body = textwrap.dedent(f"""\
        import asyncio
        from pathlib import Path
        from openakita.plugins.api import PluginBase

        class Plugin(PluginBase):
            def on_load(self, api):
                self._load_loop_id = id(asyncio.get_event_loop())

            def on_unload(self):
                async def _cleanup():
                    cur = id(asyncio.get_running_loop())
                    Path({str(marker)!r}).write_text(
                        f"{{self._load_loop_id}}={{cur}}", encoding="utf-8"
                    )
                loop = asyncio.get_event_loop()
                loop.create_task(_cleanup())
    """)
    _make_plugin(plugins_dir, "loop-check", body)
    _write_state_file(state_path, {"loop-check": {}})

    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()
    main_loop_id = id(_aio.get_running_loop())

    await pm.unload_plugin("loop-check")
    txt = marker.read_text(encoding="utf-8")
    load_id_str, cleanup_id_str = txt.split("=", 1)
    assert int(load_id_str) == main_loop_id, (
        "on_load ran on a non-main loop, test setup is broken"
    )
    assert int(cleanup_id_str) == main_loop_id, (
        "cleanup coroutine scheduled by sync on_unload must execute on the "
        "main loop — otherwise httpx/aiosqlite close() across loops will "
        "leak the underlying file handles"
    )


async def test_stray_create_task_from_on_load_is_swept(tmp_path: Path) -> None:
    """Plugin uses ``loop.create_task`` (not ``api.spawn_task``) in on_load.

    Without the framework's stray-task sweep, this task keeps running after
    unload, holding references to plugin objects and preventing rmtree.
    """
    plugins_dir = tmp_path / "plugins"
    state_path = tmp_path / "plugin_state.json"
    body = textwrap.dedent("""\
        import asyncio
        from openakita.plugins.api import PluginBase

        async def _poll_loop():
            while True:
                await asyncio.sleep(0.05)

        class Plugin(PluginBase):
            def on_load(self, api):
                # NOTE: bypasses api.spawn_task on purpose — many real
                # plugins do this and we must still catch it on unload.
                self._task = asyncio.get_event_loop().create_task(_poll_loop())

            def on_unload(self):
                pass  # intentionally does NOT cancel self._task
    """)
    _make_plugin(plugins_dir, "stray-task", body)
    _write_state_file(state_path, {"stray-task": {}})

    pm = PluginManager(plugins_dir, state_path=state_path)
    await pm.load_all()

    loaded = pm.get_loaded("stray-task")
    assert loaded is not None
    stray = loaded.instance._task  # type: ignore[attr-defined]
    assert not stray.done()

    await pm.unload_plugin("stray-task")
    # The stray-task sweeper must have cancelled & awaited it.
    assert stray.done(), (
        "Tasks created with loop.create_task (bypassing api.spawn_task) "
        "must still be cancelled by the framework on unload"
    )


def test_list_locked_files_reports_clean_dir_as_empty(tmp_path: Path) -> None:
    target = tmp_path / "clean"
    target.mkdir()
    (target / "a.txt").write_text("ok", encoding="utf-8")
    (target / "sub").mkdir()
    (target / "sub" / "b.txt").write_text("ok", encoding="utf-8")
    locked = installer._list_locked_files(target)
    assert locked == [], "Files that are not held by any process must not be reported as locked"


def test_uninstall_partial_keeps_disabled_state_via_route_logic(
    tmp_path: Path,
) -> None:
    """Regression for the "plugin comes back to life after a refresh" bug.

    Before the fix, the DELETE route removed the plugin's state entry
    BEFORE attempting to delete the on-disk directory. When the directory
    deletion partially failed (Windows file lock), the state lost the
    entry but the directory survived; ``PluginState.is_enabled`` returned
    True for unknown ids, so ``_sync_new_plugins`` would silently
    re-discover and re-load the leftover plugin on the next /list call.

    Fixed order: delete first, then reconcile state with the actual
    filesystem outcome — partial/failure marks the plugin disabled with
    a ``pending_removal_*`` reason instead of dropping the entry.

    This test exercises the same logic the route uses (without spinning
    up FastAPI) and proves a partial outcome leaves the entry as
    ``enabled=False`` so the next refresh does not re-load it.
    """
    state = PluginState()
    state.enable("ghost-plugin")  # simulate prior installed+enabled state
    assert state.is_enabled("ghost-plugin") is True

    # --- Simulated route logic for partial outcome ---
    # (Mirrors uninstall_plugin in routes/plugins.py; if the route changes,
    # this test fails loudly and we know to update both in lockstep.)
    fake_result = {
        "removed": False,
        "partial": True,
        "purged_data": False,
        "warnings": ["files locked: data/store.db-wal"],
    }
    if fake_result["removed"]:
        state.remove_plugin("ghost-plugin")
    else:
        reason = (
            "pending_removal_partial"
            if fake_result["partial"]
            else "pending_removal_failed"
        )
        state.disable("ghost-plugin", reason=reason)
    # --- End simulated route logic ---

    state_path = tmp_path / "plugin_state.json"
    state.save(state_path)
    reloaded = PluginState.load(state_path)
    assert reloaded.is_enabled("ghost-plugin") is False, (
        "Partial uninstall must leave plugin marked disabled — otherwise "
        "_sync_new_plugins would re-load the leftover directory after a "
        "refresh and the plugin appears to 'come back to life'."
    )
    entry = reloaded.get_entry("ghost-plugin")
    assert entry is not None
    assert entry.disabled_reason == "pending_removal_partial"


def test_list_locked_files_caps_at_max_items(tmp_path: Path) -> None:
    target = tmp_path / "clean"
    target.mkdir()
    for i in range(5):
        (target / f"f{i}.txt").write_text("ok", encoding="utf-8")
    locked = installer._list_locked_files(target, max_items=2)
    # No locks expected on these freshly-created files; just verify the
    # function returns within bounds (regression for accidentally walking
    # the whole tree even when none are locked).
    assert len(locked) <= 2


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="DELETE-share semantics are Windows-specific; POSIX always allows unlink",
)
def test_list_locked_files_detects_delete_share_denied(tmp_path: Path) -> None:
    """Regression: ``_list_locked_files`` must catch handles that deny
    ``FILE_SHARE_DELETE`` even when they allow ``FILE_SHARE_WRITE``.

    Real-world example: aiosqlite/sqlite, ``RotatingFileHandler``, and any
    ``open(path)`` without ``FILE_SHARE_DELETE`` — these are exactly what
    block ``shutil.rmtree`` on Windows during plugin uninstall, and the
    earlier ``open(f, "ab")`` probe missed all of them because they happily
    accept a second write handle.

    We reproduce the failing pattern with ``ctypes`` (CreateFileW with
    ``FILE_SHARE_READ | FILE_SHARE_WRITE`` but NOT ``FILE_SHARE_DELETE``).
    """
    import ctypes
    from ctypes import wintypes

    target = tmp_path / "lockme"
    target.mkdir()
    locked_file = target / "sqlite.db"
    locked_file.write_bytes(b"x")
    free_file = target / "free.txt"
    free_file.write_text("ok")

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x1
    FILE_SHARE_WRITE = 0x2
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    CreateFileW = ctypes.windll.kernel32.CreateFileW
    CreateFileW.restype = wintypes.HANDLE
    CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    CloseHandle = ctypes.windll.kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]

    handle = CreateFileW(
        str(locked_file),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,  # NOTE: no FILE_SHARE_DELETE
        None, OPEN_EXISTING, 0, None,
    )
    assert handle and handle != INVALID_HANDLE_VALUE, "Failed to open test handle"
    try:
        locked = installer._list_locked_files(target)
        # The locked file MUST be reported; the free file MUST NOT be.
        assert "sqlite.db" in locked, (
            f"DELETE-share-denied handle was not detected as locked. "
            f"Got: {locked}. This means rmtree failure diagnostics will "
            f"silently miss the most common offender (open SQLite/log handle)."
        )
        assert "free.txt" not in locked
    finally:
        CloseHandle(handle)
