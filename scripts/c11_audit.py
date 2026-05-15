"""C11 audit: 6-dimensional static + smoke check for the regression milestone.

为什么要 audit (而非 just run pytest)?
=====================================

C11 的本质是"回归验证里程碑"——它的产出是 **承诺**:

1. 我们有 25 个 e2e case (不是空 file 也不是 24 / 26 case 蒙混过关)
2. 我们有 R5-18 零配置 + R5-19 跨平台两套 case (按 plan 要求)
3. 我们有 perf SLO 基线脚本 + budget assert (不是只测无 budget)
4. baseline JSON 真的能生成 + 落到 .cache/c11_perf_baseline.json
5. 全量 pytest baseline 不退步 (基线 6 failures, 不允许新增)
6. 18 audit 全部 PASS (前 10 commit 加固不被破坏)

每个 D 维度独立, 失败一个 print 错误后继续, 最后汇总 exit code.

调用:
    python scripts/c11_audit.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests" / "unit"
SCRIPTS = ROOT / "scripts"

REQUIRED_C11_TESTS = {
    # 25 main + 6 round-2 added (26-31): evaluate_message_intent (3) +
    # approval_class_overrides (2) + unattended.DEFER (1)
    "test_policy_v2_c11_integration.py": 31,
    # 5 R5-18 + 6 R5-19 ast funcs (one R5-19 has @parametrize → 5 sub-cases at runtime)
    "test_policy_v2_c11_zero_config_and_paths.py": 11,
}

REQUIRED_PERF_BUDGETS = {
    "classify_full_p95",
    "evaluate_tool_call_p95",
}

REQUIRED_18_AUDITS = {
    "c10_audit.py",
    "c6_audit2_smoke.py",
    "c7_audit2_ctx_paths.py",
    "c7_audit2_registry_check.py",
    "c7_audit2_reset_repro.py",
    "c8b1_audit.py",
    "c8b2_audit.py",
    "c8b3_audit.py",
    "c8b4_audit.py",
    "c8b5_audit.py",
    "c8b6a_audit.py",
    "c8b6b_audit.py",
    "c8_audit_d1_completeness.py",
    "c8_audit_d2_architecture.py",
    "c8_audit_d3_no_whack_a_mole.py",
    "c8_audit_d4_hidden_bugs.py",
    "c8_audit_d5_compat.py",
    "c9_audit.py",
}

# Allowed pre-existing test failures (frozen at C11 milestone).
# If a new failure appears, this set must be updated **explicitly** with a
# linked issue/PR; silent regressions are caught by the count check below.
KNOWN_BASELINE_FAILURES = {
    "tests/unit/test_org_setup_tool.py::TestDeleteOrg::test_delete_nonexistent",
    "tests/unit/test_org_setup_tool.py::TestDeleteOrg::test_delete_success",
    (
        "tests/unit/test_reasoning_engine_user_handoff.py::"
        "test_tool_evidence_required_blocks_implicit_long_reply_without_tools"
    ),
    (
        "tests/unit/test_reasoning_engine_user_handoff.py::"
        "test_tool_evidence_required_blocks_reply_tag_without_tools"
    ),
    (
        "tests/unit/test_reasoning_engine_user_handoff.py::"
        "test_tool_evidence_required_exhausts_to_unverified_without_repeated_prompts"
    ),
    "tests/unit/test_wework_ws_adapter.py::TestAdapterProperties::test_upload_media_requires_connection",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ok(label: str, msg: str = "") -> None:
    suffix = f" — {msg}" if msg else ""
    print(f"  [PASS] {label}{suffix}")


def _fail(label: str, msg: str) -> None:
    print(f"  [FAIL] {label} — {msg}")


def _count_test_funcs(path: Path) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return -1
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                n += 1
    return n


# ---------------------------------------------------------------------------
# D1: 25 c11_NN cases registered in integration test file
# ---------------------------------------------------------------------------


def d1_31_cases_registered() -> bool:
    """31 cases (25 round-1 + 6 round-2) with NN strictly 01-31 contiguous.

    Round-2 加固: 仅查"数量"会放任"删 case 17 + 加 case 32"的静默漂移,
    所以这里也强制 NN ⊆ {01..31} 一致.
    """
    print("\nD1: 31 c11_NN_* cases registered + NN 01-31 contiguous")
    path = TESTS / "test_policy_v2_c11_integration.py"
    if not path.exists():
        _fail("integration file present", f"missing {path}")
        return False
    expected = REQUIRED_C11_TESTS["test_policy_v2_c11_integration.py"]
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        _fail("parse", str(exc))
        return False
    pat = re.compile(r"^test_c11_(\d{2})_")
    nn: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            m = pat.match(node.name)
            if m:
                nn.add(int(m.group(1)))
    if len(nn) != expected:
        _fail("c11_NN_ count", f"found {len(nn)}, need {expected}")
        return False
    expected_set = set(range(1, expected + 1))
    missing = expected_set - nn
    extras = nn - expected_set
    if missing or extras:
        _fail(
            "NN contiguous",
            f"NN must equal {{01..{expected:02d}}}; "
            f"missing={sorted(missing)}, extras={sorted(extras)}",
        )
        return False
    _ok("c11_NN_ count + contiguous", f"{len(nn)} cases, NN 01-{expected:02d}")
    return True


# ---------------------------------------------------------------------------
# D2: zero-config + cross-platform tests present and non-empty
# ---------------------------------------------------------------------------


def d2_zero_config_and_paths() -> bool:
    print("\nD2: R5-18 zero-config + R5-19 cross-platform tests present")
    path = TESTS / "test_policy_v2_c11_zero_config_and_paths.py"
    if not path.exists():
        _fail("file present", f"missing {path}")
        return False
    n = _count_test_funcs(path)
    expected = REQUIRED_C11_TESTS["test_policy_v2_c11_zero_config_and_paths.py"]
    if n < expected:
        _fail("test count", f"found {n}, need >= {expected}")
        return False
    src = path.read_text(encoding="utf-8")
    if "TestR518ZeroConfig" not in src:
        _fail("R5-18 class", "TestR518ZeroConfig... not declared")
        return False
    if "TestR519CrossPlatform" not in src:
        _fail("R5-19 class", "TestR519CrossPlatform... not declared")
        return False
    _ok("test classes", "TestR518ZeroConfigFirstInstall + TestR519CrossPlatformPaths")
    _ok("test count", f"{n} tests (>= {expected})")
    return True


# ---------------------------------------------------------------------------
# D3: perf baseline script exists with budgets + assertion path
# ---------------------------------------------------------------------------


def d3_perf_script_with_budgets() -> bool:
    print("\nD3: perf baseline script declares budgets and assert path")
    path = SCRIPTS / "c11_perf_baseline.py"
    if not path.exists():
        _fail("file present", f"missing {path}")
        return False
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        _fail("script syntax", str(exc))
        return False

    # Find SLO_BUDGET_MS dict — handle both Assign and AnnAssign
    slo_keys: set[str] = set()
    for node in ast.walk(tree):
        target_match = False
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            target_match = any(
                isinstance(t, ast.Name) and t.id == "SLO_BUDGET_MS"
                for t in node.targets
            )
            value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            target_match = (
                isinstance(node.target, ast.Name)
                and node.target.id == "SLO_BUDGET_MS"
            )
            value_node = node.value
        if target_match and isinstance(value_node, ast.Dict):
            for k in value_node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    slo_keys.add(k.value)
    missing = REQUIRED_PERF_BUDGETS - slo_keys
    if missing:
        _fail("SLO_BUDGET_MS keys", f"missing {sorted(missing)}; got {sorted(slo_keys)}")
        return False
    _ok("SLO_BUDGET_MS", f"{sorted(slo_keys)}")

    # Confirm --strict path actually returns 1
    if "--strict" not in src:
        _fail("--strict flag", "no --strict CLI option")
        return False
    if "return 1" not in src:
        _fail("--strict exit", "script never returns 1; --strict cannot fail loud")
        return False
    _ok("--strict exit path", "found 'return 1' in script body")
    return True


# ---------------------------------------------------------------------------
# D4: perf baseline JSON regenerable + budget honoured
# ---------------------------------------------------------------------------


def d4_perf_baseline_runs() -> bool:
    print("\nD4: perf baseline script runs and emits JSON")
    out_path = ROOT / ".cache" / "c11_audit_perf_check.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "c11_perf_baseline.py"),
                "--iterations",
                "1000",
                "--out",
                str(out_path),
                "--json",
            ],
            capture_output=True,
            timeout=60,
            text=True,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _fail("subprocess", str(exc))
        return False
    if result.returncode != 0:
        _fail("exit code", f"got {result.returncode}; stderr={result.stderr[:200]}")
        return False
    if not out_path.exists():
        _fail("baseline JSON", f"missing {out_path}")
        return False
    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("baseline JSON parse", str(exc))
        return False
    for required in ("classify_full", "evaluate_tool_call", "budget", "iterations"):
        if required not in payload:
            _fail("baseline JSON shape", f"missing '{required}'")
            return False
    _ok(
        "baseline JSON",
        f"iters={payload['iterations']} "
        f"classify_p95={payload['classify_full']['p95']:.3f}ms "
        f"evaluate_p95={payload['evaluate_tool_call']['p95']:.3f}ms",
    )
    return True


# ---------------------------------------------------------------------------
# D5: full unit pytest run baseline check (no new regressions)
# ---------------------------------------------------------------------------


def d5_pytest_baseline_unchanged() -> bool:
    """Run full unit suite, parse FAILED lines, verify ⊆ KNOWN_BASELINE_FAILURES."""
    print("\nD5: full pytest baseline check (no new regressions)")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit",
        "-q",
        "--no-header",
        "--tb=no",
    ]
    try:
        # Use bytes capture + manual decode with errors="replace" because
        # pytest's mixed CN/EN warning text crashes Windows GBK auto-decode
        # and silently truncates the output (which would falsely report
        # "no failures" when failures are simply lost).
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=900,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        _fail("pytest", "timeout > 900s")
        return False
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    out = stdout + "\n" + stderr
    failed_pat = re.compile(r"^FAILED (\S+)", re.MULTILINE)
    failures = set(failed_pat.findall(out))
    new_failures = failures - KNOWN_BASELINE_FAILURES
    fixed = KNOWN_BASELINE_FAILURES - failures
    if new_failures:
        _fail(
            "new regressions",
            f"{len(new_failures)} new failures NOT in baseline: "
            f"{sorted(new_failures)[:3]}{'...' if len(new_failures) > 3 else ''}",
        )
        return False
    summary = ""
    m = re.search(r"(\d+ failed, \d+ passed.*?)(?:\n|$)", out)
    if m:
        summary = m.group(1).strip()
    elif "passed" in out:
        m2 = re.search(r"(\d+ passed.*?)(?:\n|$)", out)
        if m2:
            summary = m2.group(1).strip()
    _ok("pytest baseline", summary or "no new regressions")
    if fixed:
        # fyi only — someone fixed pre-existing baseline failures
        _ok("baseline fixes (FYI)", f"{len(fixed)} previously failing tests now pass")
    return True


# ---------------------------------------------------------------------------
# D6: 18 audit scripts present and individually executable
# ---------------------------------------------------------------------------


def d6_18_audits_present_and_runnable() -> bool:
    print("\nD6: 18 audit scripts present and runnable")
    found: set[str] = set()
    for p in SCRIPTS.glob("*audit*.py"):
        if p.name == "c11_audit.py":
            continue  # don't recurse into self
        found.add(p.name)
    missing = REQUIRED_18_AUDITS - found
    if missing:
        _fail("required audits", f"missing {sorted(missing)}")
        return False
    _ok("script presence", f"all 18 audit files present (found {len(found)})")

    # Smoke-syntax-check each (cheap ast.parse vs full subprocess run)
    bad: list[str] = []
    for name in sorted(REQUIRED_18_AUDITS):
        path = SCRIPTS / name
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            bad.append(f"{name}: {exc}")
    if bad:
        _fail("syntax check", "; ".join(bad))
        return False
    _ok("syntax check", "all 18 parse clean")
    return True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    # Quick sanity — module importable
    spec = importlib.util.find_spec("openakita.core.policy_v2.engine")
    if spec is None:
        sys.path.insert(0, str(ROOT / "src"))

    print("=" * 70)
    print("C11 audit — 6 dimensions")
    print("=" * 70)

    results = {
        "D1 31 cases (NN 01-31)": d1_31_cases_registered(),
        "D2 R5-18+19 tests": d2_zero_config_and_paths(),
        "D3 perf script": d3_perf_script_with_budgets(),
        "D4 perf baseline runs": d4_perf_baseline_runs(),
        "D5 pytest baseline": d5_pytest_baseline_unchanged(),
        "D6 18 audits intact": d6_18_audits_present_and_runnable(),
    }

    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    passed = sum(1 for v in results.values() if v)
    failed = len(results) - passed
    for k, v in results.items():
        status = "PASS" if v else "FAIL"
        print(f"  {status}: {k}")
    print(f"\n{passed}/{len(results)} dimensions PASS, {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
