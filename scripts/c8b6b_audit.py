"""C8b-6b audit — v1 ``policy.py`` 整文件物理删除验证。

D1 — 文件已删 + 模块不可导入
D2 — adapter.py 内 v1 桥接 helper 全部删除（``decision_to_v1_result`` /
     ``evaluate_via_v2_to_v1_result`` / ``_v2_action_to_v1_decision`` /
     延迟 import 的 ``..policy``）
D3 — 生产代码 + 测试 + scripts 全树 0 处 ``from openakita.core.policy import``
     或 ``from .policy import``（除 channels.policy 是不同模块）
D4 — v1-only 测试文件（``test_security.py``）已删；其他 v1 测试已迁移到 v2
D5 — v2 主入口 ``evaluate_via_v2`` + ``apply_resolution`` + ``apply_resolution``
     模块级仍可正常 import 且执行

每个 dimension 失败立即抛 AssertionError，方便 CI 打印根因。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _strip_comments(text: str) -> str:
    # 剥离纯注释行 + 三引号文档块（避免 doc 中的字面量误报）
    out: list[str] = []
    in_doc = False
    for raw in text.splitlines():
        triple_count = raw.count('"""') + raw.count("'''")
        if triple_count % 2 == 1:
            in_doc = not in_doc
            continue
        if triple_count >= 2 and not in_doc:
            continue
        if in_doc:
            continue
        if raw.lstrip().startswith("#"):
            continue
        out.append(raw)
    return "\n".join(out)


def d1_file_and_module_deleted() -> None:
    print("=== C8b-6b D1 file + module deletion ===")
    f = ROOT / "src" / "openakita" / "core" / "policy.py"
    assert not f.exists(), f"policy.py 仍存在于磁盘: {f}"
    print("  src/openakita/core/policy.py 已删除: OK")

    try:
        __import__("openakita.core.policy")
    except ModuleNotFoundError:
        print("  openakita.core.policy 不可导入: OK")
    else:
        raise AssertionError("openakita.core.policy 仍可导入——清理不彻底")

    print("D1 PASS\n")


def d2_adapter_v1_bridges_deleted() -> None:
    print("=== C8b-6b D2 adapter v1 bridges deleted ===")
    adapter_path = ROOT / "src" / "openakita" / "core" / "policy_v2" / "adapter.py"
    body = _strip_comments(adapter_path.read_text(encoding="utf-8"))

    for sym in (
        "def decision_to_v1_result",
        "def evaluate_via_v2_to_v1_result",
        "def _v2_action_to_v1_decision",
    ):
        assert sym not in body, f"adapter.py 仍声明 {sym}"
    print("  3 个 v1 桥接 helper 已删: OK")

    # 延迟 import ``from ..policy import`` 已删
    for stmt in (
        "from ..policy import PolicyDecision",
        "from ..policy import PolicyResult",
    ):
        assert stmt not in body, f"adapter.py 仍含延迟 import: {stmt}"
    print("  adapter 内 ..policy 延迟 import 已清: OK")

    # public API 仍正确导出
    from openakita.core.policy_v2 import adapter

    assert hasattr(adapter, "evaluate_via_v2"), "evaluate_via_v2 缺失"
    assert hasattr(adapter, "evaluate_message_intent_via_v2"), "message intent helper 缺失"
    assert hasattr(adapter, "V2_TO_V1_DECISION"), "V2_TO_V1_DECISION 缺失"
    assert hasattr(adapter, "build_policy_name"), "build_policy_name 缺失"
    assert hasattr(adapter, "build_metadata_for_legacy_callers"), "build_metadata_for_legacy_callers 缺失"
    assert not hasattr(adapter, "decision_to_v1_result"), "decision_to_v1_result 应已删"
    assert not hasattr(adapter, "evaluate_via_v2_to_v1_result"), "v1 一步式 helper 应已删"
    assert not hasattr(adapter, "_v2_action_to_v1_decision"), "私有桥接函数应已删"
    print("  adapter public API 完整 + v1 helper 全删: OK")

    print("D2 PASS\n")


