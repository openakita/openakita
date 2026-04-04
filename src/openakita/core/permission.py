"""
Permission system for OpenAkita — code-level tool and path access control.

Ported from OpenCode's PermissionNext architecture:
- Rules: (permission, pattern, action) triples
- evaluate(): last matching rule wins (findLast semantics)
- disabled(): returns tools to remove from LLM tool list
- check_path(): runtime path-level permission check before file writes

The permission system is layered on top of existing tool filtering
(skill filter → sub-agent filter → intent filter → **permission filter**).
"""

import fnmatch
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_FAIL_CLOSED_TOOL_PREFIXES = (
    "run_",
    "delete_",
    "edit_",
    "write_",
    "rename_",
    "delegate_",
    "spawn_",
    "create_agent",
    "call_mcp_",
    "browser_",
    "desktop_",
)

# Tools whose permission maps to the "edit" permission category
EDIT_TOOLS = frozenset({
    "write_file", "edit_file", "replace_in_file",
    "create_file", "delete_file", "rename_file",
})

READ_TOOLS = frozenset({
    "read_file", "list_directory", "search_files",
    "web_search", "news_search",
})


@dataclass(frozen=True)
class PermissionRule:
    """A single permission rule.

    Attributes:
        permission: The permission category this rule applies to.
                    Can be a tool name, a category ("edit", "read", "bash"),
                    or "*" for all permissions.
        pattern:    A glob pattern for path matching, or "*" for all paths.
        action:     One of "allow", "deny", "ask".
    """
    permission: str
    pattern: str
    action: str  # "allow" | "deny" | "ask"

    def __post_init__(self):
        if self.action not in ("allow", "deny", "ask"):
            raise ValueError(f"Invalid action: {self.action!r}")


Ruleset = list[PermissionRule]


class DeniedError(Exception):
    """Raised when a tool call is denied by the permission system."""

    def __init__(self, permission: str, pattern: str, rules: Ruleset | None = None):
        self.permission = permission
        self.pattern = pattern
        self.rules = rules or []
        relevant = [r for r in self.rules if _wildcard_match(permission, r.permission)]
        msg = (
            f"Permission denied: {permission} on {pattern!r}. "
            f"Relevant rules: {[{'perm': r.permission, 'pattern': r.pattern, 'action': r.action} for r in relevant]}"
        )
        super().__init__(msg)


def _wildcard_match(text: str, pattern: str) -> bool:
    """Match text against a wildcard pattern (fnmatch-style)."""
    if pattern == "*":
        return True
    return fnmatch.fnmatch(text, pattern)


def evaluate(permission: str, pattern: str, *rulesets: Ruleset) -> PermissionRule:
    """Evaluate permission rules — last matching rule wins (findLast semantics).

    Args:
        permission: The permission being checked (e.g. "edit", "read", tool name).
        pattern:    The path or resource pattern being accessed.
        rulesets:   One or more rulesets to evaluate against.

    Returns:
        The last matching rule, or a default "ask" rule if nothing matches.
    """
    all_rules = [rule for rs in rulesets for rule in rs]

    match = None
    for rule in all_rules:
        if _wildcard_match(permission, rule.permission) and _wildcard_match(pattern, rule.pattern):
            match = rule

    if match is None:
        return PermissionRule(permission=permission, pattern="*", action="ask")
    return match


def disabled(tool_names: list[str], ruleset: Ruleset) -> set[str]:
    """Return tools that should be removed from the LLM tool list.

    Mirrors OpenCode's disabled() — findLast semantics:
    1. Map tool to permission category (edit tools -> "edit", others -> tool name)
    2. Find the LAST rule matching that permission
    3. If that rule has pattern="*" and action="deny", disable the tool

    Exception: if there are MORE SPECIFIC allow rules (non-"*" pattern)
    for the same permission, the tool stays visible (path-restricted at runtime).
    """
    result: set[str] = set()
    for tool in tool_names:
        permission = _tool_to_permission(tool)

        last_matching: PermissionRule | None = None
        has_specific_allow = False
        for rule in ruleset:
            if _wildcard_match(permission, rule.permission):
                last_matching = rule
                if rule.pattern != "*" and rule.action == "allow":
                    has_specific_allow = True

        if last_matching is None:
            continue
        if last_matching.pattern == "*" and last_matching.action == "deny":
            if not has_specific_allow:
                result.add(tool)
        elif has_specific_allow and any(
            r.pattern == "*" and r.action == "deny"
            for r in ruleset
            if _wildcard_match(permission, r.permission)
        ):
            pass  # keep visible — path-restricted at runtime

    return result


def check_path(permission: str, path: str, ruleset: Ruleset) -> PermissionRule:
    """Check if a specific path is allowed for a permission.

    Used at runtime before file operations to enforce path-level restrictions.

    Returns:
        The matching rule. Caller should check rule.action.
    """
    rule = evaluate(permission, path, ruleset)
    if rule.action == "deny":
        logger.info(f"[Permission] DENIED: {permission} on {path!r}")
    elif rule.action == "allow":
        logger.debug(f"[Permission] ALLOWED: {permission} on {path!r}")
    return rule


