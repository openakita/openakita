"""PolicyEngineV2 exceptions hierarchy.

所有 v2 异常继承 PolicyError。调用方按场景捕获：

- DeniedByPolicy：明确拒绝（matrix 查得 DENY 或 safety_immune 命中）
- ConfirmationRequired：交互式路径需要用户确认（attended）
- DeferredApprovalRequired：无人值守路径写入 pending_approvals 后让 task 暂停
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import PolicyDecisionV2


class PolicyError(Exception):
    """v2 policy 错误基类。所有具体异常都继承此类。"""

    def __init__(self, message: str = "", *, decision: PolicyDecisionV2 | None = None):
        super().__init__(message)
        self.decision = decision


class DeniedByPolicy(PolicyError):
    """工具调用被策略明确拒绝。

    触发条件：
    - matrix 查得 DENY
    - safety_immune 命中且模式不允许覆盖
    - owner_only 且当前 user 非 owner
    - death_switch 触发
    """

    def __init__(
        self,
        message: str = "",
        *,
        tool: str | None = None,
        reason: str | None = None,
        decision: PolicyDecisionV2 | None = None,
    ):
        super().__init__(message, decision=decision)
        self.tool = tool
        self.reason = reason


class ConfirmationRequired(PolicyError):
    """需要交互式确认（attended 路径）。

    上层捕获后应：
    1. 触发 SSE/IM 卡片/CLI 提示
    2. 等待用户响应（wait_for_ui_resolution）
    3. 收到 allow/deny 后续行
    """

    def __init__(
        self,
        message: str = "",
        *,
        tool: str | None = None,
        decision: PolicyDecisionV2 | None = None,
        confirm_id: str | None = None,
    ):
        super().__init__(message, decision=decision)
        self.tool = tool
        self.confirm_id = confirm_id


class DeferredApprovalRequired(PolicyError):
    """计划任务/无人值守路径触发。

    上层（scheduler / spawn_agent / webhook）捕获后应：
    1. 写入 pending_approvals.json
    2. 通过 IM 卡片/email/inbox 通知 owner
    3. 让当前 task 进入 awaiting_approval 状态
    4. 用户批准后用 30s replay 策略重跑

    详见 plan §14 + docs §2.1（修复 execute_batch 撒谎 bug）。
    """

    def __init__(
        self,
        message: str = "",
        *,
        pending_id: str | None = None,
        unattended_strategy: str | None = None,
        decision: PolicyDecisionV2 | None = None,
        meta: dict[str, Any] | None = None,
    ):
        super().__init__(message, decision=decision)
        self.pending_id = pending_id
        self.unattended_strategy = unattended_strategy
        self.meta = meta or {}
