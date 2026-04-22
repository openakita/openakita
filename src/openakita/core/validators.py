"""
确定性验证器 (Agent Harness: Deterministic Validators)

在任务完成验证中混合使用确定性检查和 LLM 判断，减少对 LLM 验证的依赖。
确定性验证器不依赖 LLM，使用规则、文件检查、退出码等确定性方法验证任务结果。

验证器类型:
- PlanValidator: 验证 Plan 所有步骤状态
- ArtifactValidator: 验证交付物是否完整（基于 delivery_receipts）
- ToolSuccessValidator: 验证关键工具是否执行成功
- FileValidator: 验证文件操作结果（磁盘存在性/大小校验）
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)


class ValidationResult(StrEnum):
    """验证结果"""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"  # 验证器不适用于当前场景


@dataclass
class ValidatorOutput:
    """单个验证器的输出"""

    name: str
    result: ValidationResult
    reason: str = ""
    confidence: float = 1.0  # 确定性验证器 = 1.0


@dataclass
class ValidationReport:
    """综合验证报告"""

    outputs: list[ValidatorOutput] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        applicable = [o for o in self.outputs if o.result != ValidationResult.SKIP]
        return (
            all(o.result in (ValidationResult.PASS, ValidationResult.WARN) for o in applicable)
            if applicable
            else True
        )

    @property
    def any_failed(self) -> bool:
        return any(o.result == ValidationResult.FAIL for o in self.outputs)

    @property
    def failed_validators(self) -> list[ValidatorOutput]:
        return [o for o in self.outputs if o.result == ValidationResult.FAIL]

    @property
    def passed_count(self) -> int:
        return sum(1 for o in self.outputs if o.result == ValidationResult.PASS)

    @property
    def applicable_count(self) -> int:
        return sum(1 for o in self.outputs if o.result != ValidationResult.SKIP)

    def get_summary(self) -> str:
        """生成人可读摘要"""
        parts = []
        for o in self.outputs:
            if o.result == ValidationResult.SKIP:
                continue
            icon = (
                "✓"
                if o.result == ValidationResult.PASS
                else ("⚠" if o.result == ValidationResult.WARN else "✗")
            )
            parts.append(f"{icon} {o.name}: {o.reason}")
        return "\n".join(parts) if parts else "No applicable validators"


class BaseValidator(ABC):
    """验证器基类"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def validate(self, context: ValidationContext) -> ValidatorOutput: ...


@dataclass
class ValidationContext:
    """验证上下文（传递给所有验证器的数据）"""

    user_request: str = ""
    assistant_response: str = ""
    executed_tools: list[str] = field(default_factory=list)
    delivery_receipts: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    conversation_id: str = ""
    # --- 组织视角字段（默认值确保向后兼容；非组织 agent 永远是 0/False） ---
    # 当前激活 chain 子树下已 ACCEPTED 的子任务数（严格信号）
    accepted_child_count: int = 0
    # 该节点 mailbox 最近 60s 内是否收到过 deliverable_accepted 类事件（弱信号兜底）
    has_recent_accepted_signal: bool = False


class PlanValidator(BaseValidator):
    """Plan 步骤完成度验证（确定性，不用 LLM）"""

    @property
    def name(self) -> str:
        return "PlanValidator"

    def validate(self, context: ValidationContext) -> ValidatorOutput:
        try:
            from ..tools.handlers.plan import get_todo_handler_for_session, has_active_todo

            if not context.conversation_id or not has_active_todo(context.conversation_id):
                return ValidatorOutput(
                    name=self.name,
                    result=ValidationResult.SKIP,
                    reason="No active todo",
                )

            handler = get_todo_handler_for_session(context.conversation_id)
            plan = handler.get_plan_for(context.conversation_id) if handler else None
            if not plan:
                return ValidatorOutput(
                    name=self.name,
                    result=ValidationResult.SKIP,
                    reason="Plan not found",
                )

            steps = plan.get("steps", [])
            total = len(steps)
            _TERMINAL = ("completed", "skipped", "failed", "cancelled")
            terminal = sum(1 for s in steps if s.get("status") in _TERMINAL)
            pending = sum(1 for s in steps if s.get("status") in ("pending", "in_progress"))
            failed = sum(1 for s in steps if s.get("status") == "failed")

            if pending > 0:
                pending_ids = [
                    s.get("id", "?") for s in steps if s.get("status") in ("pending", "in_progress")
                ]
                return ValidatorOutput(
                    name=self.name,
                    result=ValidationResult.FAIL,
                    reason=f"{pending}/{total} steps pending: {pending_ids[:3]}",
                )

            if failed > 0:
                failed_ids = [s.get("id", "?") for s in steps if s.get("status") == "failed"]
                return ValidatorOutput(
                    name=self.name,
                    result=ValidationResult.WARN,
                    reason=f"All steps resolved but {failed} failed: {failed_ids[:3]}",
                )

            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.PASS,
                reason=f"All {total} steps completed ({terminal} terminal)",
            )

        except Exception as e:
            logger.debug(f"[Validator] PlanValidator error: {e}")
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.SKIP,
                reason=f"Plan check error: {e}",
            )


