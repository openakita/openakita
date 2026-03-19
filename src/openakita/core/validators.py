"""
确定性验证器 (Agent Harness: Deterministic Validators)

在任务完成验证中混合使用确定性检查和 LLM 判断，减少对 LLM 验证的依赖。
确定性验证器不依赖 LLM，使用规则、文件检查、退出码等确定性方法验证任务结果。

验证器类型:
- PlanValidator: 验证 Plan 所有步骤状态
- ArtifactValidator: 验证交付物是否完整（基于 delivery_receipts）
- ToolSuccessValidator: 验证关键工具是否执行成功
- FileValidator: 验证文件操作结果
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ValidationResult(str, Enum):
    """验证结果"""
    PASS = "pass"
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
        return all(o.result == ValidationResult.PASS for o in applicable) if applicable else True

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
            icon = "✓" if o.result == ValidationResult.PASS else "✗"
            parts.append(f"{icon} {o.name}: {o.reason}")
        return "\n".join(parts) if parts else "No applicable validators"


class BaseValidator(ABC):
    """验证器基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def validate(self, context: ValidationContext) -> ValidatorOutput:
        ...


@dataclass
class ValidationContext:
    """验证上下文（传递给所有验证器的数据）"""
    user_request: str = ""
    assistant_response: str = ""
    executed_tools: list[str] = field(default_factory=list)
    delivery_receipts: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    conversation_id: str = ""


class PlanValidator(BaseValidator):
    """Plan 步骤完成度验证（确定性，不用 LLM）"""

    @property
    def name(self) -> str:
        return "PlanValidator"

    def validate(self, context: ValidationContext) -> ValidatorOutput:
        try:
            from ..tools.handlers.plan import get_plan_handler_for_session, has_active_todo

            if not context.conversation_id or not has_active_todo(context.conversation_id):
                return ValidatorOutput(
                    name=self.name,
                    result=ValidationResult.SKIP,
                    reason="No active plan",
                )

            handler = get_plan_handler_for_session(context.conversation_id)
            plan = handler.get_plan_for(context.conversation_id) if handler else None
            if not plan:
                return ValidatorOutput(
                    name=self.name,
                    result=ValidationResult.SKIP,
                    reason="Plan not found",
                )

            steps = plan.get("steps", [])
            total = len(steps)
            completed = sum(1 for s in steps if s.get("status") in ("completed", "skipped"))
            pending = sum(1 for s in steps if s.get("status") in ("pending", "in_progress"))

            if pending > 0:
                pending_ids = [s.get("id", "?") for s in steps if s.get("status") in ("pending", "in_progress")]
                return ValidatorOutput(
                    name=self.name,
                    result=ValidationResult.FAIL,
                    reason=f"{pending}/{total} steps pending: {pending_ids[:3]}",
                )

            return ValidatorOutput(
                name=self.name,
                result=ValidationResult.PASS,
                reason=f"All {total} steps completed",
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

        error_indicators = ["❌", "⚠️ 工具执行错误", "错误类型:", "Error:"]
        error_results = []
        for tr in context.tool_results:
            content = str(tr.get("content", "")) if isinstance(tr, dict) else str(tr)
            if any(indicator in content for indicator in error_indicators):
                tool_id = tr.get("tool_use_id", "?") if isinstance(tr, dict) else "?"
                error_results.append(tool_id)

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


# ==================== 验证器注册表 ====================

_DEFAULT_VALIDATORS: list[BaseValidator] = [
    PlanValidator(),
    ArtifactValidator(),
    ToolSuccessValidator(),
    CompletePlanValidator(),
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
                report.outputs.append(ValidatorOutput(
                    name=validator.name,
                    result=ValidationResult.SKIP,
                    reason=f"Validator error: {e}",
                ))

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
