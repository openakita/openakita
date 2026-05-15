"""Two-dimensional decision matrix: (SessionRole × ConfirmationMode × ApprovalClass) → DecisionAction.

参考 plan §3.4 + §3.6（R2-8 / R2-13）。

设计要点（安全不变量）：
1. UNKNOWN 在任何模式下均不静默 ALLOW（除 DONT_ASK 仍是 CONFIRM，永不 ALLOW）
2. DESTRUCTIVE 在 trust 模式仍是 CONFIRM；STRICT 直接 DENY
3. plan / ask 模式下任何 mutation/exec/destructive 一律 DENY（read-only 安全壳）
4. INTERACTIVE 一律 ALLOW（ask_user 类工具）；IM 渠道下 desktop_*/browser_* 的屏蔽由
   engine 层 channel-class compatibility 检查负责，不在矩阵层
5. 任何未配置组合默认 DENY（safety-by-default）

C1 提供基础矩阵；C3 PolicyEngineV2 在 step 5 调 lookup() 取得初始 action，再叠加
safety_immune / owner_only / replay / trusted_path / death_switch 等 step 修正。
"""

from __future__ import annotations

from .enums import ApprovalClass, ConfirmationMode, DecisionAction, SessionRole

A = DecisionAction.ALLOW
C = DecisionAction.CONFIRM
D = DecisionAction.DENY


_PLAN_DEFAULTS: dict[ApprovalClass, DecisionAction] = {
    ApprovalClass.READONLY_SCOPED: A,
    ApprovalClass.READONLY_GLOBAL: A,
    ApprovalClass.READONLY_SEARCH: A,
    ApprovalClass.MUTATING_SCOPED: D,
    ApprovalClass.MUTATING_GLOBAL: D,
    ApprovalClass.DESTRUCTIVE: D,
    ApprovalClass.EXEC_LOW_RISK: D,
    ApprovalClass.EXEC_CAPABLE: D,
    ApprovalClass.CONTROL_PLANE: C,
    ApprovalClass.INTERACTIVE: A,
    ApprovalClass.NETWORK_OUT: A,
    ApprovalClass.UNKNOWN: D,
}


_ASK_DEFAULTS: dict[ApprovalClass, DecisionAction] = {
    ApprovalClass.READONLY_SCOPED: A,
    ApprovalClass.READONLY_GLOBAL: A,
    ApprovalClass.READONLY_SEARCH: A,
    ApprovalClass.MUTATING_SCOPED: D,
    ApprovalClass.MUTATING_GLOBAL: D,
    ApprovalClass.DESTRUCTIVE: D,
    ApprovalClass.EXEC_LOW_RISK: D,
    ApprovalClass.EXEC_CAPABLE: D,
    ApprovalClass.CONTROL_PLANE: D,
    ApprovalClass.INTERACTIVE: A,
    ApprovalClass.NETWORK_OUT: A,
    ApprovalClass.UNKNOWN: D,
}


_AGENT_MATRIX: dict[ApprovalClass, dict[ConfirmationMode, DecisionAction]] = {
    ApprovalClass.READONLY_SCOPED: {
        ConfirmationMode.DEFAULT: A,
        ConfirmationMode.ACCEPT_EDITS: A,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: A,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.READONLY_GLOBAL: {
        ConfirmationMode.DEFAULT: A,
        ConfirmationMode.ACCEPT_EDITS: A,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: C,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.READONLY_SEARCH: {
        ConfirmationMode.DEFAULT: A,
        ConfirmationMode.ACCEPT_EDITS: A,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: A,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.MUTATING_SCOPED: {
        ConfirmationMode.DEFAULT: C,
        ConfirmationMode.ACCEPT_EDITS: A,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: C,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.MUTATING_GLOBAL: {
        ConfirmationMode.DEFAULT: C,
        ConfirmationMode.ACCEPT_EDITS: C,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: C,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.DESTRUCTIVE: {
        ConfirmationMode.DEFAULT: C,
        ConfirmationMode.ACCEPT_EDITS: C,
        ConfirmationMode.TRUST: C,
        ConfirmationMode.STRICT: D,
        ConfirmationMode.DONT_ASK: C,
    },
    ApprovalClass.EXEC_LOW_RISK: {
        ConfirmationMode.DEFAULT: A,
        ConfirmationMode.ACCEPT_EDITS: A,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: C,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.EXEC_CAPABLE: {
        ConfirmationMode.DEFAULT: C,
        ConfirmationMode.ACCEPT_EDITS: C,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: C,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.CONTROL_PLANE: {
        ConfirmationMode.DEFAULT: C,
        ConfirmationMode.ACCEPT_EDITS: C,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: C,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.INTERACTIVE: {
        ConfirmationMode.DEFAULT: A,
        ConfirmationMode.ACCEPT_EDITS: A,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: A,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.NETWORK_OUT: {
        ConfirmationMode.DEFAULT: A,
        ConfirmationMode.ACCEPT_EDITS: A,
        ConfirmationMode.TRUST: A,
        ConfirmationMode.STRICT: C,
        ConfirmationMode.DONT_ASK: A,
    },
    ApprovalClass.UNKNOWN: {
        ConfirmationMode.DEFAULT: C,
        ConfirmationMode.ACCEPT_EDITS: C,
        ConfirmationMode.TRUST: C,
        ConfirmationMode.STRICT: D,
        ConfirmationMode.DONT_ASK: C,
    },
}


_COORDINATOR_MATRIX: dict[ApprovalClass, dict[ConfirmationMode, DecisionAction]] = {
    klass: dict(modes) for klass, modes in _AGENT_MATRIX.items()
}
_COORDINATOR_MATRIX[ApprovalClass.MUTATING_GLOBAL][ConfirmationMode.TRUST] = C
_COORDINATOR_MATRIX[ApprovalClass.EXEC_CAPABLE][ConfirmationMode.TRUST] = C
_COORDINATOR_MATRIX[ApprovalClass.CONTROL_PLANE][ConfirmationMode.TRUST] = C


def lookup(
    role: SessionRole,
    mode: ConfirmationMode,
    klass: ApprovalClass,
) -> DecisionAction:
    """查二维矩阵。未配置组合默认 DENY（safety-by-default）。"""
    if role == SessionRole.PLAN:
        return _PLAN_DEFAULTS.get(klass, DecisionAction.DENY)
    if role == SessionRole.ASK:
        return _ASK_DEFAULTS.get(klass, DecisionAction.DENY)
    if role == SessionRole.COORDINATOR:
        per_class = _COORDINATOR_MATRIX.get(klass)
        if per_class is None:
            return DecisionAction.DENY
        return per_class.get(mode, DecisionAction.DENY)

    per_class = _AGENT_MATRIX.get(klass)
    if per_class is None:
        return DecisionAction.DENY
    return per_class.get(mode, DecisionAction.DENY)
