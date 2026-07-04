"""Tool-filter guards: mode/intent rulesets and shell-write detection.

Pulled out of legacy ``core/reasoning_engine.py`` (P-RC-5 / P5.8a).
The six symbols below decide which tools the engine is allowed to
show or invoke based on the active mode and intent, plus a shell-
write detector for the ``ask``/``plan`` guard. Permission imports
are deferred to call time to avoid a known init-time cycle.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....core.permission import Ruleset as PermissionRuleset

__all__ = [
    "CHAT_INTENT_CORE_TOOLS",
    "SHELL_WRITE_PATTERNS",
    "filter_tools_by_intent",
    "filter_tools_by_mode",
    "get_mode_ruleset",
    "is_shell_write_command",
    "should_block_tool",
]

logger = logging.getLogger(__name__)

# PR-M1: chat / chit-chat intent trims tools down to a 5-item whitelist
# (think / minimal Q&A / memory / profile / ask_user); anything that
# writes a file, runs a command, or calls an external service is dropped.
CHAT_INTENT_CORE_TOOLS: tuple[str, ...] = (
    "think",
    "ask_user",
    "search_memory",
    "get_user_profile",
    "get_session_context",
)


def get_mode_ruleset(mode: str) -> PermissionRuleset:
    """Get the permission ruleset for the given mode."""
    from ....core.permission import (
        ASK_MODE_RULESET,
        COORDINATOR_MODE_RULESET,
        DEFAULT_RULESET,
        PLAN_MODE_RULESET,
    )
    if mode == "plan":
        return PLAN_MODE_RULESET
    if mode == "ask":
        return ASK_MODE_RULESET
    if mode == "coordinator":
        return COORDINATOR_MODE_RULESET
    return DEFAULT_RULESET


def filter_tools_by_intent(
    tools: list[dict],
    *,
    intent_name: str | None,
    intent_tool_hints: list[str] | None = None,
    requires_tools: bool = False,
) -> list[dict]:
    """Intent-driven secondary trim: chat intent gets a small core list only."""
    try:
        from ....core.feature_flags import is_enabled as _ff_enabled
        if not _ff_enabled("intent_tool_slim_v1"):
            return tools
    except Exception:
        pass
    if requires_tools or not tools:
        return tools
    if (intent_name or "").lower() not in ("chat",):
        return tools
    keep_names = set(CHAT_INTENT_CORE_TOOLS) | set(intent_tool_hints or [])
    filtered: list[dict] = []
    for tool in tools:
        name = tool.get("name", "") or tool.get("function", {}).get("name", "")
        if name in keep_names:
            filtered.append(tool)
    if filtered and len(filtered) < len(tools):
        logger.info(
            f"[ToolFilter/Intent] chat intent slim: {len(tools)} -> {len(filtered)} tools "
            f"(kept: {sorted({t.get('name') or t.get('function', {}).get('name', '') for t in filtered})})"
        )
    if not filtered:
        for tool in tools:
            name = tool.get("name", "") or tool.get("function", {}).get("name", "")
            if name == "ask_user":
                filtered.append(tool)
                break
    return filtered or tools


def filter_tools_by_mode(tools: list[dict], mode: str) -> list[dict]:
    """Filter tool list using the permission ruleset of the active mode."""
    if mode == "agent" or not tools:
        return tools
    from ....core.permission import disabled as permission_disabled
    ruleset = get_mode_ruleset(mode)
    tool_names = [
        (t.get("name", "") or t.get("function", {}).get("name", "")) for t in tools
    ]
    disabled_set = permission_disabled(tool_names, ruleset)
    filtered = [
        tool
        for tool, name in zip(tools, tool_names, strict=False)
        if name not in disabled_set
    ]
    if disabled_set:
        logger.info(
            f"[ToolFilter] mode={mode}: {len(tools)} -> {len(filtered)} tools "
            f"(disabled: {sorted(disabled_set)})"
        )
    return filtered


SHELL_WRITE_PATTERNS = re.compile(
    r"(?:"
    r'>\s*["\'/\w]|>>|\btee\b|\bsed\s+-i|\bdd\b|\brm\s|\bmv\s|\bcp\s'
    r"|\bmkdir\b|\btouch\b|\bchmod\b|\bchown\b"
    r'|open\s*\([^)]*["\']w|\.write\s*\(|echo\s+.*>'
    r"|\bpip\s+install|\bnpm\s+install"
    r"|\bgit\s+(?:commit|push|checkout|merge|rebase|reset)"
    r"|\bOut-File\b|\bSet-Content\b|\bAdd-Content\b|\bNew-Item\b"
    r"|\bRemove-Item\b|\bMove-Item\b|\bCopy-Item\b|\bRename-Item\b"
    r"|\bInvoke-WebRequest\b.*-OutFile"
    r"|\bdel\s|\bcopy\s|\bmove\s|\bren\s|\btype\s.*>"
    r")",
    re.IGNORECASE,
)


def is_shell_write_command(command: str) -> bool:
    """Check if a shell command appears to perform write operations."""
    return bool(SHELL_WRITE_PATTERNS.search(command))


def should_block_tool(
    tool_name: str,
    tool_input: Any,
    allowed_tool_names: set[str] | None,
    mode: str,
) -> str | None:
    """Check if a tool call should be blocked by mode restrictions."""
    if allowed_tool_names is None:
        return None
    if tool_name not in allowed_tool_names:
        return (
            f"\u9519\u8bef\uff1a{tool_name} \u5728\u5f53\u524d {mode} \u6a21\u5f0f\u4e0b\u4e0d\u53ef\u7528\u3002"
            "\u8bf7\u4f7f\u7528\u5df2\u63d0\u4f9b\u7684\u5de5\u5177\u5217\u8868\u4e2d\u7684\u5de5\u5177\uff0c\u6216\u5efa\u8bae\u7528\u6237\u5207\u6362\u5230 agent \u6a21\u5f0f\u3002"
        )
    if tool_name in ("run_shell", "run_powershell"):
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = tool_input.get("command", "")
        elif isinstance(tool_input, str):
            try:
                cmd = json.loads(tool_input).get("command", "")
            except Exception:
                pass
        if cmd and is_shell_write_command(cmd):
            logger.warning(
                f"[ModeGuard] Blocked {tool_name} write command in {mode} mode: {cmd[:100]}"
            )
            return (
                f"\u9519\u8bef\uff1a\u5728 {mode} \u6a21\u5f0f\u4e0b\uff0c{tool_name} \u4ec5\u5141\u8bb8\u6267\u884c\u53ea\u8bfb\u547d\u4ee4\uff08\u5982 cat\u3001grep\u3001ls\u3001find \u7b49\uff09\u3002"
                f"\u68c0\u6d4b\u5230\u5199\u64cd\u4f5c\u547d\u4ee4\uff0c\u5df2\u62e6\u622a\u3002\u8bf7\u4f7f\u7528\u53ea\u8bfb\u547d\u4ee4\uff0c\u6216\u5efa\u8bae\u7528\u6237\u5207\u6362\u5230 agent \u6a21\u5f0f\u3002"
            )
    return None