class ArtifactValidator(BaseValidator):
    """交付物完整性验证"""

    @property
    def name(self) -> str:
        return "ArtifactValidator"

    def validate(self, context: ValidationContext) -> ValidatorOutput:
        if "deliver_artifacts" not in context.executed_tools:
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.SKIP,
                reason="No deliver_artifacts call",
            )

        delivered = [r for r in context.delivery_receipts if r.get("status") == "delivered"]
        failed = [r for r in context.delivery_receipts if r.get("status") == "failed"]

        if failed:
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.FAIL,
                reason=f"{len(failed)} artifacts failed to deliver",
            )

        if delivered:
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.PASS,
                reason=f"{len(delivered)} artifacts delivered",
            )

        return ValidatorOutput(
            name=self.name,
            result=ValidationResult.FAIL,
            reason="deliver_artifacts called but no delivery receipts",
        )


class ToolSuccessValidator(BaseValidator):
    """关键工具执行成功验证"""

    @property
    def name(self) -> str:
        return "ToolSuccessValidator"

    def validate(self, context: ValidationContext) -> ValidatorOutput:
        if not context.executed_tools:
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.SKIP,
                reason="No tools executed",
            )

        error_results = []
        for tr in context.tool_results:
            if not isinstance(tr, dict):
                continue
            if tr.get("is_error", False):
                error_results.append(tr.get("tool_use_id", "?"))

        if error_results:
            total = len(context.tool_results)
            errors = len(error_results)
            if errors > total * 0.5:
                return ValidatorOutput(
                    name=self.name,
                    result=ValidationResult.FAIL,
                    reason=f"Majority of tool calls failed ({errors}/{total})",
                )

        return ValidatorOutput(
            name=self.name,
            result=ValidationResult.PASS,
            reason=f"{len(context.executed_tools)} tools executed",
        )


class CompletePlanValidator(BaseValidator):
    """验证 complete_todo 工具是否被调用"""

    @property
    def name(self) -> str:
        return "CompletePlanValidator"

    def validate(self, context: ValidationContext) -> ValidatorOutput:
        if "complete_todo" in context.executed_tools:
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.PASS,
                reason="complete_todo was called",
            )

        try:
            from ..tools.handlers.plan import has_active_todo

            if context.conversation_id and has_active_todo(context.conversation_id):
                return ValidatorOutput(
                    name=self.name,
                    result=ValidationResult.FAIL,
                    reason="Active plan exists but complete_todo not called",
                )
        except Exception:
            pass

        return ValidatorOutput(
            name=self.name,
            result=ValidationResult.SKIP,
            reason="No active plan to complete",
        )


class FileValidator(BaseValidator):
    """文件操作结果验证（磁盘级确定性校验）

    从 tool_results 文本中提取路径，校验文件在磁盘上的实际状态：
    - write_file / edit_file: 文件应存在且大小 > 0
    - delete_file: 文件应已不存在
    """

    _WRITE_PATH_RE = re.compile(
        r"文件已[写编][入辑][:：]\s*(.+?)(?:\s+\(\d+\s*bytes\)|（|$)", re.MULTILINE
    )
    _DELETE_PATH_RE = re.compile(r"(?:文件|目录)已删除[:：]\s*(.+?)\s*$", re.MULTILINE)

    @property
    def name(self) -> str:
        return "FileValidator"

    def validate(self, context: ValidationContext) -> ValidatorOutput:
        file_tools = {"write_file", "edit_file", "delete_file"}
        if not (file_tools & set(context.executed_tools)):
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.SKIP,
                reason="No file operations executed",
            )

        issues: list[str] = []
        checked = 0

        for tr in context.tool_results:
            if not isinstance(tr, dict):
                continue
            content = str(tr.get("content", ""))
            if tr.get("is_error"):
                continue

            # write / edit: 文件应存在
            m = self._WRITE_PATH_RE.search(content)
            if m:
                fpath = m.group(1).strip()
                checked += 1
                try:
                    p = Path(fpath)
                    if not p.exists():
                        issues.append(f"write/edit 目标不存在: {fpath}")
                    elif p.stat().st_size == 0:
                        issues.append(f"write/edit 目标为空文件: {fpath}")
                except OSError as e:
                    issues.append(f"无法检查 {fpath}: {e}")
                continue

            # delete: 文件应已不存在
            m = self._DELETE_PATH_RE.search(content)
            if m:
                fpath = m.group(1).strip()
                checked += 1
                try:
                    if Path(fpath).exists():
                        issues.append(f"delete 目标仍存在: {fpath}")
                except OSError:
                    pass
                continue

        if checked == 0:
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.SKIP,
                reason="No parseable file paths in tool results",
            )

        if issues:
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.WARN,
                reason=f"{len(issues)} issue(s): {'; '.join(issues[:3])}",
            )

        return ValidatorOutput(
            name=self.name,
            result=ValidationResult.PASS,
            reason=f"All {checked} file operation(s) verified on disk",
        )


