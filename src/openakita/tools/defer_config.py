"""
Tool lazy-loading configuration

Modeled after CC's shouldDefer / alwaysLoad mechanism, centrally managing which
tools are always loaded and which are deferred (only name + description sent,
no input_schema).

Deferred tools can be discovered on demand via tool_search, or automatically
promoted to full loading once they appear in conversation history.
"""

# Core tools — always load with full schema (modeled after CC's alwaysLoad: true)
ALWAYS_LOAD_TOOLS: frozenset[str] = frozenset(
    {
        # File system (most fundamental I/O operations)
        "run_shell",
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "grep",
        "glob",
        "delete_file",
        # PowerShell (Windows core)
        "run_powershell",
        # User interaction + meta-tools
        "ask_user",
        "get_tool_info",
        "tool_search",
        # Agent delegation
        "delegate_to_agent",
        "delegate_parallel",
        # MCP entry points (MCP Catalog in the prompt guides users to call these; must stay loaded)
        "call_mcp_tool",
        "list_mcp_servers",
        # Task management
        "create_todo",
        "update_todo_step",
        "get_todo_status",
        "complete_todo",
        # Task timeout (LLM needs reliable schema during long-running tasks)
        "set_task_timeout",
    }
)

# Deferred categories — all tools under these categories are deferred by default
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
        "Profile",
        "Plugin",
        "Org Setup",
        "OpenCLI",
        "CLI Anything",
    }
)

# Individual tools to defer even when their category is not in DEFER_CATEGORIES
DEFER_INDIVIDUAL_TOOLS: frozenset[str] = frozenset(
    {
        "edit_notebook",
        "switch_mode",
        "enable_thinking",
        "get_session_logs",
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
        # Phase 3 additions (non-core, discovered on demand)
        "lsp",
        "sleep",
        "structured_output",
        "enter_worktree",
        "exit_worktree",
        # Low-frequency management tools: skill management & image generation (discovered via tool_search)
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
    """Check whether a tool should always be loaded."""
    return tool_name in ALWAYS_LOAD_TOOLS


def should_defer(
    tool_name: str,
    category: str | None = None,
    *,
    user_always_load: frozenset[str] | None = None,
    user_always_load_cats: frozenset[str] | None = None,
) -> bool:
    """Determine whether a tool should be deferred.

    Rules (by priority):
    1. Tools in ALWAYS_LOAD_TOOLS are never deferred
    2. User-configured always_load_tools / always_load_categories grant exemption
    3. Tools in DEFER_INDIVIDUAL_TOOLS are deferred
    4. Tools whose category is in DEFER_CATEGORIES are deferred
    5. All other tools are not deferred
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
    """Build a search hint string for a tool (used for tool_search matching)."""
    parts = [
        tool.get("name", ""),
        tool.get("description", ""),
        tool.get("category", ""),
    ]
    triggers = tool.get("triggers", [])
    if triggers:
        parts.extend(triggers[:3])
    return " ".join(p for p in parts if p).lower()
