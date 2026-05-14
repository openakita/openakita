"""C20 audit — Audit JSONL rotation (Phase A + B).

Phases
======

A. Rotation engine + chain head carry-over:
   - ``core/policy_v2/schema.AuditConfig`` exports ``rotation_mode``,
     ``rotation_size_mb``, ``rotation_keep_count`` with bounded
     defaults (all "off" by default).
   - ``core/policy_v2/audit_chain.ChainedJsonlWriter`` has
     ``_get_rotation_config`` (lock-free), ``_needs_rotation``,
     ``_do_rotate``, ``_list_archives``, ``_prune_archives``.
   - ``ChainedJsonlWriter.append`` calls rotation check BEFORE writing,
     so the in-memory ``_last_hash`` carries the chain head across
     the rename boundary.
   - ``_get_rotation_config`` does NOT call ``get_config_v2`` —
     guarded by an AST-level test (BUG-C2-style deadlock immunity).

B. Verifier multi-file walk + auto-discovery:
   - ``verify_chain_with_rotation(active_path)`` walks rotated
     archives (mtime-asc) + active file as one continuous chain.
   - ``_list_rotation_archives(active_path)`` is the module-level
     archive discovery helper.
   - ``api/routes/config.py`` ``GET /api/config/security/audit`` calls
     ``verify_chain_with_rotation`` so SecurityView sees post-rotation
     chain status accurately.

F. Tests + docs:
   - ``tests/unit/test_c20_audit_rotation.py`` present + green
     (30 cases).
   - ``docs/configuration.md`` has a rotation subsection.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def ok(msg: str) -> None:
    print(f"  + {msg}")


def fail(msg: str) -> None:
    print(f"  - {msg}")
    sys.exit(1)


def _read(rel: str) -> str:
    p = REPO / rel
    if not p.exists():
        fail(f"missing file: {rel}")
    return p.read_text(encoding="utf-8")


def must_contain(haystack: str, needle: str, where: str) -> None:
    if needle not in haystack:
        fail(f"{where} missing marker: {needle!r}")
    ok(f"{where}: contains {needle!r}")


def audit_phase_a_schema() -> None:
    section("A. AuditConfig rotation fields (default off)")

    schema = _read("src/openakita/core/policy_v2/schema.py")
    for marker in (
        'rotation_mode: Literal["none", "daily", "size"] = "none"',
        "rotation_size_mb: int = Field(default=100",
        "rotation_keep_count: int = Field(default=30",
    ):
        must_contain(schema, marker, "schema.AuditConfig")


def audit_phase_a_writer() -> None:
    section("A. ChainedJsonlWriter rotation primitives")

    src = _read("src/openakita/core/policy_v2/audit_chain.py")
    for marker in (
        "def _get_rotation_config",
        "def _needs_rotation",
        "def _do_rotate",
        "def _list_archives",
        "def _prune_archives",
        # The lock-free read pattern: import the module + read _config
        # attribute (NOT get_config_v2 which would re-enter the lock).
        "from . import global_engine",
        "global_engine._config",
    ):
        must_contain(src, marker, "audit_chain.ChainedJsonlWriter")


def audit_phase_a_chain_carryover() -> None:
    section("A. Chain head carry-over wiring in append()")

    src = _read("src/openakita/core/policy_v2/audit_chain.py")
    # The critical ordering: refresh _last_hash FIRST, then rotate,
    # then write. Search for the ordering by anchor comments.
    for marker in (
        "Critical: re-read tail under the filelock",
        "self._reload_last_hash_from_disk()",
        "self._get_rotation_config()",
        "self._do_rotate(suffix, keep)",
    ):
        must_contain(src, marker, "audit_chain.append")


def audit_phase_a_deadlock_guard() -> None:
    section("A. BUG-C2-style deadlock guard")

    src = _read("src/openakita/core/policy_v2/audit_chain.py")
    # _get_rotation_config docstring must explain why we DON'T call
    # get_config_v2 (so future refactors don't reintroduce the
    # deadlock).
    must_contain(
        src,
        "deliberately bypass",
        "audit_chain._get_rotation_config docstring",
    )

    # Test file has the AST static guard.
    tests = _read("tests/unit/test_c20_audit_rotation.py")
    for marker in (
        "test_rotation_config_read_is_lock_free",
        "ast.parse(src)",
        "test_append_during_simulated_lock_hold_does_not_deadlock",
    ):
        must_contain(tests, marker, "test_c20_audit_rotation.py")


def audit_phase_b_verifier() -> None:
    section("B. verify_chain_with_rotation + _list_rotation_archives")

    src = _read("src/openakita/core/policy_v2/audit_chain.py")
    for marker in (
        "def _list_rotation_archives",
        "def verify_chain_with_rotation",
        # Walks archives in mtime order, then active.
        "Walks every ``<stem>.<suffix>.jsonl`` archive",
    ):
        must_contain(src, marker, "audit_chain (Phase B)")

    # API wired
    api_cfg = _read("src/openakita/api/routes/config.py")
    must_contain(
        api_cfg,
        "verify_chain_with_rotation",
        "api/routes/config.py (audit endpoint)",
    )


def audit_phase_b_tests() -> None:
    section("F. Tests present + green")

    tests = _read("tests/unit/test_c20_audit_rotation.py")
    for marker in (
        "class TestSchema",
        "class TestRotationDefaultOff",
        "class TestDailyRotation",
        "class TestSizeRotation",
        "class TestChainHeadCarryOver",
        "class TestPrune",
        "class TestIdempotency",
        "class TestDeadlockImmune",
        "class TestVerifyChainWithRotation",
    ):
        must_contain(tests, marker, "test_c20_audit_rotation.py")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/test_c20_audit_rotation.py",
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
        fail("C20 pytest suite failed")
    ok("C20 pytest suite: green")


def audit_phase_b_docs() -> None:
    section("F. docs/configuration.md updated")

    doc = _read("docs/configuration.md")
    for marker in (
        "Audit JSONL rotation (C20)",
        "rotation_mode",
        "rotation_size_mb",
        "rotation_keep_count",
    ):
        must_contain(doc, marker, "docs/configuration.md")


def main() -> None:
    audit_phase_a_schema()
    audit_phase_a_writer()
    audit_phase_a_chain_carryover()
    audit_phase_a_deadlock_guard()
    audit_phase_b_verifier()
    audit_phase_b_tests()
    audit_phase_b_docs()
    print("\nC20 audit: all checks passed")


if __name__ == "__main__":
    main()
