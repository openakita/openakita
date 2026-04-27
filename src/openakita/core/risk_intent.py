"""Structured risk intent classification for user requests.

The classifier is intentionally deterministic and conservative.  It is the
single pre-ReAct gate for deciding whether a user message needs an explicit
confirmation before any free-form tools can run.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OperationKind(str, Enum):
    NONE = "none"
    READ = "read"
    EXPLAIN = "explain"
    INSPECT = "inspect"
    SUGGEST = "suggest"
    WRITE = "write"
    DELETE = "delete"
    RESET = "reset"
    DISABLE = "disable"
    OVERWRITE = "overwrite"
    EXECUTE = "execute"


class TargetKind(str, Enum):
    UNKNOWN = "unknown"
    SECURITY_USER_ALLOWLIST = "security_user_allowlist"
    SKILL_EXTERNAL_ALLOWLIST = "skill_external_allowlist"
    IM_ALLOWLIST = "im_allowlist"
    DEATH_SWITCH = "death_switch"
    SECURITY_POLICY = "security_policy"
    PROTECTED_FILE = "protected_file"
    SHELL_COMMAND = "shell_command"


class AccessMode(str, Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    EXECUTE = "execute"


_READ_ONLY_RE = re.compile(
    r"(解释|说明|介绍|区别|查看|只查看|列出|查询|展示|分析|建议|如何|怎么|"
    r"explain|describe|show|list|view|inspect|read|query|suggest|compare)",
    re.IGNORECASE,
)
_WRITE_RE = re.compile(
    r"(删除|删掉|移除|清空|重置|覆盖|写入|修改|添加|禁用|关闭|卸载|销毁|"
    r"delete|remove|clear|reset|overwrite|write|modify|add|disable|destroy|drop|truncate)",
    re.IGNORECASE,
)
_EXECUTE_RE = re.compile(
    r"(执行|运行|kill|rm\s+-rf|remove-item|del\s+/s|rmdir|force\s+push|push\s+--force)",
    re.IGNORECASE,
)
_INDEX_RE = re.compile(r"(?:第\s*)?(\d+)\s*(?:条|项|个|index)?", re.IGNORECASE)
_ARITHMETIC_OR_COUNT_RE = re.compile(
    r"(\d+\s*[+\-*/×÷]\s*\d+|calculate|calculation|count|revised count|sum|times|"
    r"算一下|计算|合计|数量|总数|等于多少)",
    re.IGNORECASE,
)
_NON_ACTION_DISCUSSION_RE = re.compile(
    r"(suppose|hypothetical|what should you do|what would you do|if i say|"
    r"假设|如果我说|只是讨论|不需要执行|不要执行|如何处理|应该怎么)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RiskIntentResult:
    risk_level: RiskLevel = RiskLevel.NONE
    operation_kind: OperationKind = OperationKind.NONE
    target_kind: TargetKind = TargetKind.UNKNOWN
    access_mode: AccessMode = AccessMode.READ_ONLY
    requires_confirmation: bool = False
    reason: str = ""
    action: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        data["operation_kind"] = self.operation_kind.value
        data["target_kind"] = self.target_kind.value
        data["access_mode"] = self.access_mode.value
        return data


class RiskIntentClassifier:
    """Classify whether a request is read-only or a dangerous write."""

    def classify(self, message: str, intent: Any | None = None) -> RiskIntentResult:
        text = (message or "").strip()
        lowered = text.lower()
        target = self._target_kind(lowered)
        operation = self._operation_kind(text)

        # Read-only access wins over topic keywords.  "解释 allowlist" should
        # never be blocked merely because it mentions a sensitive object.
        if operation in {
            OperationKind.READ,
            OperationKind.EXPLAIN,
            OperationKind.INSPECT,
            OperationKind.SUGGEST,
        }:
            return RiskIntentResult(
                risk_level=RiskLevel.LOW if target != TargetKind.UNKNOWN else RiskLevel.NONE,
                operation_kind=operation,
                target_kind=target,
                access_mode=AccessMode.READ_ONLY,
                requires_confirmation=False,
                reason="read_only_request",
                action=self._read_action(target),
                parameters=self._extract_parameters(text, target),
            )

        if self._is_non_action_discussion(text, intent, target, operation):
            return RiskIntentResult(
                risk_level=RiskLevel.NONE,
                operation_kind=OperationKind.NONE,
                target_kind=target,
                access_mode=AccessMode.READ_ONLY,
                requires_confirmation=False,
                reason="non_action_discussion",
                action=self._read_action(target),
                parameters=self._extract_parameters(text, target),
            )

        destructive_signal = self._intent_destructive_signal(intent)
        if operation == OperationKind.NONE and destructive_signal:
            operation = OperationKind.WRITE

        if operation == OperationKind.EXECUTE:
            return RiskIntentResult(
                risk_level=RiskLevel.HIGH,
                operation_kind=operation,
                target_kind=target if target != TargetKind.UNKNOWN else TargetKind.SHELL_COMMAND,
                access_mode=AccessMode.EXECUTE,
                requires_confirmation=True,
                reason="execute_or_shell_risk",
                action=None,
                parameters=self._extract_parameters(text, target),
            )

        if operation in {
            OperationKind.WRITE,
            OperationKind.DELETE,
            OperationKind.RESET,
            OperationKind.DISABLE,
            OperationKind.OVERWRITE,
        }:
            risk = RiskLevel.HIGH if self._is_sensitive_target(target) else RiskLevel.MEDIUM
            if target == TargetKind.UNKNOWN and not self._intent_high_risk_signal(intent):
                risk = RiskLevel.LOW
            return RiskIntentResult(
                risk_level=risk,
                operation_kind=operation,
                target_kind=target,
                access_mode=AccessMode.WRITE,
                requires_confirmation=risk in {RiskLevel.MEDIUM, RiskLevel.HIGH},
                reason="dangerous_write_request",
                action=self._write_action(operation, target),
                parameters=self._extract_parameters(text, target),
            )

        return RiskIntentResult(
            risk_level=RiskLevel.LOW if target != TargetKind.UNKNOWN else RiskLevel.NONE,
            operation_kind=OperationKind.NONE,
            target_kind=target,
            access_mode=AccessMode.READ_ONLY,
            requires_confirmation=False,
            reason="no_write_intent",
            action=self._read_action(target),
            parameters=self._extract_parameters(text, target),
        )

    @staticmethod
    def _intent_destructive_signal(intent: Any | None) -> bool:
        complexity = getattr(intent, "complexity", None)
        return bool(getattr(complexity, "destructive_potential", False))

    @staticmethod
    def _intent_high_risk_signal(intent: Any | None) -> bool:
        hint = str(getattr(intent, "risk_level_hint", "") or "").lower()
        if hint in {"risklevelhint.high", "high", "medium", "risklevelhint.medium"}:
            return True
        complexity = getattr(intent, "complexity", None)
        return bool(getattr(complexity, "destructive_potential", False))

    @classmethod
    def _is_non_action_discussion(
        cls,
        text: str,
        intent: Any | None,
        target: TargetKind,
        operation: OperationKind,
    ) -> bool:
        if operation == OperationKind.EXECUTE or cls._is_sensitive_target(target):
            return False

        requires_tools = getattr(intent, "requires_tools", None)
        risk_hint = str(getattr(intent, "risk_level_hint", "") or "").lower()
        if requires_tools is False and risk_hint in {"", "none", "low", "risklevelhint.none", "risklevelhint.low"}:
            return True

        if _ARITHMETIC_OR_COUNT_RE.search(text):
            return True

        if _NON_ACTION_DISCUSSION_RE.search(text):
            return True

        return False

    @staticmethod
    def _operation_kind(text: str) -> OperationKind:
        lowered = text.lower()
        if _READ_ONLY_RE.search(text) and not _WRITE_RE.search(text) and not _EXECUTE_RE.search(text):
            if re.search(r"(解释|说明|介绍|区别|explain|describe|compare)", text, re.IGNORECASE):
                return OperationKind.EXPLAIN
            if re.search(r"(建议|如何|怎么|suggest)", text, re.IGNORECASE):
                return OperationKind.SUGGEST
            return OperationKind.INSPECT
        if _EXECUTE_RE.search(text):
            return OperationKind.EXECUTE
        if re.search(r"(删除|删掉|移除|delete|remove|drop|truncate)", lowered, re.IGNORECASE):
            return OperationKind.DELETE
        if re.search(r"(重置|reset)", lowered, re.IGNORECASE):
            return OperationKind.RESET
        if re.search(r"(禁用|关闭|disable)", lowered, re.IGNORECASE):
            return OperationKind.DISABLE
        if re.search(r"(覆盖|overwrite)", lowered, re.IGNORECASE):
            return OperationKind.OVERWRITE
        if _WRITE_RE.search(text):
            return OperationKind.WRITE
        return OperationKind.NONE

    @staticmethod
    def _target_kind(lowered: str) -> TargetKind:
        if "security user_allowlist" in lowered or "安全白名单" in lowered:
            return TargetKind.SECURITY_USER_ALLOWLIST
        if "user_allowlist" in lowered and "skill" not in lowered:
            return TargetKind.SECURITY_USER_ALLOWLIST
        if "external_allowlist" in lowered or "技能" in lowered and "allowlist" in lowered:
            return TargetKind.SKILL_EXTERNAL_ALLOWLIST
        if "im" in lowered and ("allowlist" in lowered or "白名单" in lowered):
            return TargetKind.IM_ALLOWLIST
        if "death-switch" in lowered or "death_switch" in lowered or "死亡开关" in lowered:
            return TargetKind.DEATH_SWITCH
        if "安全策略" in lowered or "policies" in lowered or "policy" in lowered:
            return TargetKind.SECURITY_POLICY
        if any(s in lowered for s in ("identity/", "data/", ".ssh", "hosts")):
            return TargetKind.PROTECTED_FILE
        if "allowlist" in lowered or "白名单" in lowered:
            return TargetKind.SECURITY_USER_ALLOWLIST
        return TargetKind.UNKNOWN

    @staticmethod
    def _is_sensitive_target(target: TargetKind) -> bool:
        return target in {
            TargetKind.SECURITY_USER_ALLOWLIST,
            TargetKind.DEATH_SWITCH,
            TargetKind.SECURITY_POLICY,
            TargetKind.PROTECTED_FILE,
            TargetKind.SHELL_COMMAND,
        }

    @staticmethod
    def _read_action(target: TargetKind) -> str | None:
        if target == TargetKind.SECURITY_USER_ALLOWLIST:
            return "list_security_allowlist"
        if target == TargetKind.SKILL_EXTERNAL_ALLOWLIST:
            return "list_skill_external_allowlist"
        return None

    @staticmethod
    def _write_action(operation: OperationKind, target: TargetKind) -> str | None:
        if target == TargetKind.SECURITY_USER_ALLOWLIST and operation == OperationKind.DELETE:
            return "remove_security_allowlist_entry"
        if target == TargetKind.DEATH_SWITCH and operation == OperationKind.RESET:
            return "reset_death_switch"
        if target == TargetKind.SKILL_EXTERNAL_ALLOWLIST:
            return "set_skill_external_allowlist"
        return None

    @staticmethod
    def _extract_parameters(text: str, target: TargetKind) -> dict[str, Any]:
        params: dict[str, Any] = {}
        match = _INDEX_RE.search(text)
        if match:
            params["index"] = int(match.group(1))
        if target == TargetKind.SECURITY_USER_ALLOWLIST:
            if re.search(r"(tool|工具)", text, re.IGNORECASE):
                params["entry_type"] = "tool"
            else:
                params["entry_type"] = "command"
        return params


def classify_risk_intent(message: str, intent: Any | None = None) -> RiskIntentResult:
    return RiskIntentClassifier().classify(message, intent)
