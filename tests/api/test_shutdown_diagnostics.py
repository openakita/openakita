"""Sprint 15 / v32 Phase B Task C: tests for the lifespan→exit
thread-dump diagnostics module.

The forensics target is a runtime hang (~13s between lifespan completion
and actual process exit) that v31 PHASEA runs reproduced 4/4. We cannot
directly assert "shutdown completed in ≤10s" in a unit test (that needs
a real subprocess + IM gateway), but we CAN verify:

A. Import works and the public surface is intact.
B. ``arm_shutdown_diagnostics`` is idempotent (lifespan replay safety).
C. A snapshot log file is created with the documented header format.
D. The periodic dump loop fires at least one snapshot in the configured
   interval window.
E. ``arm_shutdown_diagnostics`` registers an atexit hook (verified via
   patched ``atexit.register``).
F. Tear-down (``stop_shutdown_diagnostics``) cleanly stops the daemon.

The full e2e validation (does v32 actually shut down ≤10s with this
wired into the lifespan?) is deferred to ``_v32_biz`` regression
scripts; this file only validates the module contract.
"""

from __future__ import annotations

import atexit
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from openakita.api import _shutdown_diagnostics as diag


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Each test gets a clean diagnostics module (idempotency tests
    explicitly arm twice — but always from a known-clean baseline).
    """
    diag.stop_shutdown_diagnostics(join_timeout_s=2.0)
    yield
    diag.stop_shutdown_diagnostics(join_timeout_s=2.0)


def test_a_public_surface_intact() -> None:
    """A. The module must export the documented entry points.

    Server.py calls ``arm_shutdown_diagnostics`` from a lifespan
    shutdown hook; tests need ``stop_shutdown_diagnostics`` /
    ``is_armed`` / ``get_log_path`` to reset between cases.
    """
    for name in (
        "arm_shutdown_diagnostics",
        "stop_shutdown_diagnostics",
        "is_armed",
        "get_log_path",
    ):
        assert callable(getattr(diag, name, None)), f"missing public symbol: {name}"


def test_b_arm_is_idempotent(tmp_path: Path) -> None:
    """B. Duplicate arm-calls must reuse the existing log file + daemon.

    Lifespan handlers can re-run in some Starlette / testclient setups;
    the diagnostics must NOT spawn N daemon threads or rotate the log.
    """
    path1 = diag.arm_shutdown_diagnostics(tmp_path, interval_s=0.1)
    assert path1 is not None
    assert diag.is_armed() is True

    path2 = diag.arm_shutdown_diagnostics(tmp_path, interval_s=0.1)
    assert path2 == path1, "duplicate arm must reuse the same log file"


def test_c_snapshot_log_format(tmp_path: Path) -> None:
    """C. A baseline snapshot must appear in the log with the documented header.

    Each block starts with ``[ISO_TS] label=<x> pid=<n> thread_count=<n> ...``
    followed by per-thread rows. We assert on the baseline label so a
    log format regression (e.g. someone breaks the header) is caught
    immediately.
    """
    path = diag.arm_shutdown_diagnostics(tmp_path, interval_s=10.0)
    assert path is not None
    # Baseline dump happens synchronously inside arm_shutdown_diagnostics
    # before the daemon starts, so the file exists immediately.
    contents = path.read_text(encoding="utf-8")
    assert "label=baseline" in contents
    assert "pid=" in contents
    assert "thread_count=" in contents
    assert "non_daemon_alive=" in contents


def test_d_periodic_dump_fires(tmp_path: Path) -> None:
    """D. The daemon thread must produce at least one periodic snapshot.

    Uses a small interval (100ms) plus generous slack so the test is
    not flaky on slow CI runners. We assert at least one ``periodic``
    label appears.
    """
    path = diag.arm_shutdown_diagnostics(tmp_path, interval_s=0.1)
    assert path is not None
    # Give the daemon a fair window: 5 intervals + 200ms slack.
    time.sleep(0.7)
    contents = path.read_text(encoding="utf-8")
    assert "label=periodic" in contents, (
        "diagnostics daemon should have emitted at least one periodic "
        "snapshot within ~700ms"
    )


def test_e_atexit_hook_registered(tmp_path: Path) -> None:
    """E. ``atexit.register`` must be called for the final-snapshot hook.

    We patch ``atexit.register`` inside the module and assert it is
    called with the module's ``_atexit_dump`` callable. The final
    on-disk emission cannot be observed inside a single test process
    (atexit fires only at interpreter shutdown), so we pin the
    registration as a proxy.
    """
    with patch.object(diag.atexit, "register", wraps=atexit.register) as reg:
        diag.arm_shutdown_diagnostics(tmp_path, interval_s=10.0)
        reg.assert_called_once()
        args, _ = reg.call_args
        assert args[0] is diag._atexit_dump


def test_f_stop_cleanly_joins_daemon(tmp_path: Path) -> None:
    """F. ``stop_shutdown_diagnostics`` must join the daemon promptly.

    If the daemon hangs on its sleep, the stop helper can take up to
    ``interval_s`` extra time (the Event check happens inside the
    wait). We use a small interval here so the join is sub-second.
    """
    diag.arm_shutdown_diagnostics(tmp_path, interval_s=0.1)
    assert diag.is_armed() is True
    t0 = time.monotonic()
    diag.stop_shutdown_diagnostics(join_timeout_s=2.0)
    elapsed = time.monotonic() - t0
    assert diag.is_armed() is False
    # The Event interrupts the wait; stop should be much faster than
    # the join_timeout_s ceiling.
    assert elapsed < 1.0, f"stop took {elapsed:.2f}s (>1.0s ceiling)"
    # The named daemon must have exited (or at least be no longer
    # findable under its specific name).
    assert not any(
        t.name == "openakita-shutdown-diagnostics" and t.is_alive()
        for t in threading.enumerate()
    )


def test_g_arm_survives_unwritable_log_dir(tmp_path: Path) -> None:
    """Regression guard: arm-failure must NOT raise; it returns None.

    The lifespan shutdown handler wraps the call in try/except too,
    but the module's own promise is "never raise from the shutdown
    path". We simulate failure by passing a path that is a file (so
    ``mkdir`` rejects it).
    """
    bogus = tmp_path / "actually_a_file.log"
    bogus.write_text("not a directory", encoding="utf-8")
    result = diag.arm_shutdown_diagnostics(bogus, interval_s=10.0)
    assert result is None
    assert diag.is_armed() is False