def d3_no_v1_imports_anywhere() -> None:
    """扫描全树确保没有任何文件再 ``from ... policy import``（v1 模块）。

    用 ``^\\s*from ... import`` 行首正则避免误报 audit 脚本里的字符串字面量
    （如 c8b1_audit.py 用字符串做断言、本脚本本身也用字符串做匹配）。
    """
    print("=== C8b-6b D3 no v1 policy imports anywhere ===")
    import re

    # 行首 import 模式（剥离 docstring 后再匹配）
    pat_full = re.compile(r"^\s*from\s+openakita\.core\.policy\s+import\b")
    pat_rel_dotdot = re.compile(r"^\s*from\s+\.\.core\.policy\s+import\b")
    pat_rel_dot = re.compile(r"^\s*from\s+\.policy\s+import\b")

    repo_paths = [
        ROOT / "src" / "openakita",
        ROOT / "tests",
        ROOT / "scripts",
    ]
    # ``from .policy import`` 在 channels/ 子树合法（channels.policy 是不同模块）
    channels_root = ROOT / "src" / "openakita" / "channels"

    bad: list[tuple[Path, int, str]] = []
    file_count = 0
    for root in repo_paths:
        for py in root.rglob("*.py"):
            # 跳过本审计脚本自身（有大量字面量 "from ... import" 字符串）
            if py.resolve() == Path(__file__).resolve():
                continue
            file_count += 1
            text = py.read_text(encoding="utf-8")
            stripped = _strip_comments(text)
            in_channels = channels_root in py.parents
            for i, line in enumerate(stripped.splitlines(), start=1):
                if pat_full.search(line) or pat_rel_dotdot.search(line) or pat_rel_dot.search(line) and not in_channels:
                    bad.append((py, i, line.strip()))

    assert not bad, "残余 v1 import:\n" + "\n".join(
        f"  {p.relative_to(ROOT)}:{ln}: {code}" for p, ln, code in bad
    )
    print(f"  scanned {file_count} files: 0 v1 imports")

    print("D3 PASS\n")


def d4_v1_only_tests_deleted_or_migrated() -> None:
    print("=== C8b-6b D4 v1-only tests deleted / migrated ===")
    tests_root = ROOT / "tests"

    # test_security.py 整文件已删
    f1 = tests_root / "unit" / "test_security.py"
    assert not f1.exists(), f"v1-only {f1} 仍存在"
    print("  tests/unit/test_security.py 已删: OK")

    # test_remaining_qa_fixes.py 仍在但无 v1 import
    f2 = tests_root / "unit" / "test_remaining_qa_fixes.py"
    assert f2.exists()
    body2 = f2.read_text(encoding="utf-8")
    assert "from openakita.core.policy import" not in body2
    print("  test_remaining_qa_fixes.py v1 import 已清: OK")

    # test_trusted_paths.py fixture 已迁 v2
    f3 = tests_root / "unit" / "test_trusted_paths.py"
    body3 = f3.read_text(encoding="utf-8")
    assert "from openakita.core.policy import" not in body3, (
        "test_trusted_paths.py 仍有 v1 import"
    )
    assert "set_engine_v2" in body3, "test_trusted_paths.py fixture 未迁到 v2"
    print("  test_trusted_paths.py fixture 已迁 v2: OK")

    # test_p0_regression.py 3 P0-1 测试已迁 v2
    f4 = tests_root / "e2e" / "test_p0_regression.py"
    body4 = f4.read_text(encoding="utf-8")
    assert "from openakita.core.policy import" not in body4
    assert "default_controlled_paths" in body4, "P0-1 controlled paths 测试未迁 v2"
    print("  test_p0_regression P0-1 测试已迁 v2: OK")

    print("D4 PASS\n")


def d5_v2_main_entries_healthy() -> None:
    print("=== C8b-6b D5 v2 main entries smoke ===")
    from openakita.core.policy_v2 import (
        DecisionAction,
        apply_resolution,
        evaluate_via_v2,
    )
    from openakita.core.policy_v2.global_engine import (
        is_initialized,
        reset_policy_v2_layer,
    )

    # smoke：read_file 触发 v2 评估，返回合法决策
    decision = evaluate_via_v2("read_file", {"path": "README.md"})
    assert decision is not None
    assert decision.action in (
        DecisionAction.ALLOW,
        DecisionAction.CONFIRM,
        DecisionAction.DENY,
    )
    print(f"  evaluate_via_v2('read_file') → {decision.action.value}: OK")

    # apply_resolution：no-op 调用不抛
    result = apply_resolution("nonexistent-confirm-id", "deny")
    # 没有对应 confirm → bus 返回 False；不抛即可
    assert result in (True, False)
    print(f"  apply_resolution(unknown) → {result}: OK")

    # hot-reload 入口
    reset_policy_v2_layer()
    assert not is_initialized(), "reset_policy_v2_layer 未清 v2 单例"
    # 下一次 evaluate 触发 lazy reload
    _ = evaluate_via_v2("read_file", {"path": "README.md"})
    assert is_initialized()
    print("  reset_policy_v2_layer + lazy reload: OK")

    print("D5 PASS\n")


def main() -> None:
    d1_file_and_module_deleted()
    d2_adapter_v1_bridges_deleted()
    d3_no_v1_imports_anywhere()
    d4_v1_only_tests_deleted_or_migrated()
    d5_v2_main_entries_healthy()
    print("C8b-6b ALL 5 DIMENSIONS PASS")


if __name__ == "__main__":
    main()
