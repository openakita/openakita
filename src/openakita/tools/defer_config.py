"""
渐进式工具 schema 配置。

稳定核心直接发送完整 schema，其余工具通过目录和 tool_search 按需发现、
再提升为完整 schema。
"""

# Stable main-chat direct schemas. Keep this deliberately small: every other
# registered tool remains discoverable through the textual catalog and
# ``tool_search`` and can be promoted on a later turn.
STABLE_MAIN_CHAT_CORE_TOOLS: tuple[str, ...] = (
    "run_shell",
    "read_file",
    "write_file",
    "edit_file",
    "list_directory",
    "grep",
    "ask_user",
    "tool_search",
    "get_tool_info",
    "delegate_to_agent",
    "delegate_parallel",
    "search_memory",
    "add_memory",
    "get_skill_info",
)
STABLE_MAIN_CHAT_CORE_TOOL_SET: frozenset[str] = frozenset(STABLE_MAIN_CHAT_CORE_TOOLS)


def build_search_hint(tool: dict) -> str:
    """为工具构建搜索提示文本（用于 tool_search 匹配）。"""
    parts = [
        tool.get("name", ""),
        tool.get("description", ""),
        tool.get("category", ""),
    ]
    triggers = tool.get("triggers", [])
    if triggers:
        parts.extend(triggers[:3])
    return " ".join(p for p in parts if p).lower()
