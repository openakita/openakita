"""Sprint 16 P0: lifespan teardown must close plugin aiosqlite workers.

Forensic background — ``_v32_biz_e2e/_diagnostics_analysis.md``:

* PHASEA + UVICORN + ROLLBACK rounds (15/15) all left exactly 14
  ``Thread-NN (_connection_worker_thread)`` non-daemon threads alive in
  the final diagnostics dump.
* The threading.Timer force-exit watchdog kicked in at +15 s and called
  ``os._exit(0)`` — graceful path could never reach ≤10 s SLO.
* Root cause: serve-mode shutdown never ran ``agent.shutdown()`` /
  ``pm.unload_plugin(...)``, so each loaded plugin's ``on_unload``
  (which already contains ``await self._tm.close()``) never fired.

The Sprint 16 fix added an ``@app.on_event("shutdown")`` handler in
``server.py`` that drives ``PluginManager.shutdown()`` on every loaded
plugin. This test pins the contract:

1. Lifespan startup → N aiosqlite worker threads alive (one per loaded
   plugin).
2. ``TestClient`` exit → 0 leaked aiosqlite worker threads (a short
   grace is allowed for ``Thread.join`` to settle).

The full v33 end-to-end smoke (boot a real backend, POST /api/shutdown,
measure ``shutdown_to_exit_s ≤ 10s``) is run separately in
``_v33_biz/``; this in-process integration test gives the same
signal with no subprocess.
"""

from __future__ import annotations

import asyncio
import json
import re
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from openakita.api.server import create_app
from openakita.plugins.manager import PluginManager

_AIOSQLITE_WORKER_NAME = re.compile(
    r"^Thread-\d+ \(_connection_worker_thread\)$"
)


def _count_aiosqlite_workers() -> int:
    return sum(
        1 for t in threading.enumerate() if _AIOSQLITE_WORKER_NAME.match(t.name)
    )


def _wait_for_worker_count(target: int, timeout_s: float = 3.0) -> int:
    deadline = time.monotonic() + timeout_s
    last = _count_aiosqlite_workers()
    while last > target and time.monotonic() < deadline:
        time.sleep(0.05)
        last = _count_aiosqlite_workers()
    return last


def _make_aiosqlite_plugin(base: Path, plugin_id: str) -> None:
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
                    # Schedule the aiosqlite open on the same loop the
                    # plugin manager runs on. The framework drains
                    # tasks scheduled inside ``on_load`` before
                    # ``load_all`` returns, so the connection (and
                    # its non-daemon worker thread) is alive by the
                    # time the test invariant is checked.
                    import asyncio as _aio

                    async def _open() -> None:
                        self._conn = await aiosqlite.connect({db_path!r})

                    self._open_task = _aio.get_event_loop().create_task(_open())

                async def on_unload(self) -> None:
                    # Wait for any in-flight open before close so the
                    # close coroutine sees the real connection (Win
                    # tests have occasionally raced past the open).
                    open_task = getattr(self, "_open_task", None)
                    if open_task is not None and not open_task.done():
                        try:
                            await open_task
                        except Exception:
                            pass
                    if self._conn is not None:
                        await self._conn.close()
                        self._conn = None
            """
        ),
        encoding="utf-8",
    )


class _FakeAgent:
    """Minimal stand-in for the real ``Agent`` that exposes
    ``_plugin_manager`` the way the lifespan shutdown hook expects.

    The real Agent class drags in the entire skill / brain / memory
    stack, which is overkill for a focused lifespan teardown test.
    """

    def __init__(self, pm: PluginManager) -> None:
        self._plugin_manager = pm
        self.brain = None


@pytest.fixture
def aiosqlite_plugins_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a FastAPI app whose ``app.state.agent`` owns a
    ``PluginManager`` that loads N aiosqlite-opening plugins **inside
    the lifespan startup**.

    We *cannot* call ``pm.load_all()`` outside the TestClient lifespan,
    because the aiosqlite connection futures attach to the loop that
    runs the ``await aiosqlite.connect`` call — and that loop is the
    same one the lifespan shutdown uses. Loading in a fresh
    ``asyncio.run`` and then teardown-on-a-different-loop would crash
    inside the close path with ``Future attached to a different loop``.
    """
    # Defuse the force-exit timer: this test does NOT post /api/shutdown,
    # so the timer should never arm — but the env-level grace value is
    # belt-and-braces.
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s",
        0,
        raising=False,
    )

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    plugin_count = 3
    for i in range(plugin_count):
        _make_aiosqlite_plugin(plugins_dir, f"lifespan-aiosqlite-p{i}")

    pm = PluginManager(plugins_dir, state_path=tmp_path / "state.json")
    fake_agent = _FakeAgent(pm)

    app = create_app(agent=fake_agent)

    # Hook plugin load into the lifespan startup so all plugin
    # connections come up inside the loop that owns them; the new
    # ``_shutdown_plugin_aiosqlite_workers`` handler will then close
    # them at lifespan exit on the same loop.
    @app.on_event("startup")
    async def _load_aiosqlite_plugins() -> None:
        await pm.load_all()
        # Two short ticks to let each plugin's on_load-scheduled
        # ``aiosqlite.connect`` task complete on this same loop.
        for _ in range(20):
            await asyncio.sleep(0.05)
            if _count_aiosqlite_workers() >= plugin_count:
                break

    return SimpleNamespace(
        app=app,
        plugin_manager=pm,
        plugin_count=plugin_count,
    )


