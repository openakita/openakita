"""Sprint 16 P0: regression guard for the lifespan→exit aiosqlite hang fix.

Forensics: ``_v32_biz_e2e/_diagnostics_analysis.md`` showed that every
shutdown round left 14 non-daemon ``Thread-NN (_connection_worker_thread)``
threads alive — one per plugin TaskManager that opened an aiosqlite
connection in ``on_load`` and never had its ``on_unload`` invoked
because serve-mode shutdown never called ``pm.unload_plugin(...)``.

The fix added ``PluginManager.unload_all_plugins`` and wired it into the
FastAPI lifespan shutdown. These tests pin two contract claims:

* ``unload_all_plugins`` actually triggers each plugin's ``on_unload``
  so plugin-owned async resources (aiosqlite TaskManager, httpx client,
  …) can release their non-daemon worker threads.
* ``shutdown(unload_plugins=True)`` is the public entry point used by
  the lifespan hook, and it composes ``unload_all_plugins`` with
  ``AssetBus.close`` without raising on partial failure.
"""

from __future__ import annotations

import asyncio
import json
import re
import textwrap
import threading
import time

from openakita.plugins.manager import PluginManager

# Pattern used by the shutdown diagnostics module to identify aiosqlite
# worker threads. Matches names like ``Thread-19 (_connection_worker_thread)``.
_AIOSQLITE_WORKER_NAME = re.compile(
    r"^Thread-\d+ \(_connection_worker_thread\)$"
)


def _count_aiosqlite_workers() -> int:
    return sum(
        1 for t in threading.enumerate() if _AIOSQLITE_WORKER_NAME.match(t.name)
    )


def _wait_for_worker_count(target: int, timeout_s: float = 3.0) -> int:
    """Poll ``threading.enumerate`` until the worker count reaches target."""
    deadline = time.monotonic() + timeout_s
    last = _count_aiosqlite_workers()
    while last != target and time.monotonic() < deadline:
        time.sleep(0.05)
        last = _count_aiosqlite_workers()
    return last


