"""
Deterministic Validators (Agent Harness)

Mixes deterministic checks with LLM judgment during task-completion validation,
reducing reliance on LLM-based verification.
Deterministic validators do not depend on LLM; they use rules, file checks,
exit codes, and other deterministic methods to verify task outcomes.

Validator types:
- PlanValidator: Validates the status of all Plan steps
- ArtifactValidator: Validates artifact completeness (based on delivery_receipts)
- ToolSuccessValidator: Validates whether critical tools executed successfully
- FileValidator: Validates file operation results (disk existence/size checks)
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
    """Validation result"""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"  # Validator not applicable to the current scenario


@dataclass
class ValidatorOutput:
    """Output from a single validator"""

    name: str
    result: ValidationResult
    reason: str = ""
    confidence: float = 1.0  # Deterministic validator = 1.0


@dataclass
class ValidationReport:
    """Aggregate validation report"""

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
        """Generate a human-readable summary"""
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
    """Base class for validators"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def validate(self, context: ValidationContext) -> ValidatorOutput: ...


@dataclass
class ValidationContext:
    """Validation context (data passed to all validators)"""

    user_request: str = ""
    assistant_response: str = ""
    executed_tools: list[str] = field(default_factory=list)
    delivery_receipts: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    conversation_id: str = ""


class PlanValidator(BaseValidator):
    """Plan step completion validator (deterministic, no LLM)"""

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
    """Artifact completeness validator"""

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
    """Critical tool execution success validator"""

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
    """Validates whether the complete_todo tool was called"""

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
    """File operation result validator (disk-level deterministic checks)

    Extracts file paths from tool_results text and verifies their actual
    state on disk:
    - write_file / edit_file: file should exist and have size > 0
    - delete_file: file should no longer exist
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

            # write / edit: file should exist
            m = self._WRITE_PATH_RE.search(content)
            if m:
                fpath = m.group(1).strip()
                checked += 1
                try:
                    p = Path(fpath)
                    if not p.exists():
                        issues.append(f"write/edit target does not exist: {fpath}")
                    elif p.stat().st_size == 0:
                        issues.append(f"write/edit target is empty file: {fpath}")
                except OSError as e:
                    issues.append(f"Cannot check {fpath}: {e}")
                continue

            # delete: file should no longer exist
            m = self._DELETE_PATH_RE.search(content)
            if m:
                fpath = m.group(1).strip()
                checked += 1
                try:
                    if Path(fpath).exists():
                        issues.append(f"delete target still exists: {fpath}")
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


# ==================== Validator Registry ====================

_DEFAULT_VALIDATORS: list[BaseValidator] = [
    PlanValidator(),
    ArtifactValidator(),
    ToolSuccessValidator(),
    FileValidator(),
    CompletePlanValidator(),
]


class ValidatorRegistry:
    """Validator registry"""

    def __init__(self, validators: list[BaseValidator] | None = None) -> None:
        self._validators = validators or list(_DEFAULT_VALIDATORS)

    def add(self, validator: BaseValidator) -> None:
        self._validators.append(validator)

    def run_all(self, context: ValidationContext) -> ValidationReport:
        """Run all validators"""
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
    """Create a default validator registry"""
    return ValidatorRegistry()
