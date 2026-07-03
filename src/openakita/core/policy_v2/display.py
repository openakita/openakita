"""Backend-owned UI metadata for policy confirmation surfaces."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .enums import ApprovalClass, ConfirmationMode, DecisionAction, DecisionSource, SessionRole

if TYPE_CHECKING:
    from .models import DecisionStep


def _token(value: str, label: str, *, color: str = "") -> dict[str, str]:
    data = {"value": value, "label": label}
    if color:
        data["color"] = color
    return data


_RISK_TOKENS = {
    "critical": _token("critical", "极高风险", color="#ef4444"),
    "high": _token("high", "高风险", color="#f59e0b"),
    "medium": _token("medium", "中风险", color="#3b82f6"),
    "low": _token("low", "低风险", color="#10b981"),
}

_APPROVAL_CLASS_TOKENS = {
    ApprovalClass.READONLY_SCOPED.value: _token("readonly_scoped", "局部只读", color="#10b981"),
    ApprovalClass.READONLY_GLOBAL.value: _token("readonly_global", "全局只读", color="#22c55e"),
    ApprovalClass.READONLY_SEARCH.value: _token("readonly_search", "搜索", color="#06b6d4"),
    ApprovalClass.MUTATING_SCOPED.value: _token("mutating_scoped", "局部副作用", color="#f59e0b"),
    ApprovalClass.MUTATING_GLOBAL.value: _token("mutating_global", "全局副作用", color="#ea580c"),
    ApprovalClass.DESTRUCTIVE.value: _token("destructive", "破坏性操作", color="#dc2626"),
    ApprovalClass.EXEC_LOW_RISK.value: _token("exec_low_risk", "低危执行", color="#3b82f6"),
    ApprovalClass.EXEC_CAPABLE.value: _token("exec_capable", "高权执行", color="#dc2626"),
    ApprovalClass.CONTROL_PLANE.value: _token("control_plane", "控制面", color="#9333ea"),
    ApprovalClass.INTERACTIVE.value: _token("interactive", "交互式", color="#3b82f6"),
    ApprovalClass.NETWORK_OUT.value: _token("network_out", "网络出站", color="#0891b2"),
    ApprovalClass.UNKNOWN.value: _token("unknown", "未分类", color="#6b7280"),
}

_ACTION_TOKENS = {
    DecisionAction.ALLOW.value: _token("allow", "允许", color="#10b981"),
    DecisionAction.CONFIRM.value: _token("confirm", "确认", color="#f59e0b"),
    DecisionAction.DENY.value: _token("deny", "拒绝", color="#ef4444"),
    DecisionAction.DEFER.value: _token("defer", "延期", color="#9333ea"),
}

_SOURCE_TOKENS = {
    DecisionSource.EXPLICIT_REGISTER_PARAM.value: _token("explicit_register_param", "显式注册参数"),
    DecisionSource.EXPLICIT_HANDLER_ATTR.value: _token("explicit_handler_attr", "工具处理器声明"),
    DecisionSource.SKILL_METADATA.value: _token("skill_metadata", "Skill 元数据"),
    DecisionSource.MCP_ANNOTATION.value: _token("mcp_annotation", "MCP 注解"),
    DecisionSource.PLUGIN_PREFIX.value: _token("plugin_prefix", "插件声明"),
    DecisionSource.HEURISTIC_PREFIX.value: _token("heuristic_prefix", "工具名前缀推断"),
    DecisionSource.FALLBACK_UNKNOWN.value: _token("fallback_unknown", "兜底未知"),
}

_ROLE_TOKENS = {
    SessionRole.PLAN.value: _token("plan", "计划模式"),
    SessionRole.ASK.value: _token("ask", "问答模式"),
    SessionRole.AGENT.value: _token("agent", "Agent 模式"),
    SessionRole.COORDINATOR.value: _token("coordinator", "协作编排模式"),
}

_MODE_TOKENS = {
    ConfirmationMode.TRUST.value: _token("trust", "信任确认"),
    ConfirmationMode.DEFAULT.value: _token("default", "默认确认"),
    ConfirmationMode.ACCEPT_EDITS.value: _token("accept_edits", "接受编辑"),
    ConfirmationMode.STRICT.value: _token("strict", "严格确认"),
    ConfirmationMode.DONT_ASK.value: _token("dont_ask", "不询问"),
}

_STEP_LABELS = {
    "engine_crash": "引擎异常",
    "preflight": "预检",
    "classify": "分类",
    "security_profile_off": "安全方案",
    "safety_immune": "永不放行检查",
    "owner_only": "Owner 唯一性",
    "channel_compat": "信道兼容性",
    "matrix": "矩阵决策",
    "matrix_deny": "矩阵拒绝",
    "matrix_allow": "矩阵放行",
    "replay": "重放检查",
    "trusted_path": "可信路径",
    "user_allowlist": "用户白名单",
    "death_switch": "只读保护",
    "unattended": "无人值守模式",
    "finalize": "终决",
    "tool_preview": "工具预览",
    "tool_commit_requires_riskgate": "RiskGate 授权",
    "intent_preflight": "意图预检",
    "intent_role_block": "角色意图阻断",
    "intent_trust_bypass": "信任旁路",
    "intent_clean": "意图清扫",
    "intent_risk": "意图风险",
    "approval_override_ignored": "覆盖未应用",
    "approval_override_applied": "覆盖已应用",
    "adapter_fail_closed": "策略适配器",
    "adapter_fail_open_safe": "策略适配器",
    "adapter_msg_fail_soft": "策略适配器",
}

_REASON_LABELS = {
    "matrix says CONFIRM (no relax matched)": "策略矩阵要求确认（未命中重放授权、可信路径或用户白名单）",
    "matrix says ALLOW": "策略矩阵允许执行",
    "matrix says DENY": "策略矩阵拒绝执行",
    "security profile is off": "安全方案已关闭，允许执行",
    "death_switch active": "只读保护已触发，阻止继续执行",
    "tool call only previews candidates": "该工具调用只预览候选项，不会执行提交操作",
    "tool commit requires confirmed RiskGate tool authorization": "工具提交需要已确认的 RiskGate 授权",
}


def risk_token(value: Any) -> dict[str, str]:
    raw = str(value or "").strip().lower()
    return dict(_RISK_TOKENS.get(raw) or _token(raw or "unknown", raw or "未知风险", color="#6b7280"))


def approval_class_token(value: Any) -> dict[str, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return dict(_APPROVAL_CLASS_TOKENS.get(raw) or _token(raw, raw, color="#6b7280"))


def action_token(value: Any) -> dict[str, str]:
    raw = str(getattr(value, "value", value) or "").strip()
    return dict(_ACTION_TOKENS.get(raw) or _token(raw, raw or "unknown", color="#6b7280"))


def source_token(value: Any) -> dict[str, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return dict(_SOURCE_TOKENS.get(raw) or _token(raw, raw))


def role_token(value: Any) -> dict[str, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return dict(_ROLE_TOKENS.get(raw) or _token(raw, raw))


def mode_token(value: Any) -> dict[str, str] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return dict(_MODE_TOKENS.get(raw) or _token(raw, raw))


def tool_token(tool_name: str, metadata: dict[str, Any] | None = None) -> dict[str, str]:
    meta = metadata if isinstance(metadata, dict) else {}
    display_meta = meta.get("tool_display")
    if not isinstance(display_meta, dict):
        display_meta = {}
    label = str(display_meta.get("label") or tool_name or "").strip()
    data = _token(str(tool_name or ""), label or str(tool_name or ""))
    description = str(display_meta.get("description") or "").strip()
    if description:
        data["description"] = description
    return data


def reason_text(reason: Any) -> str:
    raw = str(reason or "").strip()
    if not raw:
        return ""
    if raw in _REASON_LABELS:
        return _REASON_LABELS[raw]
    if raw.startswith("safety_immune match:"):
        return f"命中绝对保护规则：{raw.removeprefix('safety_immune match:').strip()}"
    if raw.startswith("engine_crash:"):
        return f"策略引擎异常：{raw.removeprefix('engine_crash:').strip()}"
    if raw.startswith("replay authorization:"):
        return f"命中重放授权：{raw.removeprefix('replay authorization:').strip()}"
    if raw.startswith("trusted_path override:"):
        return f"命中可信路径授权：{raw.removeprefix('trusted_path override:').strip()}"
    if raw.startswith("user_allowlist:"):
        return f"命中用户白名单：{raw.removeprefix('user_allowlist:').strip()}"
    return raw


def arguments_text(args: dict[str, Any] | None) -> str:
    if not args:
        return "{}"
    try:
        return json.dumps(args, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(args)


def decision_step_display(step: DecisionStep) -> dict[str, Any]:
    meta = step.metadata if isinstance(step.metadata, dict) else {}
    note = _decision_step_note(meta, step.note)
    return {
        "label": str(meta.get("label") or _STEP_LABELS.get(step.name) or step.name),
        "action": action_token(step.action),
        "note": note,
    }


def _decision_step_note(meta: dict[str, Any], raw_note: Any) -> str:
    tool_name = str(meta.get("tool") or "").strip()
    if tool_name:
        return f"工具：{tool_token(tool_name, meta).get('label')}"

    approval_class = approval_class_token(meta.get("approval_class"))
    source = source_token(meta.get("source"))
    if approval_class and source:
        return f"工具分类：{approval_class['label']}；来源：{source['label']}"
    if approval_class:
        return f"工具分类：{approval_class['label']}"

    role = role_token(meta.get("session_role"))
    mode = mode_token(meta.get("confirmation_mode"))
    if role and mode:
        return f"会话角色：{role['label']}；确认模式：{mode['label']}"

    strategy = str(meta.get("strategy") or "").strip()
    if strategy:
        return f"无人值守策略：{strategy}"

    reason = meta.get("reason")
    if reason:
        return reason_text(reason)

    return reason_text(raw_note)


def security_confirm_display(
    *,
    source: str,
    tool_name: str,
    args: dict[str, Any] | None,
    reason: str,
    risk_level: str,
    approval_class: str | None,
    channel: str,
    policy_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    approval = approval_class_token(approval_class)
    display = {
        "title": "RiskGate 安全确认" if source == "risk_gate" else "安全确认",
        "reason": {"text": reason_text(reason), "raw": str(reason or "")},
        "risk": risk_token(risk_level),
        "tool": tool_token(tool_name, policy_metadata),
        "channel": _token(channel, "IM 渠道" if channel == "im" else channel or "desktop"),
        "arguments": {"text": arguments_text(args), "format": "json"},
    }
    if approval is not None:
        display["approval_class"] = approval
    return display
