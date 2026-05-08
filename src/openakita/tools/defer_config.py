"""
工具延迟加载配置

参考 CC 的 shouldDefer / alwaysLoad 机制，集中管理哪些工具始终加载、
哪些工具延迟加载（仅传 name + description，不传 input_schema）。

延迟加载的工具可以通过 tool_search 按需发现，或在对话历史中出现后
自动提升为完整加载。
"""

# 核心工具 — 始终加载完整 schema（参考 CC 的 alwaysLoad: true）
ALWAYS_LOAD_TOOLS: frozenset[str] = frozenset(
    {
        # 文件系统（最基础的 I/O 操作）
        "run_shell",
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "grep",
        "glob",
        "move_file",
        "delete_file",
        # PowerShell（Windows 核心）
        "run_powershell",
        # 用户交互 + 元工具
        "ask_user",
        "get_tool_info",
        "tool_search",
        # 代理委派
        "delegate_to_agent",
        "delegate_parallel",
        # MCP 入口（prompt 中 MCP Catalog 引导用户调用，必须常驻）
        "call_mcp_tool",
        "list_mcp_servers",
        # 任务管理
        "create_todo",
        "update_todo_step",
        "get_todo_status",
        "complete_todo",
        # Fix-10：高频调度 / 记忆 / 网络工具提到首轮。
        # 这些工具在专业用户日常会话中调用频率极高（"提醒我..." /
        # "记住..." / "搜一下..."），把它们留在 deferred 会强制 LLM 多走
        # 一轮 get_tool_info → 在 19 轮探索测试里这种额外 round-trip
        # 单独贡献了 ≥6 万 token 的浪费。promote 后约多 800 token system
        # prompt，但消除了反复的 schema 拉取。
        "schedule_task",
        "list_scheduled_tasks",
        "cancel_scheduled_task",
        "search_memory",
        "add_memory",
        "web_search",
        "web_fetch",
        # 用户档案（"我叫 X" / "我是 Y" 的高频更新路径）
        "update_user_profile",
    }
)

# 延迟加载的分类 — 这些分类下的所有工具默认 defer
DEFER_CATEGORIES: frozenset[str] = frozenset(
    {
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
        # "Profile" 已移除：用户档案/记忆是消费者首轮高频路径，
        # 延迟加载导致首轮 update_user_profile 必失败（progressive disclosure
        # 两轮往返），与产品体验相悖。详见 _exploratory_test_report_20260418.md。
        "Plugin",
        "Org Setup",
        "OpenCLI",
        "CLI Anything",
    }
)

# 非延迟分类中需要延迟的个别工具
DEFER_INDIVIDUAL_TOOLS: frozenset[str] = frozenset(
    {
        "edit_notebook",
        "switch_mode",
        "enable_thinking",
        "get_session_logs",
        "set_task_timeout",
        "get_workspace_map",
        "read_lints",
        "news_search",
        "semantic_search",
        "spawn_agent",
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
        # 低频管理工具：技能管理 & 图片生成（按需通过 tool_search 发现）
        "generate_image",
        "install_skill",
        "uninstall_skill",
        "reload_skill",
        "manage_skill_enabled",
        "load_skill",
        "get_skill_reference",
    }
)


def is_always_load(tool_name: str) -> bool:
    """判断工具是否始终加载。"""
    return tool_name in ALWAYS_LOAD_TOOLS


def should_defer(
    tool_name: str,
    category: str | None = None,
    *,
    user_always_load: frozenset[str] | None = None,
    user_always_load_cats: frozenset[str] | None = None,
) -> bool:
    """判断工具是否应该延迟加载。

    规则（按优先级）:
    1. ALWAYS_LOAD_TOOLS 中的工具永不延迟
    2. 用户配置的 always_load_tools / always_load_categories 豁免
    3. 在 DEFER_INDIVIDUAL_TOOLS 中的工具延迟
    4. 在 DEFER_CATEGORIES 分类下的工具延迟
    5. 其余工具不延迟
    """
    if tool_name in ALWAYS_LOAD_TOOLS:
        return False
    if user_always_load and tool_name in user_always_load:
        return False
    if user_always_load_cats and category and category in user_always_load_cats:
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
