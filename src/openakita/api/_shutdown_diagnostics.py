"""Sprint 15 / v32 Phase B Task C: lifespan→process-exit hang RCA.

Background — even after the v31 IM gateway concurrency fix (gateway.stop
14s → 5.7s) and the v32 threading.Timer force-exit watchdog (Task A),
v31 forensics showed every PHASEA shutdown still wedged for ~13s
between "last lifespan log line" and "process actually exits":

* ``_v31_biz_e2e/_phase_a_post_fix_forensics.md`` §5: graceful path
  drains in ~6s; the next 13s+ is dead air with no log lines, until
  the external ``psutil.kill`` (or now the v32 Timer) pulls the plug.

Without runtime evidence we cannot tell which non-daemon thread /
atexit hook / uvicorn keep-alive socket / asyncio loop close phase is
responsible. This module is the evidence collector:

* ``arm_shutdown_diagnostics()`` starts a daemon Thread that snapshots
  ``threading.enumerate()`` every ``interval_s`` (default 1.0s) into a
  per-process log file.
* It also registers an ``atexit`` hook that emits one final snapshot
  at interpreter shutdown — the moment Python's interpreter is about
  to reap remaining non-daemon threads.

Wire-up: ``server.py`` calls ``arm_shutdown_diagnostics(data/logs)``
from the last lifespan ``@app.on_event("shutdown")`` handler so the
overhead is paid only during the shutdown window (not the whole
process lifetime).

Read the resulting log alongside the v32 e2e regression runs:

    data/logs/shutdown_diagnostics_<pid>_<YYYYMMDD_HHMMSS>.log

Each snapshot block lists every Thread (name, daemon, alive, native_id).
Threads that persist past ``label=atexit`` are the smoking gun.
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level singletons; the public arm helper is idempotent so a
# duplicate call (lifespan replay, multi-app testing) is a no-op.
_armed: bool = False
_stop_event: threading.Event = threading.Event()
_dump_thread: threading.Thread | None = None
_log_path: Path | None = None
_lock: threading.Lock = threading.Lock()


def _format_thread(t: threading.Thread) -> dict[str, Any]:
    """Snapshot only the cheap, JSON-safe attributes.

    We deliberately do NOT call ``sys._current_frames()`` here: it can
    deadlock the GIL when called from a daemon thread in the middle of
    Python's interpreter teardown phase, which is exactly the window we
    most need stable output from.
    """
    return {
        "name": t.name,
        "daemon": t.daemon,
        "alive": t.is_alive(),
        "ident": t.ident,
        "native_id": getattr(t, "native_id", None),
    }


def _dump_snapshot(label: str) -> None:
    """Append one snapshot block to the configured log path.

    Best-effort: any I/O / threading error is swallowed; this module
    must NEVER raise out of the shutdown path.
    """
    if _log_path is None:
        return
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        threads = [_format_thread(t) for t in threading.enumerate()]
        non_daemon_alive = sum(1 for t in threads if not t["daemon"] and t["alive"])
        header = (
            f"[{ts}] label={label} pid={os.getpid()} "
            f"thread_count={len(threads)} "
            f"non_daemon_alive={non_daemon_alive}\n"
        )
        # Open per-snapshot so a mid-shutdown crash still flushes
        # everything we wrote up to that point.
        with _log_path.open("a", encoding="utf-8") as f:
            f.write(header)
            for t in threads:
                f.write(f"  - {t}\n")
            f.write("\n")
    except Exception:  # noqa: BLE001 -- diagnostics must not break shutdown
        pass


def _dump_loop(interval_s: float) -> None:
    """Daemon-thread body: periodic snapshot until ``_stop_event`` is set.

    ``Event.wait`` is interruptible, so an explicit
    ``stop_shutdown_diagnostics()`` (used by tests) returns within
    ``interval_s`` instead of the full sleep.
    """
    while not _stop_event.is_set():
        _dump_snapshot(label="periodic")
        if _stop_event.wait(interval_s):
            break
    _dump_snapshot(label="stopped")


def _atexit_dump() -> None:
    """Final snapshot at interpreter shutdown.

    By the time atexit hooks fire, Python has begun reaping non-daemon
    threads. Anything still alive here is either a true blocker or a
    very-late finishing thread; correlate with the prior ``periodic``
    snapshots' ``alive=True`` rows to triangulate.
    """
    _dump_snapshot(label="atexit")


def arm_shutdown_diagnostics(
    log_dir: Path | str,
    *,
    interval_s: float = 1.0,
) -> Path | None:
    """Start the diagnostics daemon + register the atexit hook.

    Idempotent: returns the existing log path on the second call.

    Returns ``None`` if arming failed (e.g. ``log_dir`` not writable).
    """
    global _armed, _dump_thread, _log_path
    with _lock:
        if _armed:
            return _log_path
        try:
            log_dir_p = Path(log_dir)
            log_dir_p.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            _log_path = log_dir_p / f"shutdown_diagnostics_{os.getpid()}_{ts}.log"
            # Reset stop event in case a previous test cycle disarmed it.
            _stop_event.clear()
            _dump_snapshot(label="baseline")
            _dump_thread = threading.Thread(
                target=_dump_loop,
                args=(interval_s,),
                name="openakita-shutdown-diagnostics",
                daemon=True,
            )
            _dump_thread.start()
            atexit.register(_atexit_dump)
            _armed = True
            logger.info(
                "[ShutdownDiagnostics] Armed; snapshots → %s (interval=%.1fs)",
                _log_path,
                interval_s,
            )
            return _log_path
        except Exception as exc:  # noqa: BLE001 -- never block shutdown
            logger.warning("[ShutdownDiagnostics] arm failed: %s", exc)
            return None


def stop_shutdown_diagnostics(*, join_timeout_s: float = 2.0) -> None:
    """Signal the daemon to stop and join it.

    Public so tests can clean up between cases. Not called from the
    production shutdown path: the daemon is meant to outlive the
    asyncio loop and only die when the interpreter does.
    """
    global _armed, _dump_thread
    with _lock:
        _stop_event.set()
        if _dump_thread is not None:
            _dump_thread.join(timeout=join_timeout_s)
        _dump_thread = None
        _armed = False


def is_armed() -> bool:
    """Test hook: True iff the diagnostics daemon is currently armed."""
    return _armed


def get_log_path() -> Path | None:
    """Test hook: the active log path, or ``None`` if not armed."""
    return _log_path
