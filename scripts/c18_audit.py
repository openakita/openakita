"""C18 audit — verify Phase A..D + F UX/configuration completeness.

Phases
======

A. POLICIES.yaml hot-reload:
   - ``core/policy_v2/hot_reload.py`` exports ``PolicyHotReloader`` +
     module-level ``start_hot_reloader`` / ``stop_hot_reloader``.
   - ``schema.py`` adds ``HotReloadConfig`` with default ``enabled=False``,
     ``poll_interval_seconds`` and ``debounce_seconds`` bounded fields.
   - ``api/server.py`` wires startup/shutdown hooks for the reloader
     singleton.

B. 5s confirm aggregation:
   - ``schema.py`` adds ``confirmation.aggregation_window_seconds``
     bounded [0, 600], default 0 (disabled).
   - ``ui_confirm_bus.py`` adds ``list_batch_candidates`` +
     ``batch_resolve``.
   - ``api/routes/config.py`` adds ``POST /api/chat/security-confirm/batch``
     with server-side window clamp.
   - ``ChatView.tsx`` reads aggregation window and renders the batch
     banner only when ``securityQueueLen >= 1``.

C. POLICIES.yaml ENV variable overrides:
   - ``core/policy_v2/env_overrides.py`` defines ``OverrideSpec`` +
     ``OverrideReport`` + ``apply_env_overrides``.
   - ``loader.py`` applies overrides after schema validation +
     attaches the report to ``MigrationReport.env_overrides``.
   - ``global_engine.py._resolve_yaml_path`` honors
     ``OPENAKITA_POLICY_FILE``.
   - ``global_engine.py._audit_env_overrides`` writes audit rows.

D. ``--auto-confirm`` CLI flag:
   - ``main.py`` registers ``--auto-confirm`` on the top-level callback
     and translates it to ``OPENAKITA_AUTO_CONFIRM=1``.
   - Help text mentions destructive + safety_immune carveouts.

F. Tests + docs:
   - Every C18 test file present + green when invoked individually.
   - ``docs/configuration.md`` contains a POLICIES.yaml section with
     subsections for each phase.
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
# A. Hot-reload
# ---------------------------------------------------------------------------


def audit_phase_a() -> None:
    section("A. POLICIES.yaml hot-reload")

    hr = _read("src/openakita/core/policy_v2/hot_reload.py")
    for sym in (
        "class PolicyHotReloader",
        "def start_hot_reloader(",
        "def stop_hot_reloader(",
        "def get_hot_reloader(",
        "def _read_mtime(",
        "def _read_hash(",
        "def _do_reload(",
        "def _check_once(",
    ):
        must_contain(hr, sym, "policy_v2/hot_reload.py")

    must_contain(
        hr,
        "from .global_engine import _get_last_known_good, rebuild_engine_v2",
        "policy_v2/hot_reload.py: uses LKG + rebuild_engine_v2",
    )
    must_contain(
        hr,
        '"policy_hot_reload"',
        "policy_v2/hot_reload.py: writes audit row tagged policy_hot_reload",
    )

    schema = _read("src/openakita/core/policy_v2/schema.py")
    for sym in (
        "class HotReloadConfig",
        "poll_interval_seconds",
        "debounce_seconds",
        "hot_reload: HotReloadConfig",
    ):
        must_contain(schema, sym, "policy_v2/schema.py")

    server = _read("src/openakita/api/server.py")
    must_contain(server, "start_hot_reloader", "api/server.py: startup wires start_hot_reloader")
    must_contain(server, "stop_hot_reloader", "api/server.py: shutdown wires stop_hot_reloader")


# ---------------------------------------------------------------------------
# B. Confirm aggregation
# ---------------------------------------------------------------------------


def audit_phase_b() -> None:
    section("B. 5s confirm aggregation + batch resolve endpoint")

    schema = _read("src/openakita/core/policy_v2/schema.py")
    must_contain(
        schema,
        "aggregation_window_seconds",
        "policy_v2/schema.py: ConfirmationConfig has aggregation_window_seconds",
    )

    bus = _read("src/openakita/core/ui_confirm_bus.py")
    for sym in (
        "def list_batch_candidates(",
        "def batch_resolve(",
    ):
        must_contain(bus, sym, "core/ui_confirm_bus.py")

    cfg_routes = _read("src/openakita/api/routes/config.py")
    must_contain(
        cfg_routes,
        '/api/chat/security-confirm/batch',
        "api/routes/config.py: batch endpoint registered",
    )
    must_contain(
        cfg_routes,
        "SecurityConfirmBatchRequest",
        "api/routes/config.py: batch request schema",
    )
    must_contain(
        cfg_routes,
        "aggregation_window_seconds",
        "api/routes/config.py: GET /security/confirmation surfaces aggregation field",
    )
    # Server-side clamp keyword
    must_contain(
        cfg_routes,
        "cfg_window",
        "api/routes/config.py: server clamps client within_seconds to cfg",
    )

    chatview = _read("apps/setup-center/src/views/ChatView.tsx")
    must_contain(
        chatview,
        "securityQueueLen",
        "ChatView.tsx: queue length mirrored to React state",
    )
    must_contain(
        chatview,
        "securityAggWindow",
        "ChatView.tsx: aggregation window state loaded from server",
    )
    must_contain(
        chatview,
        "handleSecurityBatchResolve",
        "ChatView.tsx: batch resolve callback wired",
    )


# ---------------------------------------------------------------------------
# C. ENV overrides
# ---------------------------------------------------------------------------


def audit_phase_c() -> None:
    section("C. POLICIES.yaml ENV variable overrides")

    eo = _read("src/openakita/core/policy_v2/env_overrides.py")
    for sym in (
        "class OverrideSpec",
        "class OverrideReport",
        "def apply_env_overrides(",
        "def list_override_envs(",
        '"OPENAKITA_POLICY_HOT_RELOAD"',
        '"OPENAKITA_AUTO_CONFIRM"',
        '"OPENAKITA_UNATTENDED_STRATEGY"',
        '"OPENAKITA_AUDIT_LOG_PATH"',
    ):
        must_contain(eo, sym, "policy_v2/env_overrides.py")

    loader = _read("src/openakita/core/policy_v2/loader.py")
    must_contain(
        loader,
        "from .env_overrides import apply_env_overrides",
        "policy_v2/loader.py: imports apply_env_overrides",
    )
    must_contain(
        loader,
        "apply_env_overrides(cfg",
        "policy_v2/loader.py: calls apply_env_overrides on every load",
    )

    migration = _read("src/openakita/core/policy_v2/migration.py")
    must_contain(
        migration,
        "env_overrides: Any",
        "policy_v2/migration.py: MigrationReport gains env_overrides field",
    )

    ge = _read("src/openakita/core/policy_v2/global_engine.py")
    must_contain(
        ge,
        '"OPENAKITA_POLICY_FILE"',
        "policy_v2/global_engine.py: _resolve_yaml_path honors OPENAKITA_POLICY_FILE",
    )
    must_contain(
        ge,
        "def _audit_env_overrides(",
        "policy_v2/global_engine.py: writes env override audit rows",
    )
    must_contain(
        ge,
        '"policy_env_override"',
        "policy_v2/global_engine.py: audit policy tag policy_env_override",
    )


# ---------------------------------------------------------------------------
# D. --auto-confirm CLI flag
# ---------------------------------------------------------------------------


def audit_phase_d() -> None:
    section("D. --auto-confirm CLI flag")

    main = _read("src/openakita/main.py")
    must_contain(main, "def _apply_auto_confirm_flag(", "main.py: helper defined")
    must_contain(main, '"--auto-confirm"', "main.py: typer flag registered")
    must_contain(
        main,
        '"OPENAKITA_AUTO_CONFIRM"',
        "main.py: helper sets OPENAKITA_AUTO_CONFIRM env var",
    )
    # Help text must call out the destructive + safety_immune carveouts
    must_contain(
        main,
        "destructive",
        "main.py: flag help mentions destructive carveout",
    )
    must_contain(
        main,
        "safety_immune",
        "main.py: flag help mentions safety_immune carveout",
    )


# ---------------------------------------------------------------------------
# F. Tests + docs
# ---------------------------------------------------------------------------


def audit_phase_f_tests() -> None:
    section("F. Tests present + green")

    test_files = [
        "tests/unit/test_c18_hot_reload.py",
        "tests/unit/test_c18_confirm_batch.py",
        "tests/unit/test_c18_env_overrides.py",
        "tests/unit/test_c18_auto_confirm_cli.py",
        "tests/unit/test_c18_second_pass_audit.py",
    ]
    for tf in test_files:
        if not (REPO / tf).exists():
            fail(f"missing test file: {tf}")
        ok(f"present: {tf}")

    # Run them and assert exit 0
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *test_files,
        "-q",
        "-W",
        "ignore::DeprecationWarning",
        "--no-header",
    ]
    cp = subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if cp.returncode != 0:
        print(cp.stdout[-2000:])
        print(cp.stderr[-1000:])
        fail("C18 pytest suite failed")
    ok("C18 pytest suite: green")


def audit_second_pass_fixes() -> None:
    """C18 二轮自审：3 个 bug + 1 个 UX 改进必须仍然 in place。

    BUG-A1: ``hot_reload._do_reload`` must treat ``after_lkg is None``
            as a failure (invalid reload with no LKG fallback).
    BUG-C1: ``rebuild_engine_v2`` must reset the audit_logger singleton
            when the audit config changes.
    BUG-C2: ``_audit_env_overrides`` must accept ``cfg`` so it doesn't
            re-enter ``get_config_v2`` under ``_lock`` (deadlock).
    UX-B1:  ``ChatView.tsx`` batch resolve must check ``r.ok`` before
            clearing local queue.
    """
    section("2nd-pass audit: BUG-A1 / BUG-C1 / BUG-C2 / UX-B1 in place")

    hot_reload = _read("src/openakita/core/policy_v2/hot_reload.py")
    must_contain(
        hot_reload,
        "no last-known-good available",
        "hot_reload.py (BUG-A1: detect failure when LKG=None)",
    )

    global_engine_src = _read("src/openakita/core/policy_v2/global_engine.py")
    must_contain(
        global_engine_src,
        "reset_audit_logger",
        "global_engine.py (BUG-C1: invalidate audit singleton on rebuild)",
    )
    must_contain(
        global_engine_src,
        "_audit_env_overrides(report, cfg)",
        "global_engine.py (BUG-C2: pass cfg to avoid deadlock)",
    )
    # Phase C function signature must accept cfg.
    must_contain(
        global_engine_src,
        "def _audit_env_overrides(report, cfg",
        "global_engine.py (BUG-C2: signature change)",
    )

    chat = _read("apps/setup-center/src/views/ChatView.tsx")
    must_contain(
        chat,
        "if (!r.ok)",
        "ChatView.tsx (UX-B1: batch endpoint checks HTTP status)",
    )

    second_pass_test = _read("tests/unit/test_c18_second_pass_audit.py")
    for marker in (
        "TestBugA1HotReloadFailureWhenLkgNone",
        "TestBugC1AuditLoggerSingletonRefresh",
        "TestBugC2NoDeadlockOnEnvOverrideUnderLock",
    ):
        must_contain(second_pass_test, marker, "test_c18_second_pass_audit.py")


def audit_phase_f_docs() -> None:
    section("F. docs/configuration.md updated")

    doc = _read("docs/configuration.md")
    for marker in (
        "Hot-reload (C18 Phase A)",
        "Batch confirm aggregation (C18 Phase B)",
        "Environment variable overrides (C18 Phase C)",
        "`--auto-confirm` CLI flag (C18 Phase D)",
        "OPENAKITA_POLICY_HOT_RELOAD",
        "OPENAKITA_AUTO_CONFIRM",
        "OPENAKITA_UNATTENDED_STRATEGY",
        "OPENAKITA_AUDIT_LOG_PATH",
        "OPENAKITA_POLICY_FILE",
        "aggregation_window_seconds",
    ):
        must_contain(doc, marker, "docs/configuration.md")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    audit_phase_a()
    audit_phase_b()
    audit_phase_c()
    audit_phase_d()
    audit_phase_f_tests()
    audit_phase_f_docs()
    audit_second_pass_fixes()
    print("\nC18 audit: all checks passed")


if __name__ == "__main__":
    main()