def _make_aiosqlite_plugin(base, plugin_id: str) -> None:
    """Build a plugin whose ``on_load`` opens an aiosqlite connection.

    Mirrors the real-world TaskManager pattern that v32 forensics
    identified as the 14×stale-thread source — each plugin's
    ``on_load`` synchronously schedules ``await aiosqlite.connect``
    and pins the resulting connection on ``self._conn``; ``on_unload``
    is supposed to close it.
    """
    d = base / plugin_id
    d.mkdir(parents=True, exist_ok=True)
    db_path = (d / f"{plugin_id}.db").as_posix()
    (d / "plugin.json").write_text(
        json.dumps(
            {
                "id": plugin_id,
                "name": plugin_id,
                "version": "1.0.0",
                "type": "python",
                "permissions": ["tools.register"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (d / "plugin.py").write_text(
        textwrap.dedent(
            f"""\
            import aiosqlite

            from openakita.plugins.api import PluginAPI, PluginBase


            class Plugin(PluginBase):
                _conn = None

                def on_load(self, api: PluginAPI) -> None:
                    # Schedule the aiosqlite open on the same event loop the
                    # plugin manager runs on; PluginManager awaits the
                    # scheduled task as part of on_load drain so the
                    # connection (and its non-daemon worker thread) is
                    # alive by the time load_all returns.
                    import asyncio as _aio

                    async def _open() -> None:
                        self._conn = await aiosqlite.connect({db_path!r})

                    _aio.get_event_loop().create_task(_open())

                async def on_unload(self) -> None:
                    if self._conn is not None:
                        await self._conn.close()
                        self._conn = None
            """
        ),
        encoding="utf-8",
    )


# ---------- PluginManager.unload_all_plugins ----------


class TestUnloadAllAiosqlite:
    async def test_unload_all_closes_aiosqlite_worker_threads(self, tmp_path):
        """Every plugin's ``on_unload`` must run so its aiosqlite worker exits.

        Asserts the smoking-gun contract: ``threading.enumerate``
        contains N aiosqlite ``_connection_worker_thread`` entries
        after ``load_all`` (one per plugin), and 0 after
        ``unload_all_plugins`` (a brief join grace is allowed
        because Thread.join is asynchronous).
        """
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        plugin_count = 3
        for i in range(plugin_count):
            _make_aiosqlite_plugin(plugins_dir, f"aiosqlite-p{i}")

        before = _count_aiosqlite_workers()

        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        # Let each plugin's on_load-scheduled ``aiosqlite.connect`` task
        # settle so the worker thread is observable.
        await asyncio.sleep(0.1)
        assert mgr.loaded_count == plugin_count

        loaded_workers = _count_aiosqlite_workers()
        assert loaded_workers >= before + plugin_count, (
            f"expected each plugin to spawn an aiosqlite worker thread; "
            f"got {loaded_workers - before} new workers for {plugin_count} plugins"
        )

        unloaded = await mgr.unload_all_plugins()
        assert unloaded == plugin_count, (
            f"unload_all_plugins should return number of plugins that "
            f"actually unloaded; got {unloaded}, expected {plugin_count}"
        )
        assert mgr.loaded_count == 0

        # Threads join asynchronously after close() — give them a
        # generous grace window. The deterministic claim is "back to
        # the pre-load baseline", not "absolute zero" (other tests in
        # the suite may have leaked their own connections).
        final = _wait_for_worker_count(before, timeout_s=3.0)
        assert final <= before, (
            f"aiosqlite worker count did not return to baseline; "
            f"before={before}, after_unload={final}, leaked="
            f"{final - before}"
        )

    async def test_unload_all_tolerates_individual_plugin_failure(self, tmp_path):
        """One plugin's ``on_unload`` raising must not block the others.

        Lifespan teardown must never stall on a single misbehaving
        plugin; the contract is "best-effort", with logged warnings
        for failures.
        """
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_aiosqlite_plugin(plugins_dir, "good-p")

        # Build a plugin whose on_unload raises.
        bad_dir = plugins_dir / "bad-p"
        bad_dir.mkdir()
        (bad_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "id": "bad-p",
                    "name": "bad-p",
                    "version": "1.0.0",
                    "type": "python",
                    "permissions": ["tools.register"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (bad_dir / "plugin.py").write_text(
            textwrap.dedent(
                """\
                from openakita.plugins.api import PluginAPI, PluginBase


                class Plugin(PluginBase):
                    def on_load(self, api: PluginAPI) -> None:
                        api.log("loaded")

                    async def on_unload(self) -> None:
                        raise RuntimeError("intentional on_unload failure")
                """
            ),
            encoding="utf-8",
        )

        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        await mgr.load_all()
        await asyncio.sleep(0.1)
        assert mgr.loaded_count == 2

        # The bad plugin's on_unload raises, but unload_plugin swallows
        # the exception internally and proceeds with cleanup, so this
        # still returns 2 (both plugins removed from _loaded).
        unloaded = await mgr.unload_all_plugins()
        assert unloaded == 2
        assert mgr.loaded_count == 0


# ---------- PluginManager.shutdown ----------


class TestShutdownComposition:
    async def test_shutdown_unloads_plugins_and_closes_asset_bus(self, tmp_path):
        """``shutdown(unload_plugins=True)`` (default) must run both halves.

        Lifespan hook uses the no-argument form; the contract is one
        atomic teardown that closes plugin connections + the host
        Asset Bus.
        """
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_aiosqlite_plugin(plugins_dir, "shutdown-p")

        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        # Touch the asset bus so it is initialised (otherwise the
        # close path is a no-op and we cannot pin it).
        bus = mgr.asset_bus
        await bus.init()
        assert bus._db is not None

        await mgr.load_all()
        await asyncio.sleep(0.1)
        assert mgr.loaded_count == 1

        await mgr.shutdown()

        assert mgr.loaded_count == 0
        # Asset Bus connection must be released after shutdown.
        assert bus._db is None

    async def test_shutdown_with_unload_plugins_false_only_closes_asset_bus(
        self, tmp_path
    ):
        """Opt-out keeps plugins loaded but still closes the Asset Bus.

        ``Agent.shutdown`` iterates plugins itself before calling
        ``pm.shutdown(unload_plugins=False)`` would be the natural
        composition (when that wiring lands); we pin the opt-out
        semantics here so a future caller does not get a double-unload.
        """
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        _make_aiosqlite_plugin(plugins_dir, "keep-loaded-p")

        mgr = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
        bus = mgr.asset_bus
        await bus.init()
        assert bus._db is not None

        await mgr.load_all()
        await asyncio.sleep(0.1)
        assert mgr.loaded_count == 1

        await mgr.shutdown(unload_plugins=False)

        # Plugin still loaded.
        assert mgr.loaded_count == 1
        # But Asset Bus released.
        assert bus._db is None

        # Cleanup so the test's aiosqlite worker thread exits before
        # the suite tears down.
        await mgr.unload_all_plugins()