def test_lifespan_teardown_releases_aiosqlite_workers(aiosqlite_plugins_app):
    """The TestClient lifespan exit must close every plugin's aiosqlite worker.

    Asserts the smoking-gun contract from
    ``_v32_biz_e2e/_diagnostics_analysis.md``: the diagnostics dump
    can only show 0 stale workers if the lifespan handler actually
    closes them.
    """
    plugin_count = aiosqlite_plugins_app.plugin_count
    pm = aiosqlite_plugins_app.plugin_manager
    app = aiosqlite_plugins_app.app

    workers_before_app = _count_aiosqlite_workers()

    # Lifespan startup loads the plugins; verify the invariant inside
    # the with-block, then shutdown is observed on exit.
    with TestClient(app) as client:
        # client unused beyond bringing the lifespan up.
        del client
        assert pm.loaded_count == plugin_count, (
            f"startup invariant: lifespan should have loaded {plugin_count} "
            f"plugins; loaded_count={pm.loaded_count}"
        )
        workers_during = _count_aiosqlite_workers()
        assert workers_during >= workers_before_app + plugin_count, (
            f"startup invariant: each loaded plugin should hold an "
            f"aiosqlite worker; before_app={workers_before_app}, "
            f"during={workers_during}, plugin_count={plugin_count}"
        )

    # After TestClient exit, the lifespan ``shutdown`` handler chain
    # has fully run. Plugin manager should be empty AND every aiosqlite
    # worker spawned by the fixture should have been joined.
    assert pm.loaded_count == 0, (
        f"PluginManager.shutdown should have unloaded all plugins; "
        f"loaded_count={pm.loaded_count}"
    )

    # Allow Thread.join up to 3s — close() returns once the worker has
    # been signalled but the OS thread may take a tick to actually exit.
    workers_after = _wait_for_worker_count(workers_before_app, timeout_s=3.0)
    leaked = workers_after - workers_before_app
    assert leaked <= 0, (
        f"lifespan teardown leaked {leaked} aiosqlite worker thread(s); "
        f"before_app={workers_before_app}, after={workers_after}, "
        f"plugin_count={plugin_count}"
    )


def test_lifespan_teardown_safe_when_no_plugin_manager(monkeypatch):
    """Missing ``app.state.agent._plugin_manager`` must not break shutdown.

    Headless CLI invocations and unit-test fixtures often instantiate
    ``create_app(agent=None)``. The new plugin-teardown handler must
    gracefully no-op in that case.
    """
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s",
        0,
        raising=False,
    )

    app = create_app(agent=None)

    with TestClient(app):
        pass
    # If shutdown raised, TestClient.__exit__ would re-raise; reaching
    # this line is the contract.
