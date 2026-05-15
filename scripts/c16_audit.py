"""C16 audit — verify Phase A (prompt injection) / B (yaml strict) / C (audit chain).

Phases
======

A. R4-14 — Prompt injection hardening:
   - ``core/policy_v2/prompt_hardening.py`` exists & exports wrap + rules.
   - ``prompt/builder.py`` injects ``TOOL_RESULT_HARDENING_RULES`` into
     ``_SAFETY_SECTION``.
   - ``tools/handlers/agent.py`` wraps sub-agent / spawn / parallel return
     strings with ``wrap_external_content``.
   - ``core/agent.py`` wraps ``tool_summary`` replay + ``sub_agent_records``
     preview with ``wrap_external_content``.

B. R4-15 — POLICIES.yaml strict validation + last-known-good:
   - ``core/policy_v2/migration.py`` tracks ``unknown_security_keys`` +
     removes lossy ``bool()`` cast.
   - ``schema.py`` uses ``Strict()`` metadata on boolean fields + adds
     regex / path validators.
   - ``global_engine.py`` introduces ``_LAST_KNOWN_GOOD`` cache with
     dedicated lock + ``_recover_from_load_failure`` fallback.
   - ``reset_policy_v2_layer`` clears LKG.

C. R5-17 — Audit JSONL hash chain:
   - ``core/policy_v2/audit_chain.py`` exposes ``ChainedJsonlWriter`` +
     ``verify_chain`` + ``GENESIS_HASH`` + singleton helpers.
   - ``audit_logger.py`` opts into chain by default + promotes
     ``safety_immune`` to top-level (with nested compat).
   - ``evolution_window.record_decision`` + ``system_tasks._append_audit``
     go through the chained writer.
   - ``api/routes/config.py`` returns ``chain_verification`` on
     ``GET /api/config/security/audit``.

D. Regression: prior milestones unchanged (C14 + C15 audit scripts still pass).
E. Tests: three new test files present + all green.
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
# A. Prompt injection hardening (Phase A)
# ---------------------------------------------------------------------------


def audit_phase_a() -> None:
    section("A. Phase A — Prompt injection hardening (R4-14)")

    ph_path = "src/openakita/core/policy_v2/prompt_hardening.py"
    if not (REPO / ph_path).exists():
        fail(f"{ph_path} missing")
    ok(f"{ph_path} exists")
    ph = _read(ph_path)
    must_contain(ph, "def wrap_external_content", ph_path)
    must_contain(ph, "def is_marker_present", ph_path)
    must_contain(ph, "TOOL_RESULT_HARDENING_RULES", ph_path)
    must_contain(ph, "secrets.token_hex(4)", f"{ph_path} (per-call nonce)")
    must_contain(ph, '_END_TOKEN}_ESCAPED"', f"{ph_path} (anti-forgery end)")
    must_contain(ph, '_BEGIN_TOKEN}_ESCAPED"', f"{ph_path} (anti-forgery begin)")

    builder = _read("src/openakita/prompt/builder.py")
    must_contain(
        builder,
        "from ..core.policy_v2.prompt_hardening import",
        "prompt/builder.py imports from prompt_hardening",
    )
    must_contain(
        builder,
        "TOOL_RESULT_HARDENING_RULES",
        "prompt/builder.py references TOOL_RESULT_HARDENING_RULES",
    )
    must_contain(
        builder,
        "_SAFETY_SECTION = _SAFETY_SECTION + ",
        "prompt/builder.py concatenates rules into _SAFETY_SECTION",
    )

    agent_handler = _read("src/openakita/tools/handlers/agent.py")
    must_contain(
        agent_handler,
        "wrap_external_content(str(result), source=f\"sub_agent:{agent_id}\")",
        "tools/handlers/agent.py::_delegate wraps result",
    )
    must_contain(
        agent_handler,
        "wrap_external_content(str(result), source=f\"spawn_agent:{ephemeral_id}\")",
        "tools/handlers/agent.py::_spawn wraps result",
    )
    must_contain(
        agent_handler,
        'wrap_external_content(\n                    result, source=f"parallel_sub_agent:{display_id}"\n                )',
        "tools/handlers/agent.py::_delegate_parallel wraps each result",
    )

    core_agent = _read("src/openakita/core/agent.py")
    must_contain(
        core_agent,
        'wrap_external_content(_tool_summary, source="tool_trace")',
        "core/agent.py: tool_summary replay wrap",
    )
    must_contain(
        core_agent,
        'wrap_external_content(\n                            preview[:500], source=f"sub_agent_preview:{name}"\n                        )',
        "core/agent.py: sub_agent_records preview wrap",
    )

    init_text = _read("src/openakita/core/policy_v2/__init__.py")
    must_contain(init_text, '"wrap_external_content"', "policy_v2/__init__.py")
    must_contain(init_text, '"TOOL_RESULT_HARDENING_RULES"', "policy_v2/__init__.py")


# ---------------------------------------------------------------------------
# B. POLICIES.yaml strict validation + LKG (Phase B)
# ---------------------------------------------------------------------------


def audit_phase_b() -> None:
    section("B. Phase B — POLICIES.yaml strict + last-known-good (R4-15)")

    migration = _read("src/openakita/core/policy_v2/migration.py")
    must_contain(migration, "unknown_security_keys: list[str]", "migration.py::MigrationReport")
    must_contain(
        migration,
        "if k not in _KNOWN_SECURITY_KEYS:",
        "migration.py: unknown key detection",
    )
    if "out_sec[\"enabled\"] = bool(src_sec[\"enabled\"])" in migration:
        fail("migration.py still has lossy bool() coercion on enabled")
    ok("migration.py: lossy bool() cast removed")
    must_contain(
        migration,
        "out_sec[\"enabled\"] = src_sec[\"enabled\"]",
        "migration.py: raw passthrough for enabled",
    )

    schema = _read("src/openakita/core/policy_v2/schema.py")
    must_contain(schema, "from pydantic import BaseModel, ConfigDict, Field, Strict, field_validator", "schema.py imports Strict")
    must_contain(schema, "_StrictBool = Annotated[bool, Strict()]", "schema.py: _StrictBool alias")
    must_contain(schema, "def _validate_regex_list", "schema.py: regex validator")
    must_contain(schema, "def _validate_safe_path", "schema.py: strict path validator")
    must_contain(schema, "def _validate_loose_path", "schema.py: loose path validator")
    must_contain(schema, "_check_regex_lists", "schema.py: ShellRiskConfig wires regex validator")
    must_contain(schema, "_check_log_path", "schema.py: AuditConfig wires path validator")
    must_contain(schema, "_check_snapshot_dir", "schema.py: CheckpointConfig wires path validator")

    ge = _read("src/openakita/core/policy_v2/global_engine.py")
    must_contain(ge, "_LAST_KNOWN_GOOD: PolicyConfigV2 | None = None", "global_engine.py: LKG cache")
    must_contain(ge, "_LKG_LOCK = threading.Lock()", "global_engine.py: LKG lock")
    must_contain(ge, "def _set_last_known_good", "global_engine.py")
    must_contain(ge, "def _get_last_known_good", "global_engine.py")
    must_contain(ge, "def _clear_last_known_good", "global_engine.py")
    must_contain(ge, "def _recover_from_load_failure", "global_engine.py")
    must_contain(
        ge,
        "load_policies_yaml(yaml_path, strict=True)",
        "global_engine.py: strict=True load",
    )
    must_contain(
        ge,
        "_clear_last_known_good()",
        "global_engine.py: reset_policy_v2_layer clears LKG",
    )


# ---------------------------------------------------------------------------
# C. Audit JSONL hash chain (Phase C)
# ---------------------------------------------------------------------------


def audit_phase_c() -> None:
    section("C. Phase C — Audit JSONL hash chain (R5-17)")

    ac_path = "src/openakita/core/policy_v2/audit_chain.py"
    if not (REPO / ac_path).exists():
        fail(f"{ac_path} missing")
    ok(f"{ac_path} exists")
    ac = _read(ac_path)
    must_contain(ac, "GENESIS_HASH: str = \"0\" * 64", ac_path)
    must_contain(ac, "class ChainedJsonlWriter", ac_path)
    must_contain(ac, "class ChainVerifyResult", ac_path)
    must_contain(ac, "def verify_chain", ac_path)
    must_contain(ac, "def get_writer", ac_path)
    must_contain(ac, "def reset_writers_for_testing", ac_path)
    must_contain(ac, "row_hash must be excluded from hash input", ac_path)
    must_contain(ac, "_truncated_tail_recovered", ac_path)

    audit_logger = _read("src/openakita/core/audit_logger.py")
    must_contain(
        audit_logger,
        "from .policy_v2.audit_chain import get_writer",
        "audit_logger.py uses ChainedJsonlWriter",
    )
    must_contain(
        audit_logger,
        "include_chain: bool = True",
        "audit_logger.py default include_chain=True",
    )
    must_contain(
        audit_logger,
        'entry["safety_immune"] = bool(si)',
        "audit_logger.py: safety_immune promoted to top-level",
    )

    ew = _read("src/openakita/core/policy_v2/evolution_window.py")
    must_contain(
        ew,
        "from .audit_chain import get_writer",
        "evolution_window.py uses ChainedJsonlWriter",
    )

    st = _read("src/openakita/core/policy_v2/system_tasks.py")
    must_contain(
        st,
        "from .audit_chain import get_writer",
        "system_tasks._append_audit uses ChainedJsonlWriter",
    )

    api_cfg = _read("src/openakita/api/routes/config.py")
    must_contain(
        api_cfg,
        "from openakita.core.policy_v2.audit_chain import verify_chain",
        "api/routes/config.py: imports verify_chain",
    )
    must_contain(
        api_cfg,
        '"chain_verification": chain_verification',
        "api/routes/config.py: exposes chain_verification in response",
    )

    schema = _read("src/openakita/core/policy_v2/schema.py")
    must_contain(schema, "include_chain: _StrictBool = True", "schema.py: include_chain default flipped")

    init_text = _read("src/openakita/core/policy_v2/__init__.py")
    must_contain(init_text, '"ChainedJsonlWriter"', "policy_v2/__init__.py")
    must_contain(init_text, '"verify_chain"', "policy_v2/__init__.py")
    must_contain(init_text, '"GENESIS_HASH"', "policy_v2/__init__.py")


# ---------------------------------------------------------------------------
# D. Regression — prior audits still pass
# ---------------------------------------------------------------------------


def audit_phase_d_regression() -> None:
    section("D. Regression — C14 + C15 audit scripts still pass")
    for script in ("c14_audit.py", "c15_audit.py"):
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
            fail(f"{script} exited non-zero — C16 regressed prior milestone")
        ok(f"{script} passes")


# ---------------------------------------------------------------------------
# E. Tests — three new files present + all green
# ---------------------------------------------------------------------------


C16_TEST_FILES = [
    "tests/unit/test_policy_v2_c16_prompt_hardening.py",
    "tests/unit/test_policy_v2_c16_audit_chain.py",
    "tests/unit/test_policy_v2_c16_yaml_strict.py",
]


def audit_phase_e_tests() -> None:
    section("E. Tests — three C16 test files present + green")
    for rel in C16_TEST_FILES:
        if not (REPO / rel).exists():
            fail(f"{rel} missing")
        ok(f"{rel} exists")

    cp = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *C16_TEST_FILES,
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
        fail("C16 tests failed")
    summary = [
        line for line in out.splitlines() if "passed" in line and "in " in line
    ]
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
    audit_phase_d_regression()
    audit_phase_e_tests()
    print(
        "\n+ C16 audit passed: A (prompt) / B (yaml strict + LKG) / "
        "C (audit chain) / D (C14+C15 regression) / E (tests)."
    )


if __name__ == "__main__":
    main()
