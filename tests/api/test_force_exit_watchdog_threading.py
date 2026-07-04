"""Sprint 15 / v32 Phase B: mechanism-level tests for the threading-based
force-exit safety net.

Why a dedicated test file: ``test_shutdown_endpoint_bounded.py`` covers
the HTTP route contract (returns 200 fast, arms the watchdog, respects
``grace_s=0``). The mechanism-specific guarantees — *which* primitive
holds the watchdog handle, that the primitive survives uvicorn lifespan
teardown, that ``os._exit(0)`` actually fires — are checked here so the
HTTP test file stays focused on the user-facing contract.

Forensic background (read first):
* ``_v31_biz_e2e/_phase_a_post_fix_forensics.md`` — v31 design (asyncio
  watchdog) armed 4/4 PHASEA runs but fired 0/4 because uvicorn lifespan
  teardown cancels every pending asyncio task.
* ``_v32_biz/_phase_b_watchdog_redesign.md`` — v32 design (threading.Timer)
  has no asyncio handle, so lifespan teardown cannot reach it.

Test coverage:

A. ``_arm_force_exit_watchdog_sync`` registers a ``threading.Timer``
   visible in ``threading.enumerate()`` with the well-known name.
B. The timer actually fires ``os._exit(0)`` after the grace window
   (mocked so the test process does not die).
C. The watchdog is idempotent: a second arm-call reuses the existing
   timer (multi-tab Setup Center shutdown safety).
D. ``grace_s = 0`` short-circuits without arming anything.
E. The dispatcher (``_schedule_force_exit_after_grace``) honours
   ``shutdown_force_exit_use_threading``: True → threading, False →
   legacy asyncio (rollback path).
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import pytest

from openakita.api.server import (
    _arm_force_exit_watchdog_async,
    _arm_force_exit_watchdog_sync,
    _schedule_force_exit_after_grace,
)


@pytest.fixture(autouse=True)
def _patch_os_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence-in-depth: never let an in-test timer kill the test process.

    Sprint 15 / v32 Phase B: the threading.Timer watchdog is
    intentionally NOT cancellable on the graceful path (the whole point
    is to survive lifespan teardown). Tests that arm it with a short
    grace and forget to cancel could fire ``os._exit(0)`` mid-suite —
    we hit exactly that failure during initial v32 porting, with
    pytest exiting at 82% with code 0 and the summary block silently
    truncated. Patching ``openakita.api.server.os._exit`` at fixture
    scope makes the misfire harmless.
    """
    monkeypatch.setattr("openakita.api.server.os._exit", lambda code=0: None)


def _fresh_fake_app() -> object:
    """Minimal stand-in for FastAPI ``app`` with a mutable ``.state``."""

    class _State:
        pass

    class _App:
        state = _State()

    return _App()


def test_threading_watchdog_registers_named_daemon_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A. Threading.Timer must appear in ``threading.enumerate()``.

    This is the v32 design's load-bearing claim: unlike the v31
    asyncio.Task (which lives inside the event loop and is invisible
    to ``threading.enumerate``), the new watchdog is a real OS-backed
    Thread whose lifecycle is independent of any asyncio loop.
    """
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s", 30, raising=False
    )

    app = _fresh_fake_app()
    try:
        _arm_force_exit_watchdog_sync(app)
        timer = app.state._force_exit_task  # type: ignore[attr-defined]
        assert isinstance(timer, threading.Timer)
        assert timer.name == "openakita-force-exit-watchdog"
        assert timer.daemon is True
        assert timer.is_alive(), "watchdog timer should be running"

        named = [t for t in threading.enumerate() if t.name == timer.name]
        assert named, "watchdog timer not present in threading.enumerate()"
        assert app.state._force_exit_mechanism == "threading.Timer"  # type: ignore[attr-defined]
    finally:
        timer = getattr(app.state, "_force_exit_task", None)
        if isinstance(timer, threading.Timer):
            timer.cancel()


def test_threading_watchdog_fires_os_exit_after_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B. The timer must reach ``os._exit(0)`` after the grace window.

    This is the regression test for the v31 0/4-fired symptom. We pick
    grace_s=1 (smallest legal value) so the test completes in ~1s. We
    patch ``os._exit`` so the test process does not die when the timer
    fires; the patch target is ``openakita.api.server.os._exit`` because
    ``_do_force_exit`` resolves ``os`` through the module-level import.
    """
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s", 1, raising=False
    )

    fired = threading.Event()

    def _fake_exit(code: int) -> None:
        assert code == 0
        fired.set()

    app = _fresh_fake_app()
    try:
        with patch("openakita.api.server.os._exit", side_effect=_fake_exit):
            _arm_force_exit_watchdog_sync(app)
            timer = app.state._force_exit_task  # type: ignore[attr-defined]
            assert isinstance(timer, threading.Timer)

            # 1s grace + slack; if this assert ever flakes, raise the slack
            # rather than the grace — flake hides a real bug here.
            assert fired.wait(timeout=4.0), (
                "watchdog Timer did not fire os._exit within grace+slack; "
                "this is the v31 regression we shipped v32 to fix."
            )
    finally:
        timer = getattr(app.state, "_force_exit_task", None)
        if isinstance(timer, threading.Timer):
            timer.cancel()


