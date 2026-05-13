"""Core enums for PolicyEngineV2.

ApprovalClass 是 v2 决策的中心维度——工具的语义+参数细化后归到这 11 类之一
（外加 UNKNOWN 兜底）。SessionRole 与 ConfirmationMode 是正交两层 mode。
DecisionAction 是 PolicyEngine 的输出。DecisionSource 标记 ApprovalClassifier
判定来源，C19 完备性测试用它判断"是否走了启发式回退"。

完整设计见 docs/policy_v2_research.md §4 + plan §3。
"""

from __future__ import annotations

from enum import StrEnum


class ApprovalClass(StrEnum):
    """工具语义+参数细化后的风险分类（11 维 + UNKNOWN 兜底）。"""

    # 只读类
    READONLY_SCOPED = "readonly_scoped"
    READONLY_GLOBAL = "readonly_global"
    READONLY_SEARCH = "readonly_search"

    # 修改类
    MUTATING_SCOPED = "mutating_scoped"
    MUTATING_GLOBAL = "mutating_global"
    DESTRUCTIVE = "destructive"

    # 执行类
    EXEC_LOW_RISK = "exec_low_risk"
    EXEC_CAPABLE = "exec_capable"

    # 控制 / 交互 / 网络
    CONTROL_PLANE = "control_plane"
    INTERACTIVE = "interactive"
    NETWORK_OUT = "network_out"

    # 兜底（未分类，永不静默放行）
    UNKNOWN = "unknown"


class SessionRole(StrEnum):
    """会话角色（plan §3）。与 ConfirmationMode 正交。"""

    PLAN = "plan"
    ASK = "ask"
    AGENT = "agent"
    COORDINATOR = "coordinator"


class ConfirmationMode(StrEnum):
    """确认模式（plan §3）。

    与 v1 兼容映射（context.py:_coerce_mode 实现）：
    - yolo → trust
    - smart → default
    - cautious → strict
    """

    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    TRUST = "trust"
    STRICT = "strict"
    DONT_ASK = "dont_ask"


class DecisionAction(StrEnum):
    """PolicyEngine 决策结果。"""

    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"
    DEFER = "defer"


class DecisionSource(StrEnum):
    """ApprovalClass 判定来源（C19 完备性测试用）。

    显式来源（不会触发 C19 WARN/CI red）：
    - EXPLICIT_REGISTER_PARAM：agent.py register(tool_classes={...}) 注入
    - EXPLICIT_HANDLER_ATTR：handler 类的 TOOL_CLASSES 属性
    - SKILL_METADATA：SKILL.md frontmatter risk_class
    - MCP_ANNOTATION：MCP server tool.annotations
    - PLUGIN_PREFIX：plugin manifest 声明

    回退来源（触发 C19 WARN，缺失会让 CI red）：
    - HEURISTIC_PREFIX：按工具名前缀启发式归类
    - FALLBACK_UNKNOWN：完全无法判断，归到 UNKNOWN
    """

    EXPLICIT_REGISTER_PARAM = "explicit_register_param"
    EXPLICIT_HANDLER_ATTR = "explicit_handler_attr"
    SKILL_METADATA = "skill_metadata"
    MCP_ANNOTATION = "mcp_annotation"
    PLUGIN_PREFIX = "plugin_prefix"
    HEURISTIC_PREFIX = "heuristic_prefix"
    FALLBACK_UNKNOWN = "fallback_unknown"

    @classmethod
    def is_explicit(cls, source: DecisionSource) -> bool:
        """是否属于"开发者主动声明"（非启发式/兜底）。"""
        return source in (
            cls.EXPLICIT_REGISTER_PARAM,
            cls.EXPLICIT_HANDLER_ATTR,
            cls.SKILL_METADATA,
            cls.MCP_ANNOTATION,
            cls.PLUGIN_PREFIX,
        )


_STRICTNESS_ORDER: dict[ApprovalClass, int] = {
    # 互动/只读类（低风险）
    ApprovalClass.INTERACTIVE: 1,
    ApprovalClass.READONLY_SCOPED: 2,
    ApprovalClass.READONLY_SEARCH: 3,
    ApprovalClass.READONLY_GLOBAL: 4,
    # 网络/低危执行
    ApprovalClass.NETWORK_OUT: 5,
    ApprovalClass.EXEC_LOW_RISK: 6,
    # 修改类（渐进）
    ApprovalClass.MUTATING_SCOPED: 7,
    ApprovalClass.MUTATING_GLOBAL: 8,
    # 控制面 / 任意执行
    ApprovalClass.CONTROL_PLANE: 9,
    ApprovalClass.EXEC_CAPABLE: 10,
    # 兜底未知按 safety-by-default 视同高危
    ApprovalClass.UNKNOWN: 11,
    # 不可恢复
    ApprovalClass.DESTRUCTIVE: 12,
}


def strictness(klass: ApprovalClass) -> int:
    """风险严格度排序值（越大越严）。

    用于多源声明叠加时取 strict 大者（safety-by-default）：
    - register(tool_classes=) 与 handler.TOOL_CLASSES 同时声明时取严
    - Skill/MCP/plugin 自报与启发式取严（C15 trust_level=trusted 时直接采信，不走此叠加）
    """
    return _STRICTNESS_ORDER.get(klass, _STRICTNESS_ORDER[ApprovalClass.UNKNOWN])


def most_strict(
    candidates: list[tuple[ApprovalClass, DecisionSource]],
) -> tuple[ApprovalClass, DecisionSource]:
    """从多个 (class, source) 候选中取严格度最大者。
    严格度相同时保留第一个传入的 source（输入顺序代表优先级）。
    """
    if not candidates:
        return ApprovalClass.UNKNOWN, DecisionSource.FALLBACK_UNKNOWN
    best = candidates[0]
    best_score = strictness(best[0])
    for cand in candidates[1:]:
        score = strictness(cand[0])
        if score > best_score:
            best = cand
            best_score = score
    return best


# ---------------------------------------------------------------------------
# v1 → v2 mode aliases (single source of truth)
# ---------------------------------------------------------------------------

LEGACY_MODE_ALIASES: dict[str, str] = {
    "yolo": "trust",
    "smart": "default",
    "cautious": "strict",
}
"""v1 ConfirmationMode 字符串 → v2 ConfirmationMode 字符串。

历史上 ``yolo`` / ``smart`` / ``cautious`` 是 v1 的别名；v2 改名为
``trust`` / ``default`` / ``strict``。本表是**唯一真相源**：
``policy_v2.context._coerce_mode`` 与 ``policy_v2.migration.migrate_v1_to_v2``
都从这里 import，避免双份硬编码漂移。
"""
