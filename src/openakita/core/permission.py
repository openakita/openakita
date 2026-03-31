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
from dataclasses import dataclass

logger = logging.getLogger(__name__)

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


# ==================== Content-Level Permission Check ====================

def check_tool_permission(
    tool_name: str,
    tool_input: dict,
    *rulesets: Ruleset,
) -> str:
    """检查工具调用权限（支持内容级匹配）。

    参考 Claude Code 的 hasPermissionsToUseToolInner 设计:
    1. deny 规则 → 拒绝
    2. ask 规则 → 需要确认
    3. tool.checkPermissions() → 工具自检
    4. allow 规则 → 放行
    5. 默认 → ask

    Args:
        tool_name: 工具名称
        tool_input: 工具输入参数
        rulesets: 权限规则集

    Returns:
        "allow" | "deny" | "ask"
    """
    permission = _tool_to_permission(tool_name)

    # Extract path from tool input for path-level matching
    path = tool_input.get("path", "") or tool_input.get("file_path", "")
    command = tool_input.get("command", "")

    # Check with path if available
    pattern = path or command or "*"
    rule = evaluate(permission, pattern, *rulesets)

    # Content-level checks for dangerous patterns
    if tool_name == "run_shell" and command:
        if _is_dangerous_command(command):
            deny_rules = [r for r in merge(*rulesets) if r.action == "deny" and r.permission in ("*", "run_shell")]
            if not deny_rules:
                return "ask"
            return "deny"

    return rule.action


def _is_dangerous_command(command: str) -> bool:
    """检查命令是否包含危险模式。"""
    dangerous_patterns = [
        "rm -rf /",
        "chmod 777",
        "curl | bash",
        "wget | bash",
        "eval $(curl",
        "> /dev/sd",
        "mkfs.",
        "dd if=",
    ]
    cmd_lower = command.lower()
    return any(p in cmd_lower for p in dangerous_patterns)


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
