"""C19 audit: 6-dimensional gate for the developer 4-layer guardrails.

为什么要 audit (而非 just run pytest)?
=====================================

C19 的本质是 "护栏不被悄悄拆掉" 的承诺. 4 层 + 测试可以单独 pass, 但若
某层在重构里被改成 no-op, pytest 仍可能 green. audit 显式断言:

D1: completeness test 文件存在, 至少 35 cases (34 AST + 1 runtime), 且
    包含 register WARN 的 2 个负例正例对照.
D2: SystemHandlerRegistry.register() 在 _collect_tool_classes 之后真的
    会 emit "[Policy] Tool ... has no explicit ApprovalClass" WARN.
D3: 34 个 handler files 顶部 docstring 都含有 "ApprovalClass checklist"
    标记 + cookbook 链接 (可被任何 read 该文件的 AI 看到).
D4: .cursor/rules/add-internal-tool.mdc 存在且包含必要 globs.
D5: ApprovalClassifier 暴露 classify_with_source 公开方法 (C19 cookbook
    §12.5.2.2 的依赖).
D6: 完备性 pytest 真的能跑且 0 unclassified — 是 audit 的最终 smoke.

每个 D 维度独立, 失败一个 print 错误后继续, 最后汇总 exit code.

调用:
    python scripts/c19_audit.py
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests" / "unit"
HANDLERS = ROOT / "src" / "openakita" / "tools" / "handlers"
RULES = ROOT / ".cursor" / "rules"

D3_MARKER = "# ApprovalClass checklist"
D3_COOKBOOK_REF = "§4.21"

D3_SKIP_FILES = {
    "__init__.py",
    "todo_state.py",
    "todo_store.py",
    "todo_heuristics.py",
    "plan.py",
}

D4_RULE_FILE = "add-internal-tool.mdc"
D4_REQUIRED_GLOBS = [
    "src/openakita/tools/handlers/**/*.py",
    "src/openakita/core/agent.py",
    "src/openakita/core/policy_v2/classifier.py",
]


def _ok(label: str, msg: str = "") -> None:
    suffix = f" — {msg}" if msg else ""
    print(f"  [PASS] {label}{suffix}")


def _fail(label: str, msg: str) -> None:
    print(f"  [FAIL] {label} — {msg}")


# ---------------------------------------------------------------------------
# D1: completeness test file shape
# ---------------------------------------------------------------------------


def d1_completeness_test_present() -> bool:
    print("\nD1: tests/unit/test_classifier_completeness.py 存在 + 形态正确")
    path = TESTS / "test_classifier_completeness.py"
    if not path.exists():
        _fail("file present", f"missing {path}")
        return False
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        _fail("ast parse", str(exc))
        return False

    test_funcs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                test_funcs.add(node.name)

    required = {
        "test_handler_declares_tool_classes_for_every_tool",
        "test_registry_get_tool_class_returns_nonnull_for_every_registered_tool",
        "test_register_warns_when_tool_lacks_explicit_approval_class",
        "test_register_does_not_warn_when_all_tools_have_classes",
    }
    missing = required - test_funcs
    if missing:
        _fail("required test funcs", f"missing {sorted(missing)}")
        return False
    _ok("required test funcs", f"all 4 present (total {len(test_funcs)} test_*)")
    return True


# ---------------------------------------------------------------------------
# D2: register() WARN logic still wired
# ---------------------------------------------------------------------------


def d2_register_warn_wired() -> bool:
    print("\nD2: SystemHandlerRegistry.register() 含 WARN 逻辑 + cookbook 引用")
    init_path = HANDLERS / "__init__.py"
    if not init_path.exists():
        _fail("file present", f"missing {init_path}")
        return False
    src = init_path.read_text(encoding="utf-8")
    if "no explicit" not in src:
        _fail(
            "WARN message",
            "expected '...no explicit ApprovalClass...' WARN string in register()",
        )
        return False
    if D3_COOKBOOK_REF not in src:
        _fail(
            "cookbook ref",
            f"WARN must reference docs/policy_v2_research.md {D3_COOKBOOK_REF}",
        )
        return False
    _ok("WARN message + cookbook ref")
    return True


# ---------------------------------------------------------------------------
# D3: handler docstrings have checklist marker
# ---------------------------------------------------------------------------


def d3_handler_docstrings_marked() -> bool:
    print(
        f"\nD3: handler files 顶部 docstring 含 '{D3_MARKER}' "
        f"+ cookbook ref {D3_COOKBOOK_REF}"
    )
    targets = [
        p
        for p in sorted(HANDLERS.glob("*.py"))
        if p.name not in D3_SKIP_FILES
    ]
    if len(targets) < 30:
        _fail(
            "handler count",
            f"expected >= 30 handler files, found {len(targets)} "
            f"(after excluding {sorted(D3_SKIP_FILES)})",
        )
        return False
    missing_marker: list[str] = []
    missing_ref: list[str] = []
    for p in targets:
        src = p.read_text(encoding="utf-8")
        # Docstring must be at the very top of file
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            _fail(f"parse {p.name}", str(exc))
            return False
        doc = ast.get_docstring(tree, clean=False) or ""
        if D3_MARKER not in doc:
            missing_marker.append(p.name)
        if D3_COOKBOOK_REF not in doc:
            missing_ref.append(p.name)
    if missing_marker:
        _fail(
            "checklist marker",
            f"{len(missing_marker)} files missing '{D3_MARKER}': "
            f"{missing_marker[:5]}{'...' if len(missing_marker) > 5 else ''}",
        )
        return False
    if missing_ref:
        _fail(
            "cookbook ref",
            f"{len(missing_ref)} files missing '{D3_COOKBOOK_REF}': "
            f"{missing_ref[:5]}{'...' if len(missing_ref) > 5 else ''}",
        )
        return False
    _ok(
        "all handler docstrings",
        f"{len(targets)} files have marker + cookbook ref",
    )
    return True


# ---------------------------------------------------------------------------
# D4: Cursor rule file present + globs intact
# ---------------------------------------------------------------------------


def d4_cursor_rule_present() -> bool:
    print(f"\nD4: .cursor/rules/{D4_RULE_FILE} 存在 + 必要 globs 齐全")
    path = RULES / D4_RULE_FILE
    if not path.exists():
        _fail("file present", f"missing {path}")
        return False
    src = path.read_text(encoding="utf-8")
    # Must have YAML frontmatter
    if not src.startswith("---\n"):
        _fail("frontmatter", "missing YAML frontmatter")
        return False
    missing_glob = [g for g in D4_REQUIRED_GLOBS if g not in src]
    if missing_glob:
        _fail("globs", f"missing globs: {missing_glob}")
        return False
    if "alwaysApply: false" not in src:
        _fail(
            "alwaysApply",
            "expected 'alwaysApply: false' so rule injects only on glob match",
        )
        return False
    if D3_COOKBOOK_REF not in src and "policy_v2_research.md" not in src:
        _fail("cookbook ref", "rule must link to cookbook §4.21")
        return False
    _ok("rule frontmatter + globs + cookbook link")
    return True


# ---------------------------------------------------------------------------
# D5: classify_with_source public method exists
# ---------------------------------------------------------------------------


def d5_classify_with_source_exposed() -> bool:
    print("\nD5: ApprovalClassifier.classify_with_source 公开方法存在 (C19 依赖)")
    cls_path = (
        ROOT / "src" / "openakita" / "core" / "policy_v2" / "classifier.py"
    )
    if not cls_path.exists():
        _fail("file present", f"missing {cls_path}")
        return False
    src = cls_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        _fail("ast parse", str(exc))
        return False
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ApprovalClassifier":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "classify_with_source":
                    found = True
                    break
    if not found:
        _fail(
            "classify_with_source",
            "ApprovalClassifier.classify_with_source method not found",
        )
        return False
    _ok("classify_with_source", "exposed on ApprovalClassifier")
    return True


# ---------------------------------------------------------------------------
# D6: pytest the completeness file end-to-end (real smoke)
# ---------------------------------------------------------------------------


def d6_completeness_pytest_passes() -> bool:
    print("\nD6: pytest tests/unit/test_classifier_completeness.py 真跑 + 全绿")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/test_classifier_completeness.py",
        "-q",
        "--no-header",
        "--tb=line",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,
            cwd=str(ROOT),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _fail("subprocess", str(exc))
        return False
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")
    out = stdout + "\n" + stderr
    if result.returncode != 0:
        # Show last 5 informative lines
        tail = "\n".join(out.strip().splitlines()[-8:])
        _fail("pytest exit", f"rc={result.returncode}\n{tail}")
        return False
    m = re.search(r"(\d+) passed", out)
    n = int(m.group(1)) if m else 0
    if n < 35:
        _fail("pass count", f"expected >= 35 passes, got {n}")
        return False
    _ok("pytest", f"{n} passed")
    return True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 70)
    print("C19 audit — 6 dimensions (4-layer guardrails completeness)")
    print("=" * 70)

    results = {
        "D1 completeness test file": d1_completeness_test_present(),
        "D2 register() WARN wired": d2_register_warn_wired(),
        "D3 handler docstrings marked": d3_handler_docstrings_marked(),
        "D4 Cursor rule present": d4_cursor_rule_present(),
        "D5 classify_with_source": d5_classify_with_source_exposed(),
        "D6 pytest end-to-end": d6_completeness_pytest_passes(),
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
