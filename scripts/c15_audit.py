"""C15 audit — verify all three phases of Evolution / SystemTasks / Skill-MCP trust.

Phases
======

A. R4-12 / R4-13 / R5-21 — Skill / MCP declared_class trust 严格度取大:
   - ``core/policy_v2/declared_class_trust.py`` exists & exports the API.
   - ``SkillRegistry.get_tool_class`` routes through ``compute_effective_class``.
   - ``MCPClient.get_tool_class`` routes through ``compute_effective_class``.
   - ``MCPServerConfig.trust_level`` field exists (default ``"default"``).
   - ``classifier.heuristic_classify`` is publicly exported.

B. R4-10 / R4-11 — SYSTEM_TASKS.yaml whitelist + hash lock + bypass:
   - ``core/policy_v2/system_tasks.py`` exposes registry + bypass API.
   - ``identity/SYSTEM_TASKS.yaml.template`` ships as authoring template.
   - ``load_registry`` fails closed on lock mismatch (regex on docstring +
     verified via unit test).
   - ``request_bypass`` integrates ``CheckpointManager.create_checkpoint``
     when ``requires_backup=True``.

C. R4-9 — Evolution self-fix audit window:
   - ``core/policy_v2/evolution_window.py`` exposes window + audit API.
   - ``entry_point.classify_entry`` handles ``"evolution"`` /
     ``"evolution-self-fix"`` channels (unattended).
   - ``PolicyContext.evolution_fix_id`` field exists.
   - ``adapter.build_policy_context`` accepts and propagates
     ``evolution_fix_id`` (including via contextvar).
   - ``evolution.self_check._execute_fix_by_llm_decision`` installs
     classifier + window + contextvar around the fix agent.
   - ``engine._maybe_audit`` fans out to ``evolution_decisions.jsonl``.

D. Regression: prior milestones unchanged (C14 audit script still passes).
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
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    raise SystemExit(1)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def must_contain(text: str, pattern: str, where: str, *, regex: bool = False) -> None:
    found = bool(re.search(pattern, text)) if regex else (pattern in text)
    if not found:
        fail(f"{where}: expected {'pattern' if regex else 'literal'} {pattern!r}")
    ok(f"{where}: contains {pattern!r}")


# ---------------------------------------------------------------------------
# A. Skill/MCP declared_class trust rule (Phase A)
# ---------------------------------------------------------------------------


def audit_phase_a() -> None:
    section("A. Phase A — Skill/MCP declared_class trust (R4-12/13/R5-21)")

    dct_path = "src/openakita/core/policy_v2/declared_class_trust.py"
    if not (REPO / dct_path).exists():
        fail(f"{dct_path} missing")
    ok(f"{dct_path} exists")
    dct = _read(dct_path)
    must_contain(dct, "class DeclaredClassTrust(StrEnum)", dct_path)
    must_contain(dct, "def compute_effective_class", dct_path)
    must_contain(dct, "def infer_skill_declared_trust", dct_path)
    must_contain(dct, "def infer_mcp_declared_trust", dct_path)
    must_contain(dct, "most_strict", dct_path)

    classifier_text = _read("src/openakita/core/policy_v2/classifier.py")
    must_contain(
        classifier_text,
        "def heuristic_classify",
        "core/policy_v2/classifier.py",
    )

    skills_reg = _read("src/openakita/skills/registry.py")
    must_contain(
        skills_reg,
        "from ..core.policy_v2.declared_class_trust import",
        "skills/registry.py",
    )
    must_contain(
        skills_reg,
        "compute_effective_class",
        "skills/registry.py::get_tool_class",
    )
    must_contain(
        skills_reg,
        "infer_skill_declared_trust",
        "skills/registry.py::get_tool_class",
    )

    mcp_text = _read("src/openakita/tools/mcp.py")
    must_contain(
        mcp_text,
        "trust_level: str = \"default\"",
        "tools/mcp.py::MCPServerConfig",
    )
    must_contain(
        mcp_text,
        "infer_mcp_declared_trust",
        "tools/mcp.py::get_tool_class",
    )
    must_contain(
        mcp_text,
        "trust_level=str(server_data.get(\"trust_level\", \"default\"))",
        "tools/mcp.py::load_servers_from_config",
    )

    init_text = _read("src/openakita/core/policy_v2/__init__.py")
    must_contain(init_text, '"DeclaredClassTrust"', "policy_v2/__init__.py")
    must_contain(init_text, '"compute_effective_class"', "policy_v2/__init__.py")


# ---------------------------------------------------------------------------
# B. SYSTEM_TASKS.yaml whitelist + hash lock + bypass (Phase B)
# ---------------------------------------------------------------------------


def audit_phase_b() -> None:
    section("B. Phase B — SYSTEM_TASKS.yaml whitelist + hash lock (R4-10/R4-11)")

    st_path = "src/openakita/core/policy_v2/system_tasks.py"
    if not (REPO / st_path).exists():
        fail(f"{st_path} missing")
    ok(f"{st_path} exists")
    st = _read(st_path)
    must_contain(st, "class SystemTask", st_path)
    must_contain(st, "class SystemTaskRegistry", st_path)
    must_contain(st, "def load_registry", st_path)
    must_contain(st, "def request_bypass", st_path)
    must_contain(st, "def finalize_bypass", st_path)
    must_contain(st, "def compute_yaml_hash", st_path)
    must_contain(st, "def write_lock", st_path)
    must_contain(st, "def read_lock", st_path)
    must_contain(
        st,
        "checkpoint_mgr.create_checkpoint",
        f"{st_path}::request_bypass",
    )
    # Fail-closed assertions in docstring (capitalized in source)
    must_contain(
        st,
        "Fail-closed on tamper",
        f"{st_path} docstring",
    )

    # Template ships
    tmpl = "identity/SYSTEM_TASKS.yaml.template"
    if not (REPO / tmpl).exists():
        fail(f"{tmpl} missing")
    ok(f"{tmpl} exists")
    tmpl_text = _read(tmpl)
    must_contain(tmpl_text, "version: 1", tmpl)
    must_contain(tmpl_text, "tasks:", tmpl)

    init_text = _read("src/openakita/core/policy_v2/__init__.py")
    must_contain(init_text, '"SystemTaskRegistry"', "policy_v2/__init__.py")
    must_contain(init_text, '"request_bypass"', "policy_v2/__init__.py")
    must_contain(init_text, '"finalize_bypass"', "policy_v2/__init__.py")


# ---------------------------------------------------------------------------
# C. Evolution self-fix audit window + classifier ctx wiring (Phase C)
# ---------------------------------------------------------------------------


def audit_phase_c() -> None:
    section("C. Phase C — Evolution audit window + self-fix ctx (R4-9 + C14 follow-up)")

    ew_path = "src/openakita/core/policy_v2/evolution_window.py"
    if not (REPO / ew_path).exists():
        fail(f"{ew_path} missing")
    ok(f"{ew_path} exists")
    ew = _read(ew_path)
    must_contain(ew, "class EvolutionWindow", ew_path)
    must_contain(ew, "def open_window", ew_path)
    must_contain(ew, "def close_window", ew_path)
    must_contain(ew, "def record_decision", ew_path)
    must_contain(ew, "def set_active_fix_id", ew_path)
    must_contain(ew, "def reset_active_fix_id", ew_path)
    must_contain(ew, "DEFAULT_WINDOW_TTL_SECONDS", ew_path)

    entry_pt = _read("src/openakita/core/policy_v2/entry_point.py")
    must_contain(
        entry_pt,
        'channel_norm in ("evolution", "evolution-self-fix")',
        "entry_point.py::classify_entry",
    )

    ctx_text = _read("src/openakita/core/policy_v2/context.py")
    must_contain(
        ctx_text,
        "evolution_fix_id: str | None = None",
        "context.py::PolicyContext",
    )
    must_contain(
        ctx_text,
        "evolution_fix_id=self.evolution_fix_id",
        "context.py::PolicyContext.derive_child",
    )

    adapter_text = _read("src/openakita/core/policy_v2/adapter.py")
    must_contain(
        adapter_text,
        "evolution_fix_id: str | None = None",
        "adapter.py::build_policy_context signature",
    )
    must_contain(
        adapter_text,
        "from .evolution_window import get_active_fix_id",
        "adapter.py contextvar fallback",
    )
    must_contain(
        adapter_text,
        "evolution_fix_id=base.evolution_fix_id",
        "adapter.py parent_ctx path",
    )
    must_contain(
        adapter_text,
        "evolution_fix_id=effective_evolution_fix_id",
        "adapter.py session path",
    )

    self_check = _read("src/openakita/evolution/self_check.py")
    must_contain(
        self_check,
        "open_window",
        "evolution/self_check.py::_execute_fix_by_llm_decision",
    )
    must_contain(
        self_check,
        'classify_entry("evolution", force_unattended=True)',
        "evolution/self_check.py: classifier wiring",
    )
    must_contain(
        self_check,
        "set_active_fix_id",
        "evolution/self_check.py: contextvar install",
    )
    must_contain(
        self_check,
        "reset_active_fix_id",
        "evolution/self_check.py: contextvar reset (try/finally)",
    )
    must_contain(
        self_check,
        "close_window",
        "evolution/self_check.py: window close (try/finally)",
    )

    engine = _read("src/openakita/core/policy_v2/engine.py")
    must_contain(
        engine,
        "if ctx.evolution_fix_id",
        "engine.py::_maybe_audit",
    )
    must_contain(
        engine,
        "from .evolution_window import default_audit_path",
        "engine.py: evolution audit fan-out",
    )
    must_contain(
        engine,
        "from .evolution_window import record_decision",
        "engine.py: evolution audit fan-out",
    )

    init_text = _read("src/openakita/core/policy_v2/__init__.py")
    must_contain(init_text, '"EvolutionWindow"', "policy_v2/__init__.py")
    must_contain(init_text, '"open_window"', "policy_v2/__init__.py")
    must_contain(init_text, '"set_active_fix_id"', "policy_v2/__init__.py")
    must_contain(init_text, '"evolution_default_audit_path"', "policy_v2/__init__.py")


# ---------------------------------------------------------------------------
# D. Regression — prior C14 audit still passes
# ---------------------------------------------------------------------------


def audit_phase_d_c14_regression() -> None:
    section("D. Regression — C14 audit script still passes")
    cp = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "c14_audit.py")],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0:
        print(cp.stdout)
        print(cp.stderr)
        fail("c14_audit.py exited non-zero — C15 regressed C14")
    ok("c14_audit.py passes (C14 phases A–H unchanged)")


# ---------------------------------------------------------------------------
# E. Tests — three new files present + all green
# ---------------------------------------------------------------------------


C15_TEST_FILES = [
    "tests/unit/test_policy_v2_c15_declared_class_trust.py",
    "tests/unit/test_policy_v2_c15_system_tasks.py",
    "tests/unit/test_policy_v2_c15_evolution_window.py",
]


def audit_phase_e_tests() -> None:
    section("E. Tests — three C15 test files present + green")
    for rel in C15_TEST_FILES:
        if not (REPO / rel).exists():
            fail(f"{rel} missing")
        ok(f"{rel} exists")

    cp = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *C15_TEST_FILES,
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
        fail("C15 tests failed")
    # Last summary line typically ``N passed in Xs``
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
    audit_phase_d_c14_regression()
    audit_phase_e_tests()
    print("\n✓ C15 audit passed: A (trust) / B (system_tasks) / C (evolution) / D (C14 regression) / E (tests).")


if __name__ == "__main__":
    main()