def _tool_to_permission(tool_name: str) -> str:
    """Map a tool name to its permission category."""
    if tool_name in EDIT_TOOLS:
        return "edit"
    if tool_name in READ_TOOLS:
        return "read"
    return tool_name


def from_config(config: dict[str, str | dict[str, str]]) -> Ruleset:
    """Build a Ruleset from a config dictionary (OpenCode-compatible format).

    Config format:
        {
            "edit": {"*": "deny", "data/plans/*.md": "allow"},
            "read": "allow",
            "question": "allow",
        }
    """
    ruleset: Ruleset = []
    for key, value in config.items():
        if isinstance(value, str):
            ruleset.append(PermissionRule(permission=key, pattern="*", action=value))
        elif isinstance(value, dict):
            for pattern, action in value.items():
                ruleset.append(PermissionRule(permission=key, pattern=pattern, action=action))
    return ruleset


def merge(*rulesets: Ruleset) -> Ruleset:
    """Merge multiple rulesets into one (order preserved = precedence)."""
    return [rule for rs in rulesets for rule in rs]


# check_tool_permission() 和 _is_dangerous_command() 已废弃并移除（P1-4）。
# 内容级安全检查现由 PolicyEngine._check_shell_command() 统一处理。


# ==================== P2: 统一权限决策 ====================

_MODE_LABELS = {"plan": "计划", "ask": "问答", "agent": "执行", "coordinator": "协调"}


@dataclass
class PermissionDecision:
    """统一权限检查结果（P2）。

    behavior: "allow" / "deny" / "confirm"
    reason: 用户可见的中文原因说明
    reason_detail: 技术细节（仅日志）
    policy_name: 命中的策略名称
    decision_chain: 决策经过（审计用）
    """
    behavior: str
    reason: str = ""
    reason_detail: str = ""
    policy_name: str = ""
    metadata: dict = field(default_factory=dict)
    decision_chain: list = field(default_factory=list)


def _should_fail_closed(tool_name: str) -> bool:
    if tool_name in EDIT_TOOLS:
        return True
    return tool_name.startswith(_FAIL_CLOSED_TOOL_PREFIXES)


def check_permission(
    tool_name: str,
    tool_input: dict,
    mode: str = "agent",
    extra_rules: Ruleset | None = None,
) -> PermissionDecision:
    """统一权限检查入口 — 先检查模式规则，再查询 PolicyEngine。

    Args:
        tool_name: 工具名称
        tool_input: 工具参数
        mode: 当前模式（plan / ask / agent）
        extra_rules: 额外规则集（如 AgentProfile.permission_rules），
                     在 mode rules 之后、PolicyEngine 之前评估。

    Returns:
        PermissionDecision: 权限检查结果
    """
    chain: list[dict] = []

    # Step 1: 模式规则
    mode_decision = check_mode_permission(tool_name, tool_input, mode=mode)
    if mode_decision is not None:
        chain.extend(mode_decision.decision_chain)
        if mode_decision.behavior == "deny":
            return mode_decision

    # Step 1b: 额外规则（如 AgentProfile.permission_rules）
    if extra_rules:
        permission = _tool_to_permission(tool_name)
        pattern = "*"
        if tool_name in EDIT_TOOLS:
            file_path = tool_input.get("path", tool_input.get("file_path", ""))
            pattern = str(file_path) if file_path else "*"
        rule = evaluate(permission, pattern, extra_rules)
        chain.append({"layer": "extra_rules", "action": rule.action})
        if rule.action == "deny":
            return PermissionDecision(
                behavior="deny",
                reason=f"智能体配置规则禁止使用工具 {tool_name}。",
                reason_detail=f"extra_rule={rule}",
                policy_name="AgentProfileRules",
                decision_chain=chain,
            )

    # Step 2: PolicyEngine（仅 agent 模式 / mode 规则放行后）
    try:
        from .policy import get_policy_engine
        pe = get_policy_engine()
        pr = pe.assert_tool_allowed(tool_name, tool_input)
        chain.append({
            "layer": "policy_engine",
            "decision": pr.decision.value,
            "policy": pr.policy_name,
        })
        return PermissionDecision(
            behavior=pr.decision.value,
            reason=pr.reason,
            reason_detail=f"policy={pr.policy_name}",
            policy_name=pr.policy_name,
            metadata=pr.metadata,
            decision_chain=chain,
        )
    except Exception as e:
        chain.append({"layer": "policy_engine", "error": str(e)})
        if _should_fail_closed(tool_name):
            logger.error(f"[Permission] PolicyEngine unavailable, fail-closed for {tool_name}: {e}")
            return PermissionDecision(
                behavior="deny",
                reason="安全策略暂时不可用，已阻止高风险操作，请稍后重试。",
                reason_detail=f"PolicyEngine not available for risky tool: {e}",
                policy_name="PolicyEngineUnavailable",
                decision_chain=chain,
            )
        logger.warning(f"[Permission] PolicyEngine unavailable, fail-open for safe read path: {e}")
        return PermissionDecision(
            behavior="allow",
            reason="",
            reason_detail=f"PolicyEngine not available: {e}",
            decision_chain=chain,
        )


