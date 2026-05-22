"""Tool interrupt-behavior registry (S4, plan: conversation concurrency v1.28).

Single source of truth for "is this tool safe to abort mid-execution".
Drives the INTERRUPT-policy downgrade logic in
``Agent._preempt_or_queue_prev_task``:

* ``"cancel"`` — tool is safe to abort.  Pure reads, idempotent queries,
  fast in-memory ops with no external side effects.  INTERRUPT can really
  ``task.cancel(...)`` while one of these is in flight.
* ``"block"`` — tool started a side effect that mid-cancel would leave
  inconsistent (half-written file, half-clicked browser, half-sent IM,
  subprocess holding fds, DB row half-inserted, sub-agent half-launched).
  INTERRUPT must downgrade to QUEUE — wait for the tool to settle first,
  then proceed.

**Safety-by-default**: any tool not listed here (third-party MCP, future
contributions, dynamically-registered) is treated as ``"block"``.  This
mirrors the conservative default we already have for ``ApprovalClass`` —
the cost of a missing tag is one user-visible "INTERRUPT degraded to
QUEUE" log line; the cost of mis-classifying a write as cancel is a
corrupted user file.

External overrides supported:
* MCP server ``annotations.interruptBehavior`` — when a remote tool
  declares its own behavior explicitly, the caller can pass it to
  :func:`get_tool_interrupt_behavior` and it will override the default.

Tests for completeness live in
``tests/unit/test_tool_interrupt_behavior_completeness.py`` — they walk
``tools/definitions/*.py`` at collection time and fail the build if any
defined tool is missing from this table without a registered exemption.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Literal

logger = logging.getLogger(__name__)

InterruptBehavior = Literal["cancel", "block"]
DEFAULT_BEHAVIOR: Final[InterruptBehavior] = "block"


# ── Registry ─────────────────────────────────────────────────────────
#
# Keep alphabetised within each section to make code review easier;
# adding a new tool with the wrong class is the typical regression we
# want to catch on review, not at runtime.

_INTERRUPT_BEHAVIOR_MAP: dict[str, InterruptBehavior] = {
    # ── Agent / org / delegation ──────────────────────────────────────
    # Delegation IS the side effect — cancelling mid-delegate leaves
    # the sub-agent task orphaned with no parent to report back to.
    # Better to let it finish and report.
    "create_agent": "block",
    "delegate_parallel": "block",
    "delegate_to_agent": "block",
    "send_agent_message": "block",
    "setup_organization": "block",
    "spawn_agent": "block",
    "task_stop": "cancel",  # task_stop is itself an interrupt request

    # ── Agent hub / package ──────────────────────────────────────────
    "batch_export_agents": "block",
    "export_agent": "block",
    "get_hub_agent_detail": "cancel",
    "import_agent": "block",
    "inspect_agent_package": "cancel",
    "install_hub_agent": "block",
    "list_exportable_agents": "cancel",
    "publish_agent": "block",
    "search_hub_agents": "cancel",

    # ── Browser ─────────────────────────────────────────────────────
    # Anything that mutates DOM / navigates / interacts with the page
    # is block — a half-completed click is worse than no click.
    "browser_click": "block",
    "browser_close": "block",
    "browser_execute_js": "block",  # arbitrary JS side effects
    "browser_get_content": "cancel",
    "browser_list_tabs": "cancel",
    "browser_navigate": "block",
    "browser_new_tab": "block",
    "browser_open": "block",
    "browser_screenshot": "cancel",
    "browser_scroll": "block",
    "browser_switch_tab": "block",
    "browser_type": "block",
    "browser_wait": "cancel",  # purely a sleep

    # ── CLI / shell-likes ──────────────────────────────────────────
    # Subprocesses hold fds, file locks, network sockets — interrupt
    # mid-run risks an inconsistent state.
    "cli_anything_discover": "cancel",
    "cli_anything_help": "cancel",
    "cli_anything_run": "block",
    "opencli_doctor": "cancel",
    "opencli_list": "cancel",
    "opencli_run": "block",
    "run_powershell": "block",
    "run_shell": "block",

    # ── Code quality / search ─────────────────────────────────────
    "read_lints": "cancel",
    "semantic_search": "cancel",
    "tool_search": "cancel",

    # ── Config / system ───────────────────────────────────────────
    "ask_user": "cancel",  # already an awaiting-user state; cancel is fine
    "enable_thinking": "cancel",
    "generate_image": "block",  # external API call + file write
    "get_session_context": "cancel",
    "get_session_logs": "cancel",
    "get_tool_info": "cancel",
    "get_workspace_map": "cancel",
    "set_task_timeout": "cancel",
    "system_config": "block",

    # ── Desktop automation ────────────────────────────────────────
    "desktop_click": "block",
    "desktop_find_element": "cancel",
    "desktop_hotkey": "block",
    "desktop_inspect": "cancel",
    "desktop_screenshot": "cancel",
    "desktop_scroll": "block",
    "desktop_type": "block",
    "desktop_wait": "cancel",
    "desktop_window": "block",

    # ── Filesystem ────────────────────────────────────────────────
    # Reads are cancel; any write/mutate is block (half-written file
    # is worse than the user re-running the operation).
    "delete_file": "block",
    "edit_file": "block",
    "glob": "cancel",
    "grep": "cancel",
    "list_directory": "cancel",
    "move_file": "block",
    "read_file": "cancel",
    "write_file": "block",

    # ── IM channel ────────────────────────────────────────────────
    # Send-side ops are block (a partial send is worse than nothing);
    # read-side is cancel.
    "deliver_artifacts": "block",
    "get_chat_history": "cancel",
    "get_chat_info": "cancel",
    "get_chat_members": "cancel",
    "get_image_file": "cancel",
    "get_recent_messages": "cancel",
    "get_user_info": "cancel",
    "get_voice_file": "cancel",
    "send_sticker": "block",

    # ── LSP / advanced ────────────────────────────────────────────
    "edit_notebook": "block",
    "lsp": "cancel",
    "sleep": "cancel",
    "structured_output": "cancel",
    "view_image": "cancel",

    # ── MCP ──────────────────────────────────────────────────────
    # call_mcp_tool is the dispatcher; the actual remote tool's
    # behavior is resolved separately via mcp_annotations.  Server
    # management ops mutate config so block.
    "add_mcp_server": "block",
    "call_mcp_tool": "block",  # safe default; MCP annotations can override per-tool
    "connect_mcp_server": "block",
    "disconnect_mcp_server": "block",
    "get_mcp_instructions": "cancel",
    "list_mcp_servers": "cancel",
    "reload_mcp_servers": "block",
    "remove_mcp_server": "block",

    # ── Memory ──────────────────────────────────────────────────
    # Reads cancel; writes/consolidation block (mid-write rolls back
    # in SQLite but DB-level retry semantics still want clean state).
    "add_memory": "block",
    "consolidate_memories": "block",
    "get_memory_stats": "cancel",
    "list_recent_tasks": "cancel",
    "memory_delete_by_query": "block",
    "search_conversation_traces": "cancel",
    "search_memory": "cancel",
    "search_relational_memory": "cancel",
    "trace_memory": "cancel",

    # ── Mode / persona / profile ────────────────────────────────
    "get_persona_profile": "cancel",
    "get_user_profile": "cancel",
    "skip_profile_question": "cancel",
    "switch_mode": "block",
    "switch_persona": "block",
    "toggle_proactive": "block",
    "update_persona_trait": "block",
    "update_user_profile": "block",

    # ── Plan / todo ─────────────────────────────────────────────
    "complete_todo": "block",
    "create_plan_file": "block",
    "create_todo": "block",
    "exit_plan_mode": "block",
    "get_todo_status": "cancel",
    "update_todo_step": "block",

    # ── Plugins ────────────────────────────────────────────────
    "get_plugin_info": "cancel",
    "list_plugins": "cancel",

    # ── Scheduled ──────────────────────────────────────────────
    "cancel_scheduled_task": "block",
    "list_scheduled_tasks": "cancel",
    "query_task_executions": "cancel",
    "schedule_task": "block",
    "trigger_scheduled_task": "block",
    "update_scheduled_task": "block",

    # ── Skills ────────────────────────────────────────────────
    "execute_skill": "block",
    "find_skills": "cancel",
    "get_skill_info": "cancel",
    "get_skill_reference": "cancel",
    "install_skill": "block",
    "list_skills": "cancel",
    "load_skill": "cancel",  # in-memory load; cheap and idempotent
    "manage_skill_enabled": "block",
    "reload_skill": "cancel",
    "run_skill_script": "block",
    "uninstall_skill": "block",

    # ── Skill store ──────────────────────────────────────────
    "get_store_skill_detail": "cancel",
    "install_store_skill": "block",
    "search_store_skills": "cancel",
    "submit_skill_repo": "block",

    # ── Stickers ─────────────────────────────────────────────
    # (send_sticker already listed under IM channel.)

    # ── Web ─────────────────────────────────────────────────
    "news_search": "cancel",
    "web_fetch": "cancel",
    "web_search": "cancel",

    # ── Worktree ────────────────────────────────────────────
    # Worktree ops touch git internals — block for safety.
    "enter_worktree": "block",
    "exit_worktree": "block",
}


# ── Public API ───────────────────────────────────────────────────


def get_tool_interrupt_behavior(
    name: str,
    *,
    mcp_annotations: dict[str, Any] | None = None,
) -> InterruptBehavior:
    """Resolve the interrupt behavior for a tool by name.

    Resolution order:
    1. Built-in static map (this module).
    2. ``mcp_annotations["interruptBehavior"]`` — when the caller has
       the MCP server's tool annotations available and the server
       declared a value of ``"cancel"`` or ``"block"``.
    3. :data:`DEFAULT_BEHAVIOR` (= ``"block"``).

    Note: the static map wins over MCP annotations for tools we ship
    ourselves; an MCP server cannot upgrade a built-in ``"block"`` tool
    to ``"cancel"``.  Annotations are only consulted for tools we don't
    know about (third-party MCP tools, dynamic registrations).
    """
    if name in _INTERRUPT_BEHAVIOR_MAP:
        return _INTERRUPT_BEHAVIOR_MAP[name]
    if mcp_annotations:
        ann = mcp_annotations.get("interruptBehavior")
        if ann in ("cancel", "block"):
            return ann  # type: ignore[return-value]
    return DEFAULT_BEHAVIOR


def is_unknown_tool(name: str) -> bool:
    """True if ``name`` has no explicit entry in the static map.

    Used by startup warn (``warn_unclassified_tools``) and by the
    completeness test to surface drift between the tool registry and
    this table.
    """
    return name not in _INTERRUPT_BEHAVIOR_MAP


def known_tools() -> frozenset[str]:
    """All tool names with an explicit entry. Test / debug helper."""
    return frozenset(_INTERRUPT_BEHAVIOR_MAP.keys())


def has_any_block_tool(names: list[str]) -> bool:
    """Convenience for ``_preempt_or_queue_prev_task``: True if any of
    the given in-flight tool names resolves to ``"block"`` (including
    unknown tools, which default to block).  An empty list returns
    False — nothing in flight means INTERRUPT is unambiguously safe."""
    for n in names:
        if get_tool_interrupt_behavior(n) == "block":
            return True
    return False


def partition_by_behavior(names: list[str]) -> tuple[list[str], list[str]]:
    """Return ``(block_tools, cancel_tools)``.  Useful for logging the
    actual culprits that caused an INTERRUPT downgrade."""
    block_tools: list[str] = []
    cancel_tools: list[str] = []
    for n in names:
        if get_tool_interrupt_behavior(n) == "block":
            block_tools.append(n)
        else:
            cancel_tools.append(n)
    return block_tools, cancel_tools


def warn_unclassified_tools(tool_names: list[str]) -> int:
    """Walk the given tool names, log a warning for each one missing
    from the static map.  Intended to run once at agent startup so
    contributors notice drift.  Returns the count of warnings logged."""
    warned = 0
    for n in tool_names:
        if is_unknown_tool(n):
            logger.warning(
                "[ToolInterrupt] tool %r has no interrupt_behavior tag; "
                "defaulting to %r (INTERRUPT will downgrade to QUEUE while "
                "this tool is in flight). Add an entry to "
                "openakita.core.tool_interrupt_behavior._INTERRUPT_BEHAVIOR_MAP "
                "to opt in to mid-flight cancel.",
                n,
                DEFAULT_BEHAVIOR,
            )
            warned += 1
    return warned


__all__ = [
    "DEFAULT_BEHAVIOR",
    "InterruptBehavior",
    "get_tool_interrupt_behavior",
    "has_any_block_tool",
    "is_unknown_tool",
    "known_tools",
    "partition_by_behavior",
    "warn_unclassified_tools",
]
