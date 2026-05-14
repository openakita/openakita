"""PolicyEngineV2 data models.

PolicyDecisionV2 是 evaluate_tool_call/evaluate_message_intent 的返回值。
PolicyResult 是 v1 名字别名（保持 orgs/runtime.py 等外部代码兼容，详见
docs §6.6）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .enums import ApprovalClass, DecisionAction, DecisionSource


@dataclass(slots=True)
class DecisionStep:
    """决策链中的单步记录。

    PolicyEngineV2 走 12 步，每步把判定写入 chain，便于审计与 dev 模式调试。
    """

    name: str
    action: DecisionAction
    note: str = ""
    duration_ms: float = 0.0


@dataclass(slots=True)
class PolicyDecisionV2:
    """v2 policy 决策结果。

    字段：
    - action: 终态动作（ALLOW/CONFIRM/DENY/DEFER）
    - reason: 简短人类可读原因
    - approval_class: 该次决策对应的工具分类
    - chain: 12 步决策链（dev 模式才回传给前端，详见 docs §8）
    - ttl_seconds: ALLOW 决策的有效期（None 表示单次有效）
    - safety_immune_match / is_owner_required / is_unattended_path:
      命中标记，UI/审计用
    - shell_risk_level: 仅 run_shell/run_powershell/opencli_run 等 EXEC 类有值
      （R2-5：ApprovalClassifier 一次性算出，避免 engine 与 classifier 各算一遍）
    - needs_sandbox: 高危 shell 命令推荐沙箱执行（R2-5；C7 sandbox 接入时消费）
    - needs_checkpoint: DESTRUCTIVE / MUTATING_GLOBAL 推荐先快照（R2-5；C8 checkpoint 接入时消费）
    - decided_at: 决策时间戳（用于 dedup / replay）
    - metadata: 自由扩展槽位
    """

    action: DecisionAction
    reason: str = ""
    approval_class: ApprovalClass = ApprovalClass.UNKNOWN
    chain: list[DecisionStep] = field(default_factory=list)
    ttl_seconds: float | None = None
    safety_immune_match: str | None = None
    is_owner_required: bool = False
    is_unattended_path: bool = False
    shell_risk_level: str | None = None
    needs_sandbox: bool = False
    needs_checkpoint: bool = False
    decided_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_allow(self) -> bool:
        return self.action == DecisionAction.ALLOW

    def is_deny(self) -> bool:
        return self.action == DecisionAction.DENY

    def is_confirm(self) -> bool:
        return self.action == DecisionAction.CONFIRM

    def is_defer(self) -> bool:
        return self.action == DecisionAction.DEFER

    def to_audit_dict(self) -> dict[str, Any]:
        """序列化给 audit_logger / SSE。chain 默认不带，按需 GET 详情。"""
        return {
            "action": self.action.value,
            "reason": self.reason,
            "approval_class": self.approval_class.value,
            "ttl_seconds": self.ttl_seconds,
            "safety_immune_match": self.safety_immune_match,
            "is_owner_required": self.is_owner_required,
            "is_unattended_path": self.is_unattended_path,
            "shell_risk_level": self.shell_risk_level,
            "needs_sandbox": self.needs_sandbox,
            "needs_checkpoint": self.needs_checkpoint,
            "decided_at": self.decided_at,
            "step_count": len(self.chain),
        }

    def to_ui_chain(self) -> list[dict[str, Any]]:
        """Compact JSON-safe serialization of ``chain`` for the
        ``security_confirm`` SSE payload (C23 P2-2).

        Plan C9 specified the modal should render ``decision_chain`` so the
        user can see *why* the engine reached its verdict. We ship every
        step's ``name`` / ``action`` / ``note``; ``duration_ms`` is
        intentionally dropped — it's almost always 0 (engine steps are
        sub-millisecond) and exposes nothing actionable to the UI.

        Returning ``list[dict]`` keeps the wire format trivially
        JSON-serializable and matches the existing
        ``decision_chain: list[dict[str, Any]]`` shape already used by
        ``pending_approvals.PendingApprovalEntry`` (see
        ``api/routes/pending_approvals._serialize``), so frontend code
        that handles deferred approvals can reuse the same renderer.
        """
        return [
            {
                "name": step.name,
                "action": step.action.value,
                "note": step.note,
            }
            for step in self.chain
        ]


PolicyResult = PolicyDecisionV2
"""v1 名字别名。orgs/runtime.py 等外部代码继续用此名 import（docs §6.6）。"""


@dataclass(slots=True)
class ToolCallEvent:
    """evaluate_tool_call 输入。

    classifier_source 由 ApprovalClassifier 在分类时回填，让 engine 可
    依据来源调整严格度（plugin 来源默认更严等，详见 plan §3.6）。
    """

    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    classifier_source: DecisionSource | None = None
    handler_name: str | None = None


@dataclass(slots=True)
class MessageIntentEvent:
    """evaluate_message_intent 输入（pre-LLM RiskGate 入口）。

    risk_intent 是 risk_intent.py:classify_risk_intent 的输出（保留独立模块，
    见 docs §5.5），engine 把它转化为 ApprovalClass + 决策。
    """

    message: str
    risk_intent: Any = None
