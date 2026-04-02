"""
工具延迟加载配置

参考 CC 的 shouldDefer / alwaysLoad 机制，集中管理哪些工具始终加载、
哪些工具延迟加载（仅传 name + description，不传 input_schema）。

延迟加载的工具可以通过 tool_search 按需发现，或在对话历史中出现后
自动提升为完整加载。
"""

# 核心工具 — 始终加载完整 schema（参考 CC 的 alwaysLoad: true）
ALWAYS_LOAD_TOOLS: frozenset[str] = frozenset({
    # 文件系统（最基础的 I/O 操作）
    "run_shell",
    "read_file",
    "write_file",
    "edit_file",
    "list_directory",
    "grep",
    "glob",
    "delete_file",
    # PowerShell（Windows 核心）
    "run_powershell",
    # 用户交互 + 元工具
    "ask_user",
    "get_tool_info",
    "tool_search",
    # 代理委派
    "delegate_to_agent",
    # 任务管理
    "create_todo",
    "update_todo_step",
    "get_todo_status",
    "complete_todo",
})

# 延迟加载的分类 — 这些分类下的所有工具默认 defer
DEFER_CATEGORIES: frozenset[str] = frozenset({
    "Browser",
    "Desktop",
    "Scheduled",
    "IM Channel",
    "Agent Package",
    "Persona",
    "Sticker",
    "Config",
    "Agent Hub",
    "Skill Store",
    "Profile",
    "Plugin",
    "Org Setup",
    "OpenCLI",
    "CLI Anything",
})

# 非延迟分类中需要延迟的个别工具
DEFER_INDIVIDUAL_TOOLS: frozenset[str] = frozenset({
    "edit_notebook",
    "switch_mode",
    "generate_image",
    "enable_thinking",
    "get_session_logs",
    "set_task_timeout",
    "get_workspace_map",
    "read_lints",
    "news_search",
    "semantic_search",
    "spawn_agent",
    "delegate_parallel",
    "create_agent",
    "get_agent_status",
    "list_active_agents",
    "cancel_agent",
    "task_stop",
    "send_agent_message",
    "search_relational_memory",
    "create_plan_file",
    "exit_plan_mode",
    "set_persona_trait",
    "get_persona_traits",
    "reset_persona",
    # Phase 3 新增工具（非核心，按需发现）
    "lsp",
    "sleep",
    "structured_output",
    "enter_worktree",
    "exit_worktree",
})


def is_always_load(tool_name: str) -> bool:
    """判断工具是否始终加载。"""
    return tool_name in ALWAYS_LOAD_TOOLS


def should_defer(tool_name: str, category: str | None = None) -> bool:
    """判断工具是否应该延迟加载。

    规则:
    1. always_load 工具永不延迟
    2. 在 DEFER_CATEGORIES 分类下的工具延迟
    3. 在 DEFER_INDIVIDUAL_TOOLS 中的工具延迟
    4. 其余工具不延迟
    """
    if tool_name in ALWAYS_LOAD_TOOLS:
        return False
    if tool_name in DEFER_INDIVIDUAL_TOOLS:
        return True
    if category and category in DEFER_CATEGORIES:
        return True
    return False


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