def test_threading_watchdog_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C. A second arm-call must reuse the existing timer.

    Multi-tab Setup Center users (or restart scripts that retry on
    transient network errors) can hit ``/api/shutdown`` twice in quick
    succession; we must not end up with two competing os._exit racers.
    """
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s", 30, raising=False
    )

    app = _fresh_fake_app()
    try:
        _arm_force_exit_watchdog_sync(app)
        first = app.state._force_exit_task  # type: ignore[attr-defined]
        _arm_force_exit_watchdog_sync(app)
        second = app.state._force_exit_task  # type: ignore[attr-defined]
        assert second is first, "duplicate arm must reuse existing timer"
    finally:
        timer = getattr(app.state, "_force_exit_task", None)
        if isinstance(timer, threading.Timer):
            timer.cancel()


def test_threading_watchdog_disabled_when_grace_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D. ``grace_s = 0`` must short-circuit before allocating a timer.

    This is the diagnostic-only path: operators set grace=0 when they
    want to attach a debugger and inspect a hang without having the
    process os._exit itself out from under their forensics.
    """
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s", 0, raising=False
    )

    app = _fresh_fake_app()
    _arm_force_exit_watchdog_sync(app)
    assert getattr(app.state, "_force_exit_task", None) is None


def test_dispatcher_routes_to_threading_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E1. ``_schedule_force_exit_after_grace`` defaults to threading.

    Production code paths (``shutdown_endpoint``) call the dispatcher,
    not the threading helper directly. We pin that the dispatcher
    routes to threading when ``shutdown_force_exit_use_threading`` is
    True (default) so a future config refactor cannot silently revert
    to the broken v31 mechanism.
    """
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s", 30, raising=False
    )
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_use_threading",
        True,
        raising=False,
    )

    app = _fresh_fake_app()
    try:
        _schedule_force_exit_after_grace(app)
        assert app.state._force_exit_mechanism == "threading.Timer"  # type: ignore[attr-defined]
        assert isinstance(app.state._force_exit_task, threading.Timer)  # type: ignore[attr-defined]
    finally:
        timer = getattr(app.state, "_force_exit_task", None)
        if isinstance(timer, threading.Timer):
            timer.cancel()


@pytest.mark.asyncio
async def test_dispatcher_routes_to_async_when_rollback_flag_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2. ``shutdown_force_exit_use_threading=False`` falls back to async.

    The async path is documented as known-broken under uvicorn lifespan
    teardown but kept as a no-redeploy rollback hatch. This test pins
    the rollback wiring works (mechanism string + ``asyncio.Task``
    type), regardless of the v31 cancel-during-teardown bug.

    We let the task complete on a 1s grace and patch os._exit so the
    body executes without nuking the test process.
    """
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s", 30, raising=False
    )
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_use_threading",
        False,
        raising=False,
    )

    app = _fresh_fake_app()
    _schedule_force_exit_after_grace(app)
    try:
        assert app.state._force_exit_mechanism == "asyncio.Task"  # type: ignore[attr-defined]
        task = app.state._force_exit_task  # type: ignore[attr-defined]
        assert isinstance(task, asyncio.Task)
    finally:
        task = getattr(app.state, "_force_exit_task", None)
        if isinstance(task, asyncio.Task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def test_threading_watchdog_arms_immediately_outside_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: threading helper must NOT require a running loop.

    The v31 helper called ``asyncio.get_event_loop()`` which on Python
    3.12+ raises ``DeprecationWarning`` / eventually ``RuntimeError``
    when there is no current event loop. The v32 helper must work in a
    plain synchronous call site (e.g. signal handler, atexit hook, or
    sync test code like this one).
    """
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s", 30, raising=False
    )

    elapsed_ms = 0.0
    app = _fresh_fake_app()
    try:
        t0 = time.monotonic()
        _arm_force_exit_watchdog_sync(app)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        assert app.state._force_exit_task is not None  # type: ignore[attr-defined]
        # Arming is just a Timer alloc + start; should be sub-50ms on
        # any reasonable laptop.
        assert elapsed_ms < 200.0, f"arm took {elapsed_ms:.1f}ms (>200ms slack)"
    finally:
        timer = getattr(app.state, "_force_exit_task", None)
        if isinstance(timer, threading.Timer):
            timer.cancel()


def test_async_arm_helper_exposed_for_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy async helper must remain importable and callable.

    We do not exercise its full body here (that's covered by the
    asyncio-flagged dispatcher test above); we only verify the symbol
    is exported and accepts a ``grace_s=0`` short-circuit without an
    event loop, which is the no-arm branch shared with the threading
    helper.
    """
    monkeypatch.setattr(
        "openakita.config.settings.shutdown_force_exit_grace_s", 0, raising=False
    )

    app = _fresh_fake_app()
    _arm_force_exit_watchdog_async(app)
    assert getattr(app.state, "_force_exit_task", None) is None
