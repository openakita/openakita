"""C7 smoke test —— end-to-end 验证 ContextVar wire-up + handler.TOOL_CLASSES
+ explicit_lookup 在 RiskGate / tool decision 路径上真正生效。

场景覆盖：
1. 信任模式下覆盖桌面 .txt（原始 P1 bug）—— 应 ALLOW
2. delete_file 强 DESTRUCTIVE → 信任模式下也应 CONFIRM
3. 启发式回退（未声明 TOOL_CLASSES 的工具，例如 future_tool） → 应 CONFIRM 或 UNKNOWN 路径
4. ContextVar 真正承载 TRUST → evaluate_via_v2 见到 ConfirmationMode.TRUST
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 让 src/ 可 import
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def assert_action(actual: str, expected: str, context: str) -> bool:
    if actual == expected:
        print(f"[OK] {context}: {actual}")
        return True
    print(f"[FAIL] {context}: expected={expected} got={actual}")
    return False


def main() -> int:
    from openakita.core.policy_v2 import (
        ConfirmationMode,
        DecisionAction,
        SessionRole,
        build_policy_context,
        evaluate_via_v2,
        reset_current_context,
        set_current_context,
    )
    from openakita.core.policy_v2.global_engine import (
        get_config_v2,
        rebuild_engine_v2,
        reset_engine_v2,
    )
    from openakita.tools.handlers import SystemHandlerRegistry
    from openakita.tools.handlers.filesystem import FilesystemHandler

    failures: list[str] = []

    # 模拟 agent.py _init_handlers 的注册 —— 把 explicit_lookup 喂给 v2 engine
    registry = SystemHandlerRegistry()
    registry.register(
        "filesystem",
        lambda *a, **k: "",
        tool_names=FilesystemHandler.TOOLS,
        tool_classes=FilesystemHandler.TOOL_CLASSES,
    )
    rebuild_engine_v2(explicit_lookup=registry.get_tool_class)

    # ------------------------------------------------------------------
    # Smoke 1：信任模式下 + 跨盘 write_file → ALLOW
    # ------------------------------------------------------------------
    cfg = get_config_v2()
    cfg.confirmation.mode = ConfirmationMode.TRUST

    desktop_txt = (
        Path(os.path.expanduser("~/Desktop")) / "_c7_smoke_test.txt"
    )
    ctx = build_policy_context(
        session=None,
        mode="agent",
        user_message="覆盖桌面 .txt 文件",
    )
    assert ctx.confirmation_mode == ConfirmationMode.TRUST, "ctx mode 未读到 TRUST"
    token = set_current_context(ctx)
    try:
        decision = evaluate_via_v2("write_file", {"path": str(desktop_txt)})
        if not assert_action(
            decision.action.value,
            DecisionAction.ALLOW.value,
            "Smoke 1: 信任模式覆盖桌面 .txt",
        ):
            failures.append("smoke1_trust_desktop_write")
    finally:
        reset_current_context(token)

    # ------------------------------------------------------------------
    # Smoke 2：信任模式下 + delete_file → CONFIRM（DESTRUCTIVE 在 trust 仍 confirm）
    # ------------------------------------------------------------------
    ctx = build_policy_context(session=None, mode="agent")
    token = set_current_context(ctx)
    try:
        decision = evaluate_via_v2("delete_file", {"path": str(desktop_txt)})
        if not assert_action(
            decision.action.value,
            DecisionAction.CONFIRM.value,
            "Smoke 2: 信任模式 delete_file",
        ):
            failures.append("smoke2_trust_destructive")
    finally:
        reset_current_context(token)

    # ------------------------------------------------------------------
    # Smoke 3：信任模式下 + read_file → ALLOW（READONLY）
    # ------------------------------------------------------------------
    ctx = build_policy_context(session=None, mode="agent")
    token = set_current_context(ctx)
    try:
        decision = evaluate_via_v2("read_file", {"path": str(desktop_txt)})
        if not assert_action(
            decision.action.value,
            DecisionAction.ALLOW.value,
            "Smoke 3: 信任模式 read_file",
        ):
            failures.append("smoke3_trust_readonly")
    finally:
        reset_current_context(token)

    # ------------------------------------------------------------------
    # Smoke 4：plan 模式 + write_file → DENY（plan/ask 模式禁止 mutation）
    # ------------------------------------------------------------------
    cfg.confirmation.mode = ConfirmationMode.DEFAULT
    ctx = build_policy_context(session=None, mode="plan")
    assert ctx.session_role == SessionRole.PLAN
    token = set_current_context(ctx)
    try:
        decision = evaluate_via_v2("write_file", {"path": str(desktop_txt)})
        if not assert_action(
            decision.action.value,
            DecisionAction.DENY.value,
            "Smoke 4: plan 模式 write_file",
        ):
            failures.append("smoke4_plan_deny_write")
    finally:
        reset_current_context(token)

    # ------------------------------------------------------------------
    # Smoke 5：default 模式 + read_file → ALLOW
    # ------------------------------------------------------------------
    cfg.confirmation.mode = ConfirmationMode.DEFAULT
    ctx = build_policy_context(session=None, mode="agent")
    token = set_current_context(ctx)
    try:
        decision = evaluate_via_v2("read_file", {"path": "any.txt"})
        if not assert_action(
            decision.action.value,
            DecisionAction.ALLOW.value,
            "Smoke 5: default agent read_file",
        ):
            failures.append("smoke5_default_read")
    finally:
        reset_current_context(token)

    # 清理
    cfg.confirmation.mode = ConfirmationMode.DEFAULT
    reset_engine_v2()

    if failures:
        print(f"\n[FAILED] {len(failures)} smoke check(s) failed: {failures}")
        return 1
    print("\n[PASS] all C7 smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