class OrgDelegationValidator(BaseValidator):
    """组织协作者交付完成度验证。

    协调者节点（如 Editor-in-Chief / PlanningEditor）完成的方式是「下属
    交付物均已验收」，本身并不会调用 ``deliver_artifacts``。这种场景下
    原有 ArtifactValidator 不适用，verify 容易把汇总文本误判为
    ``verify_incomplete``。本 validator 在以下两种信号成立时回 ``PASS``：

    1) 严格信号：``accepted_child_count >= 1`` —— 当前激活 chain 子树下
       至少有 1 个子任务已 ACCEPTED（来自 ProjectStore）。
    2) 弱信号兜底：``has_recent_accepted_signal=True`` —— 该节点 mailbox
       最近 60s 内有 ``deliverable_accepted`` 类事件（runtime 层面）。

    非组织 agent 默认两个字段都是 0/False，validator 永远 SKIP，
    与原有 verify 流程行为一致。
    """

    @property
    def name(self) -> str:
        return "OrgDelegationValidator"

    def validate(self, context: ValidationContext) -> ValidatorOutput:
        accepted = int(getattr(context, "accepted_child_count", 0) or 0)
        recent = bool(getattr(context, "has_recent_accepted_signal", False))

        if accepted >= 1:
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.PASS,
                reason=(
                    f"{accepted} downstream task(s) already ACCEPTED in current chain — "
                    "treating coordinator response as completed"
                ),
            )
        if recent:
            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.PASS,
                reason=(
                    "recent deliverable_accepted signal in node mailbox — "
                    "treating coordinator response as completed (weak signal)"
                ),
            )
        return ValidatorOutput(
            name=self.name,
            result=ValidationResult.SKIP,
            reason="no accepted child task / no recent deliverable_accepted signal",
        )


# ==================== 验证器注册表 ====================

_DEFAULT_VALIDATORS: list[BaseValidator] = [
    PlanValidator(),
    ArtifactValidator(),
    ToolSuccessValidator(),
    FileValidator(),
    CompletePlanValidator(),
    OrgDelegationValidator(),
]


class ValidatorRegistry:
    """验证器注册表"""

    def __init__(self, validators: list[BaseValidator] | None = None) -> None:
        self._validators = validators or list(_DEFAULT_VALIDATORS)

    def add(self, validator: BaseValidator) -> None:
        self._validators.append(validator)

    def run_all(self, context: ValidationContext) -> ValidationReport:
        """运行所有验证器"""
        report = ValidationReport()

        for validator in self._validators:
            try:
                output = validator.validate(context)
                report.outputs.append(output)
            except Exception as e:
                logger.warning(f"[Validator] {validator.name} error: {e}")
                report.outputs.append(
                    ValidatorOutput(
                        name=validator.name,
                        result=ValidationResult.SKIP,
                        reason=f"Validator error: {e}",
                    )
                )

        # Decision Trace
        try:
            from ..tracing.tracer import get_tracer

            tracer = get_tracer()
            tracer.record_decision(
                decision_type="deterministic_validation",
                reasoning=report.get_summary()[:500],
                outcome="pass" if report.all_passed else "fail",
                passed=report.passed_count,
                applicable=report.applicable_count,
            )
        except Exception:
            pass

        if report.any_failed:
            logger.info(
                f"[Validator] Deterministic validation FAILED: "
                f"{[f.name for f in report.failed_validators]}"
            )
        else:
            logger.debug(
                f"[Validator] Deterministic validation PASSED "
                f"({report.passed_count}/{report.applicable_count})"
            )

        return report


def create_default_registry() -> ValidatorRegistry:
    """创建默认验证器注册表"""
    return ValidatorRegistry()
