"""C12 + C9c audit script (Policy V2 §14 + §8.4).

Runs the same kind of multi-dimensional self-audit as scripts/c19_audit.py
but covers the C12 + C9c bundle:

D1. PolicyContext exposes ``is_unattended`` + ``unattended_strategy`` fields
    AND ``Session`` has them as first-class fields with backward-compat
    fallback to ``metadata`` (R3-2)
D2. PendingApprovalsStore module exists with the documented API surface
    (create / list / resolve / event_hook). user_message field present.
D3. tool_executor wires DEFER → _defer_unattended_confirm → returns
    _deferred_approval_id marker (R3-1 + R3-3)
D4. agent.py raises DeferredApprovalRequired on the marker (Ralph loop bubble)
D5. scheduler/task.py defines TaskStatus.AWAITING_APPROVAL + transition rules
    + mark_awaiting_approval() helper (Phase D)
D6. scheduler/executor.py installs PolicyContext via ContextVar with
    is_unattended=True + lifts replay_authorizations from task.metadata
D7. /api/pending_approvals routes + /resolve POST endpoint + scope=security
    on reset_policy_v2_layer SSE wiring
D8. C9c-1 tool_intent_preview emission (sanitization + redaction)
D9. C9c-2 pending_approval_{created,resolved} hook wired at startup
D10. C9c-3 policy_config_reloaded[_failed] emission with scope arg
D11. End-to-end pytest of the new test files

Each dimension is independent. A failure prints WHY and the script exits
non-zero so CI can gate on it.

Note: This audit reads source files; no live FastAPI server needed.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "openakita"


def _ok(label: str, msg: str = "") -> None:
    suffix = f" — {msg}" if msg else ""
    print(f"  [OK] {label}{suffix}")


def _fail(label: str, msg: str) -> None:
    print(f"  [FAIL] {label} — {msg}")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"<<read-error: {exc}>>"


# -----------------------------------------------------------------------------
# D1: Session + PolicyContext fields
# -----------------------------------------------------------------------------


def d1_session_and_context_fields() -> bool:
    print("\n[D1] Session + PolicyContext: is_unattended / unattended_strategy")
    sess_src = _read(SRC / "sessions" / "session.py")
    ctx_src = _read(SRC / "core" / "policy_v2" / "context.py")
    ok = True
    if "is_unattended: bool" not in sess_src:
        _fail("Session.is_unattended", "field not declared in sessions/session.py")
        ok = False
    else:
        _ok("Session.is_unattended declared")
    if "unattended_strategy: str" not in sess_src:
        _fail("Session.unattended_strategy", "field not declared")
        ok = False
    else:
        _ok("Session.unattended_strategy declared")
    if "is_unattended" not in ctx_src or "unattended_strategy" not in ctx_src:
        _fail("PolicyContext", "is_unattended / unattended_strategy missing")
        ok = False
    else:
        _ok("PolicyContext exposes both fields")
    if "from_session" not in ctx_src or "metadata" not in ctx_src:
        _fail("PolicyContext.from_session", "back-compat metadata fallback missing")
        ok = False
    else:
        _ok("from_session fallback to session.metadata")
    return ok


# -----------------------------------------------------------------------------
# D2: PendingApprovalsStore API surface
# -----------------------------------------------------------------------------


def d2_pending_approvals_store_api() -> bool:
    print("\n[D2] PendingApprovalsStore: API surface + user_message field")
    src = _read(SRC / "core" / "pending_approvals.py")
    ok = True
    required = [
        "class PendingApproval",
        "class PendingApprovalsStore",
        "def create(",
        "def list_active(",
        "def list_all(",
        "def resolve(",
        "def get(",
        "def stats(",
        "def set_event_hook(",
        "user_message: str =",
        "user_message: str = \"\"",
    ]
    for needle in required:
        if needle not in src:
            _fail(f"missing `{needle}`", "in pending_approvals.py")
            ok = False
    if ok:
        _ok("all required PendingApprovalsStore symbols present (incl. user_message)")
    return ok


# -----------------------------------------------------------------------------
# D3: tool_executor wires DEFER → marker
# -----------------------------------------------------------------------------


def d3_tool_executor_defer_marker() -> bool:
    print("\n[D3] tool_executor.execute_batch: DEFER -> pending_approval marker")
    src = _read(SRC / "core" / "tool_executor.py")
    ok = True
    needles = [
        "is_unattended_path",
        "_defer_unattended_confirm",
        "_deferred_approval_id",
        "_deferred_approval_strategy",
        "user_message=captured_msg",
    ]
    for n in needles:
        if n not in src:
            _fail(f"missing `{n}`", "in tool_executor.py")
            ok = False
    if ok:
        _ok("DEFER -> _defer_unattended_confirm -> marker fully wired")
    return ok


# -----------------------------------------------------------------------------
# D4: agent.py bubbles DeferredApprovalRequired
# -----------------------------------------------------------------------------


def d4_agent_bubbles_deferred() -> bool:
    print("\n[D4] agent.py: bubbles _deferred_approval_id as DeferredApprovalRequired")
    src = _read(SRC / "core" / "agent.py")
    ok = True
    if "_deferred_approval_id" not in src:
        _fail("_deferred_approval_id check", "missing in agent.py")
        ok = False
    if "DeferredApprovalRequired" not in src:
        _fail("DeferredApprovalRequired", "exception not raised in agent.py")
        ok = False
    if ok:
        _ok("agent loops detect marker + raise DeferredApprovalRequired")
    return ok


# -----------------------------------------------------------------------------
# D5: scheduler/task.py AWAITING_APPROVAL + helper
# -----------------------------------------------------------------------------


def d5_scheduler_task_awaiting() -> bool:
    print("\n[D5] scheduler/task.py: AWAITING_APPROVAL + mark_awaiting_approval")
    src = _read(SRC / "scheduler" / "task.py")
    ok = True
    if "AWAITING_APPROVAL" not in src:
        _fail("TaskStatus.AWAITING_APPROVAL", "enum value missing")
        ok = False
    if "def mark_awaiting_approval" not in src:
        _fail("mark_awaiting_approval", "helper method missing")
        ok = False
    if "_VALID_TRANSITIONS" not in src or "awaiting_approval" not in src.lower():
        _fail("transition table", "AWAITING_APPROVAL not in _VALID_TRANSITIONS")
        ok = False
    if ok:
        _ok("AWAITING_APPROVAL state + helper + transitions all present")
    return ok


# -----------------------------------------------------------------------------
# D6: scheduler/executor.py PolicyContext + replay lift
# -----------------------------------------------------------------------------


def d6_scheduler_executor_context() -> bool:
    print("\n[D6] scheduler/executor.py: PolicyContext install + replay lift")
    src = _read(SRC / "scheduler" / "executor.py")
    ok = True
    needles = [
        "set_current_context",
        "is_unattended=True",
        "unattended_strategy=",
        "replay_authorizations",
        "DeferredApprovalRequired",
        "[awaiting_approval]",
    ]
    for n in needles:
        if n not in src:
            _fail(f"missing `{n}`", "in scheduler/executor.py")
            ok = False
    if ok:
        _ok("ContextVar wire + replay_auth lift + AWAITING marker handling")
    return ok


# -----------------------------------------------------------------------------
# D7: /api/pending_approvals routes + resolve
# -----------------------------------------------------------------------------


def d7_pending_approvals_routes() -> bool:
    print("\n[D7] /api/pending_approvals routes + /resolve POST")
    routes_path = SRC / "api" / "routes" / "pending_approvals.py"
    if not routes_path.exists():
        _fail("file missing", str(routes_path))
        return False
    src = _read(routes_path)
    server_src = _read(SRC / "api" / "server.py")
    ok = True
    needles = [
        '"/api/pending_approvals"',
        '"/api/pending_approvals/stats"',
        '"/api/pending_approvals/{pending_id}"',
        '"/api/pending_approvals/{pending_id}/resolve"',
        "REPLAY_TTL_SECONDS",
        "_resume_task",
        "_fail_task",
    ]
    for n in needles:
        if n not in src:
            _fail(f"missing route/symbol `{n}`", "in routes/pending_approvals.py")
            ok = False
    if "pending_approvals.router" not in server_src:
        _fail("router not registered", "api/server.py missing include_router")
        ok = False
    if ok:
        _ok("4 routes + resume/fail helpers + server.include_router wired")
    return ok


# -----------------------------------------------------------------------------
# D8: C9c-1 tool_intent_preview
# -----------------------------------------------------------------------------


def d8_tool_intent_preview() -> bool:
    print("\n[D8] C9c-1: tool_intent_preview SSE emission")
    src = _read(SRC / "core" / "tool_executor.py")
    ok = True
    needles = [
        "_emit_tool_intent_previews",
        "_sanitize_preview_params",
        "_PREVIEW_REDACT_KEYS",
        '"tool_intent_preview"',
        "***REDACTED***",
    ]
    for n in needles:
        if n not in src:
            _fail(f"missing `{n}`", "in tool_executor.py")
            ok = False
    if ok:
        _ok("tool_intent_preview emitter + sanitizer all present")
    return ok


# -----------------------------------------------------------------------------
# D9: C9c-2 pending_approval_{created,resolved} hook
# -----------------------------------------------------------------------------


def d9_pending_approval_sse_hook() -> bool:
    print("\n[D9] C9c-2: pending_approval_{created,resolved} hook at startup")
    src = _read(SRC / "core" / "pending_approvals.py")
    server = _read(SRC / "api" / "server.py")
    ok = True
    if '"pending_approval_created"' not in src:
        _fail("emit pending_approval_created", "missing in store")
        ok = False
    if '"pending_approval_resolved"' not in src:
        _fail("emit pending_approval_resolved", "missing in store")
        ok = False
    if "_wire_pending_approvals_sse" not in server:
        _fail("startup hook", "_wire_pending_approvals_sse missing in server.py")
        ok = False
    if "set_event_hook" not in server:
        _fail("hook installation", "set_event_hook not called in server startup")
        ok = False
    if ok:
        _ok("SSE hook wired in store + startup installer present")
    return ok


# -----------------------------------------------------------------------------
# D10: C9c-3 policy_config_reloaded[_failed]
# -----------------------------------------------------------------------------


def d10_policy_config_reloaded() -> bool:
    print("\n[D10] C9c-3: policy_config_reloaded[_failed] SSE")
    ge = _read(SRC / "core" / "policy_v2" / "global_engine.py")
    cfg = _read(SRC / "api" / "routes" / "config.py")
    ok = True
    if "_emit_reload_event" not in ge:
        _fail("_emit_reload_event helper", "missing in global_engine.py")
        ok = False
    if '"policy_config_reloaded"' not in ge or '"policy_config_reload_failed"' not in ge:
        _fail("event names", "missing emit strings in global_engine.py")
        ok = False
    if "scope: str" not in ge:
        _fail("scope arg", "reset_policy_v2_layer signature missing scope arg")
        ok = False
    # config.py callsites pass scope
    expected_scopes = {"security", "zones", "commands", "sandbox",
                       "permission_mode", "confirmation", "self_protection"}
    missing = {s for s in expected_scopes if f'scope="{s}"' not in cfg}
    if missing:
        _fail("scope passthrough", f"config.py missing scopes: {sorted(missing)}")
        ok = False
    else:
        _ok("all 7 config.py callsites pass scope=...")
    if ok:
        _ok("policy_config_reload[ed|_failed] fully wired")
    return ok


# -----------------------------------------------------------------------------
# D11: pytest gate
# -----------------------------------------------------------------------------


def d11_pytest() -> bool:
    print("\n[D11] pytest: new test files (PendingApprovalsStore + C9c SSE)")
    test_files = [
        "tests/unit/test_pending_approvals_store.py",
        "tests/unit/test_policy_v2_c9c_sse.py",
    ]
    cmd = [sys.executable, "-m", "pytest", *test_files, "-q", "--no-header"]
    # On Windows, default subprocess decoding uses cp1252/gbk and pytest
    # output (esp. tracebacks) sometimes contains UTF-8 bytes that crash
    # the reader thread. Force binary capture + explicit decode.
    res = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, timeout=180
    )
    stdout = (res.stdout or b"").decode("utf-8", errors="replace")
    stderr = (res.stderr or b"").decode("utf-8", errors="replace")
    if res.returncode != 0:
        _fail("pytest", f"failed (exit {res.returncode})\n{stdout}\n{stderr}")
        return False
    passed_lines = [l for l in stdout.splitlines() if "passed" in l]
    summary = passed_lines[-1] if passed_lines else "(no summary)"
    _ok("pytest", summary.strip())
    return True


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> int:
    print("=" * 72)
    print("C12 + C9c audit (Policy V2 §14 + §8.4)")
    print("=" * 72)
    results = [
        ("D1", d1_session_and_context_fields()),
        ("D2", d2_pending_approvals_store_api()),
        ("D3", d3_tool_executor_defer_marker()),
        ("D4", d4_agent_bubbles_deferred()),
        ("D5", d5_scheduler_task_awaiting()),
        ("D6", d6_scheduler_executor_context()),
        ("D7", d7_pending_approvals_routes()),
        ("D8", d8_tool_intent_preview()),
        ("D9", d9_pending_approval_sse_hook()),
        ("D10", d10_policy_config_reloaded()),
        ("D11", d11_pytest()),
    ]
    print("\n" + "=" * 72)
    print("Summary")
    print("=" * 72)
    failed = [name for name, ok in results if not ok]
    for name, ok in results:
        marker = "OK  " if ok else "FAIL"
        print(f"  [{marker}] {name}")
    if failed:
        print(f"\n{len(failed)}/{len(results)} dimension(s) failed: {failed}")
        return 1
    print(f"\nAll {len(results)} dimensions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
