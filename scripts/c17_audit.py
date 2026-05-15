"""C17 audit — verify Phase A..E reliability hardening.

Phases
======

A. R5-09 — Scheduler crash recovery + per-task execution lock:
   - ``scheduler/locks.py`` exports ``ExecLock`` / ``acquire_exec_lock`` /
     ``heartbeat_exec_lock`` / ``scan_orphaned_locks`` +
     ``set_current_scheduled_task_id`` ContextVar bridge.
   - ``scheduler/scheduler.py`` acquires the lock, runs a heartbeat task,
     advances next_run **before** execution, persists immediately after
     mark_running, and calls ``_rescan_orphaned_runs`` +
     ``_reconcile_awaiting_approval`` + ``_stagger_missed_tasks`` on startup.
   - ``core/tool_executor.py`` falls back to ``get_current_scheduled_task_id``
     when ``state.task_id`` is missing — so pending_approval rows from
     scheduled tasks carry the right ``task_id``.

B. R4-18 + R4-19 — SSE Last-Event-ID resume + multi-client confirm dedup:
   - ``core/sse_replay.py`` exports ``SSESession`` / ``SSESessionRegistry`` /
     ``parse_last_event_id`` / ``format_sse_frame`` + GC + LRU eviction.
   - ``api/routes/chat.py`` reads ``Last-Event-ID`` header, calls
     ``replay_from``, and emits ``id: <seq>`` lines per frame.
   - ``core/ui_confirm_bus.py`` adds ``set_broadcast_hook`` +
     ``active_confirms_for_session`` + fires ``confirm_initiated`` /
     ``confirm_revoked`` events on store_pending / resolve.
   - ``api/server.py`` wires the bus broadcast hook to ``fire_event``.
   - ``api/routes/sessions.py`` exposes
     ``GET /api/sessions/{conv}/active_confirms`` for second-client
     readonly rendering.
   - ``ChatView.tsx`` sends Last-Event-ID + dedups based on parsed
     ``id:`` lines.

C. R-DEV — Health probes:
   - ``api/routes/health.py`` adds ``/api/healthz`` (always 200) and
     ``/api/readyz`` (200 healthy / 503 degraded) with policy_v2 /
     audit_chain / event_loop / scheduler / gateway checks, 5s cache,
     and remote-caller sanitization.

D. R5-12 — OrgEventStore hardening:
   - ``orgs/event_store.py`` gains ``threading.Lock`` + ``filelock`` +
     non-cryptographic-audit docstring + warning logs on contention.

E. R5-17 followup — ChainedJsonlWriter cross-process + ParamMutationAuditor
   chain integration:
   - ``audit_chain.py`` adds ``filelock.FileLock`` per writer and
     ``_reload_last_hash_from_disk`` (cross-process safe append).
   - ``param_mutation_audit.py`` adds ``_sanitize_for_chain`` and uses
     ``audit_chain.get_writer`` instead of plain ``open(..., "a")``.

F. Regression: prior milestones (C14, C15, C16 audit scripts) still pass.
G. Tests: every new C17 test file present + green.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "openakita"


def _read(rel_path: str) -> str:
    p = REPO / rel_path
    if not p.exists():
        raise FileNotFoundError(rel_path)
    return p.read_text(encoding="utf-8")


def ok(msg: str) -> None:
    print(f"  + {msg}")


def fail(msg: str) -> None:
    print(f"  x {msg}")
    raise SystemExit(1)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def must_contain(text: str, pattern: str, where: str, *, regex: bool = False) -> None:
    found = bool(re.search(pattern, text)) if regex else (pattern in text)
    if not found:
        fail(f"{where}: expected {'pattern' if regex else 'literal'} {pattern!r}")
    ok(f"{where}: contains {pattern!r}")


# ---------------------------------------------------------------------------
# A. Scheduler crash recovery + exec lock
# ---------------------------------------------------------------------------


def audit_phase_a() -> None:
    section("A. Scheduler crash recovery + per-task execution lock")

    locks = _read("src/openakita/scheduler/locks.py")
    for sym in (
        "class ExecLock",
        "class OrphanLock",
        "def acquire_exec_lock(",
        "def release_exec_lock(",
        "def heartbeat_exec_lock(",
        "def scan_orphaned_locks(",
        "def is_stale(",
        "def set_current_scheduled_task_id(",
        "def reset_current_scheduled_task_id(",
        "def get_current_scheduled_task_id(",
    ):
        must_contain(locks, sym, "scheduler/locks.py")
    must_contain(locks, "O_EXCL", "scheduler/locks.py: uses O_EXCL")
    must_contain(
        locks,
        "ContextVar",
        "scheduler/locks.py: ContextVar bridge for task_id",
    )

    scheduler = _read("src/openakita/scheduler/scheduler.py")
    must_contain(scheduler, "from .locks import", "scheduler/scheduler.py imports lock module")
    must_contain(
        scheduler,
        "acquire_exec_lock(",
        "scheduler/scheduler.py: acquires exec lock per run",
    )
    must_contain(
        scheduler,
        "_rescan_orphaned_runs",
        "scheduler/scheduler.py: defines rescan",
    )
    must_contain(
        scheduler,
        "_reconcile_awaiting_approval",
        "scheduler/scheduler.py: reconciles awaiting_approval on start",
    )
    must_contain(
        scheduler,
        "_stagger_missed_tasks",
        "scheduler/scheduler.py: staggers missed tasks on start",
    )
    must_contain(
        scheduler,
        "set_current_scheduled_task_id",
        "scheduler/scheduler.py: propagates task_id via ContextVar",
    )

    tool_exec = _read("src/openakita/core/tool_executor.py")
    must_contain(
        tool_exec,
        "get_current_scheduled_task_id",
        "core/tool_executor.py: falls back to ContextVar task_id",
    )


# ---------------------------------------------------------------------------
# B. SSE replay + multi-client confirm dedup
# ---------------------------------------------------------------------------


def audit_phase_b() -> None:
    section("B. SSE Last-Event-ID resume + UIConfirmBus broadcast")

    sse = _read("src/openakita/core/sse_replay.py")
    for sym in (
        "class SSEEvent",
        "class SSESession",
        "class SSESessionRegistry",
        "def parse_last_event_id(",
        "def format_sse_frame(",
        "def get_registry(",
        "def reset_registry_for_testing(",
        "MAX_SESSIONS",
        "DEFAULT_MAXLEN",
        "DEFAULT_TTL_SECONDS",
    ):
        must_contain(sse, sym, "core/sse_replay.py")

    chat = _read("src/openakita/api/routes/chat.py")
    must_contain(
        chat,
        "from ...core.sse_replay import",
        "api/routes/chat.py: imports sse_replay",
    )
    must_contain(
        chat,
        "format_sse_frame",
        "api/routes/chat.py: uses format_sse_frame",
    )
    must_contain(
        chat,
        "Last-Event-ID",
        "api/routes/chat.py: reads Last-Event-ID header",
    )
    must_contain(
        chat,
        "replay_from(_last_event_id)",
        "api/routes/chat.py: replays missed events on reconnect",
    )

    bus = _read("src/openakita/core/ui_confirm_bus.py")
    must_contain(bus, "def set_broadcast_hook(", "core/ui_confirm_bus.py")
    must_contain(bus, '"confirm_initiated"', "core/ui_confirm_bus.py")
    must_contain(bus, '"confirm_revoked"', "core/ui_confirm_bus.py")
    must_contain(
        bus,
        "def active_confirms_for_session(",
        "core/ui_confirm_bus.py: per-session active accessor",
    )

    server = _read("src/openakita/api/server.py")
    must_contain(
        server,
        "UIConfirmBus broadcast hook wired",
        "api/server.py: wires bus broadcast hook on startup",
    )

    sessions = _read("src/openakita/api/routes/sessions.py")
    must_contain(
        sessions,
        "/api/sessions/{conversation_id}/active_confirms",
        "api/routes/sessions.py: registers active_confirms endpoint",
    )

    chatview = _read("apps/setup-center/src/views/ChatView.tsx")
    must_contain(
        chatview,
        "lastSeqByConv",
        "ChatView.tsx: tracks last seq per conversation",
    )
    must_contain(
        chatview,
        "Last-Event-ID",
        "ChatView.tsx: sends Last-Event-ID on reconnect",
    )
    must_contain(
        chatview,
        'line.startsWith("id: ")',
        "ChatView.tsx: parses id: lines from SSE",
    )


# ---------------------------------------------------------------------------
# C. /healthz + /readyz
# ---------------------------------------------------------------------------


def audit_phase_c() -> None:
    section("C. /api/healthz liveness + /api/readyz readiness probes")
    health = _read("src/openakita/api/routes/health.py")
    must_contain(health, '"/api/healthz"', "health.py: registers /api/healthz")
    must_contain(health, '"/api/readyz"', "health.py: registers /api/readyz")
    must_contain(health, "_READYZ_CACHE_TTL_SECONDS", "health.py: 5s cache constant")
    for check in (
        "_check_policy_engine",
        "_check_audit_chain",
        "_check_event_loop_lag",
        "_check_scheduler",
        "_check_gateway",
    ):
        must_contain(health, f"def {check}(", f"health.py: defines {check}")
    must_contain(
        health,
        "_is_localhost",
        "health.py: sanitizes details for non-localhost callers",
    )


# ---------------------------------------------------------------------------
# D. OrgEventStore hardening
# ---------------------------------------------------------------------------


def audit_phase_d() -> None:
    section("D. OrgEventStore — threading + filelock + non-crypto docs")
    es = _read("src/openakita/orgs/event_store.py")
    must_contain(es, "threading.Lock()", "orgs/event_store.py: in-process lock")
    must_contain(es, "FileLock", "orgs/event_store.py: cross-process filelock")
    must_contain(
        es,
        "非密码学",
        "orgs/event_store.py: documents non-cryptographic audit nature",
    )
    must_contain(
        es,
        "_FILELOCK_TIMEOUT_SECONDS",
        "orgs/event_store.py: bounded filelock timeout",
    )


# ---------------------------------------------------------------------------
# E. ChainedJsonlWriter cross-process + ParamMutationAuditor chain
# ---------------------------------------------------------------------------


def audit_phase_e() -> None:
    section("E. ChainedJsonlWriter cross-process + ParamMutationAuditor chain")
    chain = _read("src/openakita/core/policy_v2/audit_chain.py")
    must_contain(chain, "FileLock", "audit_chain.py: imports FileLock")
    must_contain(
        chain,
        "_reload_last_hash_from_disk",
        "audit_chain.py: defines _reload_last_hash_from_disk",
    )
    must_contain(
        chain,
        "self._filelock_path",
        "audit_chain.py: per-writer sidecar lock file",
    )
    must_contain(
        chain,
        "_FILELOCK_TIMEOUT_SECONDS",
        "audit_chain.py: bounded filelock timeout",
    )

    pma = _read("src/openakita/core/policy_v2/param_mutation_audit.py")
    must_contain(
        pma,
        "def _sanitize_for_chain(",
        "param_mutation_audit.py: defines _sanitize_for_chain",
    )
    must_contain(
        pma,
        "from .audit_chain import get_writer",
        "param_mutation_audit.py: writes through ChainedJsonlWriter",
    )
    must_contain(
        pma,
        "_SANITIZE_MAX_DEPTH",
        "param_mutation_audit.py: depth cap exposed",
    )


# ---------------------------------------------------------------------------
# F. Regression — prior audits still pass
# ---------------------------------------------------------------------------


def audit_phase_f_regression() -> None:
    section("F. Regression — C14 + C15 + C16 audits still pass")
    for script in ("c14_audit.py", "c15_audit.py", "c16_audit.py"):
        cp = subprocess.run(
            [sys.executable, str(REPO / "scripts" / script)],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = (cp.stdout or "") + (cp.stderr or "")
        if cp.returncode != 0:
            print(out)
            fail(f"{script} exited non-zero — C17 regressed prior milestone")
        ok(f"{script} passes")


# ---------------------------------------------------------------------------
# G. Tests — C17 test files present + green
# ---------------------------------------------------------------------------


C17_TEST_FILES = [
    "tests/unit/test_c17_scheduler_lock_recovery.py",
    "tests/unit/test_c17_sse_replay.py",
    "tests/unit/test_c17_healthz_readyz.py",
    "tests/unit/test_c17_audit_chain_hardening.py",
]


def audit_phase_g_tests() -> None:
    section("G. Tests — four C17 test files present + green")
    for rel in C17_TEST_FILES:
        if not (REPO / rel).exists():
            fail(f"{rel} missing")
        ok(f"{rel} exists")

    cp = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *C17_TEST_FILES,
            "-q",
            "--no-header",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (cp.stdout or "") + (cp.stderr or "")
    if cp.returncode != 0:
        print(out)
        fail("C17 tests failed")
    summary = [line for line in out.splitlines() if "passed" in line and "in " in line]
    if summary:
        ok(f"test summary: {summary[-1].strip()}")
    else:
        ok("tests passed (no summary line parsed)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    audit_phase_a()
    audit_phase_b()
    audit_phase_c()
    audit_phase_d()
    audit_phase_e()
    audit_phase_f_regression()
    audit_phase_g_tests()
    print(
        "\n+ C17 audit passed: A (scheduler) / B (SSE+confirm) / "
        "C (healthz+readyz) / D (event_store) / E (chain+sanitize) / "
        "F (C14+C15+C16 regression) / G (tests)."
    )


if __name__ == "__main__":
    main()
