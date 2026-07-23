"""Sprint 14 / v31 Phase A + Sprint 15 / v32 Phase B regression:
``POST /api/shutdown`` must return immediately AND arm an ``os._exit(0)``
safety net so a wedged graceful path can no longer pin the process for
13~20 s.

Forensic background — see ``_v31_biz_e2e/_phase_a_post_fix_forensics.md``
and ``_v32_biz/_phase_b_watchdog_redesign.md``:

* v23/v24/v26/v28/v29/v30 reproduced ``POST /api/shutdown`` returning
  200 but the process never self-exiting in time, forcing a manual
  ``Stop-Process`` every regression.
* v31 added an ``asyncio.create_task`` watchdog; it ARMED in 4/4 runs
  but FIRED in 0/4 because uvicorn lifespan teardown cancels every
  pending asyncio task.
* v32 (this commit) replaces the watchdog with ``threading.Timer``
  which has no asyncio handle and survives lifespan teardown.

These tests pin three route-level guarantees:

1. ``POST /api/shutdown`` returns 200 ``shutting_down`` quickly.
2. The route arms the force-exit watchdog (visible on ``app.state``).
3. ``shutdown_force_exit_grace_s = 0`` disables the safety net.

Threading vs. legacy-asyncio mechanism-level tests live in
``test_force_exit_watchdog_threading.py`` so this file stays focused on
the HTTP route contract.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openakita.api.server import create_app


@pytest.fixture
def shutdown_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """A real FastAPI app with a fresh ``shutdown_event`` wired in.

    The ``app.state.shutdown_event`` must exist for the force-exit safety
    net to arm; ``create_app(...)`` accepts it as a kwarg.

    Three access gates need satisfying for the TestClient to reach the
    handler:

    1. The setup gate: proxied requests are intentionally not trusted as
       local, so the fixture completes setup in an isolated data directory.
    2. The auth middleware: TestClient's host is ``testclient`` (not
       ``127.0.0.1``) so we mint a real access token, mirroring how the
       desktop GUI authenticates.
    3. The route's own ``_is_local_request``: the ``/api/shutdown``
       handler 403s non-localhost callers. Setting ``TRUST_PROXY=1`` plus
       a ``X-Forwarded-For: 127.0.0.1`` header makes ``get_client_ip``
       resolve to localhost without hard-coding test plumbing into prod.

    Sprint 15 / v32 Phase B defence-in-depth: ``os._exit`` is patched
    inside this fixture so a regression where a test arms the
    threading.Timer watchdog and forgets to cancel it cannot kill the
    pytest process partway through the suite (we hit exactly that
    failure during v32 fixture porting; see
    ``_v32_biz/_phase_b_watchdog_redesign.md``).
    """
    monkeypatch.setenv("TRUST_PROXY", "1")
    monkeypatch.setattr("openakita.api.server.os._exit", lambda code=0: None)
    monkeypatch.setattr("openakita.config.settings.project_root", tmp_path)
    shutdown_event = asyncio.Event()
    app = create_app(shutdown_event=shutdown_event)
    app.state.web_access_config.change_password("shutdown-test-password")
    token = app.state.web_access_config.create_access_token()
    client = TestClient(app)
    client.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "X-Forwarded-For": "127.0.0.1",
        }
    )
    try:
        yield client
    finally:
        client.close()
        # Belt-and-braces: cancel any timer left armed by the test so
        # the daemon thread does not linger past the suite.
        task = getattr(app.state, "_force_exit_task", None)
        cancel = getattr(task, "cancel", None) if task is not None else None
        if callable(cancel):
            cancel()


def test_shutdown_endpoint_returns_immediately(
    shutdown_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/shutdown must respond ≤ 500ms with the grace watchdog
    armed (arming is just a ``threading.Timer.start`` — no await).
    """
    # Use a long grace so the timer cannot fire mid-suite. v32's
    # threading.Timer is **intentionally non-cancellable on the
    # graceful path** — see ``_arm_force_exit_watchdog_sync`` docstring.
    # Tests cannot rely on the asyncio-cancel-on-teardown side effect
    # the v31 implementation had; we MUST explicitly cancel the
    # ``threading.Timer`` in ``finally`` so it does not later fire
    # ``os._exit(0)`` and silently kill the whole pytest process.
    monkeypatch.setattr("openakita.config.settings.shutdown_force_exit_grace_s", 30, raising=False)

    app = shutdown_app.app  # type: ignore[attr-defined]
    try:
        started = time.monotonic()
        response = shutdown_app.post("/api/shutdown")
        elapsed = time.monotonic() - started

        assert response.status_code == 200, response.text
        assert response.json() == {"status": "shutting_down"}
        assert elapsed < 0.5, f"shutdown response took {elapsed:.3f}s (expected <0.5s)"
    finally:
        task = getattr(app.state, "_force_exit_task", None)
        cancel = getattr(task, "cancel", None) if task is not None else None
        if callable(cancel):
            cancel()


def test_shutdown_endpoint_arms_force_exit_watchdog(
    shutdown_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route must arm the force-exit watchdog on ``app.state``.

    ``app.state._force_exit_task`` is set by
    :func:`_schedule_force_exit_after_grace` and is the load-bearing
    handle the second-`/api/shutdown` idempotency check uses. Under the
    v32 threading default it holds a ``threading.Timer``; under the
    legacy asyncio path it holds an ``asyncio.Task``. This test asserts
    only that it is set — mechanism-specific asserts live in
    ``test_force_exit_watchdog_threading.py``.

    A long grace (30s) keeps the timer pending past test teardown; we
    cancel it explicitly in ``finally`` so the test process does not
    end up holding a stray daemon thread sleeping for 30s after the
    suite finishes.
    """
    monkeypatch.setattr("openakita.config.settings.shutdown_force_exit_grace_s", 30, raising=False)

    app = shutdown_app.app  # type: ignore[attr-defined]
    try:
        response = shutdown_app.post("/api/shutdown")
        assert response.status_code == 200, response.text

        task = getattr(app.state, "_force_exit_task", None)
        assert task is not None, "force-exit watchdog should be armed"
    finally:
        task = getattr(app.state, "_force_exit_task", None)
        cancel = getattr(task, "cancel", None) if task is not None else None
        if callable(cancel):
            cancel()


def test_shutdown_endpoint_disabled_when_grace_zero(
    shutdown_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``shutdown_force_exit_grace_s = 0`` must disable the safety net.

    Diagnostic / debug operators occasionally need this so the process
    cannot ``os._exit`` itself out from under their forensics. We verify
    the task is not armed and the route still returns 200.
    """
    monkeypatch.setattr("openakita.config.settings.shutdown_force_exit_grace_s", 0, raising=False)

    response = shutdown_app.post("/api/shutdown")
    assert response.status_code == 200, response.text

    app = shutdown_app.app  # type: ignore[attr-defined]
    assert getattr(app.state, "_force_exit_task", None) is None