def check_mode_permission(
    tool_name: str,
    tool_input: dict,
    mode: str = "agent",
) -> PermissionDecision | None:
    """Only evaluate plan/ask/coordinator mode restrictions, without PolicyEngine."""
    if mode not in ("plan", "ask", "coordinator"):
        return None

    ruleset = (
        PLAN_MODE_RULESET if mode == "plan"
        else COORDINATOR_MODE_RULESET if mode == "coordinator"
        else ASK_MODE_RULESET
    )
    permission = _tool_to_permission(tool_name)

    if tool_name in EDIT_TOOLS:
        file_path = tool_input.get("path", tool_input.get("file_path", ""))
        pattern = str(file_path) if file_path else "*"
    else:
        pattern = "*"

    rule = evaluate(permission, pattern, ruleset)
    chain = [{"layer": "mode_ruleset", "mode": mode, "action": rule.action}]
    if rule.action != "deny":
        return PermissionDecision(
            behavior="allow",
            policy_name="ModeRuleset",
            decision_chain=chain,
        )

    mode_label = _MODE_LABELS.get(mode, mode)
    if tool_name in EDIT_TOOLS and mode == "plan":
        reason = f"当前处于{mode_label}模式，只能编辑 data/plans/ 下的计划文件。如需执行其他操作，请建议用户切换到执行模式。"
    elif mode == "ask":
        reason = f"当前处于{mode_label}模式，只能查看和搜索，不能修改文件或执行命令。"
    else:
        reason = f"工具 {tool_name} 在当前{mode_label}模式下不可用。"
    return PermissionDecision(
        behavior="deny",
        reason=reason,
        reason_detail=f"mode={mode}, rule={rule}",
        policy_name="ModeRuleset",
        decision_chain=chain,
    )


# ==================== Preset Rulesets ====================

DEFAULT_RULESET: Ruleset = from_config({
    "*": "allow",
})

PLAN_MODE_RULESET: Ruleset = from_config({
    "*": "deny",
    "read": "allow",
    "edit": {"*": "deny", "data/plans/*.md": "allow"},
    "run_shell": "deny",
    "create_plan_file": "allow",
    "exit_plan_mode": "allow",
    "get_todo_status": "allow",
    "ask_user": "allow",
    "web_search": "allow",
    "news_search": "allow",
    "search_memory": "allow",
    "get_tool_info": "allow",
    "get_skill_info": "allow",
    "list_skills": "allow",
    "list_mcp_servers": "allow",
    "get_mcp_instructions": "allow",
    "get_workspace_map": "allow",
    "get_session_logs": "allow",
    "browser_screenshot": "allow",
    "view_image": "allow",
    "list_scheduled_tasks": "allow",
    "get_user_profile": "allow",
    "get_persona_profile": "allow",
    "read_file": "allow",
    "list_directory": "allow",
    "grep": "allow",
    "glob": "allow",
})

ASK_MODE_RULESET: Ruleset = from_config({
    "*": "deny",
    "read": "allow",
    "edit": "deny",
    "run_shell": "deny",
    "ask_user": "allow",
    "web_search": "allow",
    "news_search": "allow",
    "search_memory": "allow",
    "add_memory": "allow",
    "get_memory_stats": "allow",
    "list_recent_tasks": "allow",
    "trace_memory": "allow",
    "search_conversation_traces": "allow",
    "get_tool_info": "allow",
    "get_skill_info": "allow",
    "list_skills": "allow",
    "list_mcp_servers": "allow",
    "get_mcp_instructions": "allow",
    "get_todo_status": "allow",
    "get_workspace_map": "allow",
    "get_session_logs": "allow",
    "browser_screenshot": "allow",
    "view_image": "allow",
    "list_scheduled_tasks": "allow",
    "get_user_profile": "allow",
    "get_persona_profile": "allow",
    "read_file": "allow",
    "list_directory": "allow",
    "grep": "allow",
    "glob": "allow",
})

COORDINATOR_MODE_RULESET: Ruleset = from_config({
    "*": "deny",
    "delegate_to_agent": "allow",
    "delegate_parallel": "allow",
    "spawn_agent": "allow",
    "create_agent": "allow",
    "task_stop": "allow",
    "send_agent_message": "allow",
    "create_todo": "allow",
    "update_todo_step": "allow",
    "get_todo_status": "allow",
    "complete_todo": "allow",
    "create_plan_file": "allow",
    "exit_plan_mode": "allow",
    "web_search": "allow",
    "news_search": "allow",
    "search_memory": "allow",
    "add_memory": "allow",
    "get_chat_history": "allow",
    "list_skills": "allow",
    "get_skill_info": "allow",
    "get_tool_info": "allow",
    "ask_user": "allow",
    "read": "allow",
    "read_file": "allow",
    "list_directory": "allow",
    "grep": "allow",
    "glob": "allow",
    "get_workspace_map": "allow",
    "list_mcp_servers": "allow",
    "get_mcp_instructions": "allow",
    "get_session_logs": "allow",
    "get_user_profile": "allow",
    "get_persona_profile": "allow",
})
