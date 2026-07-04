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
    "append_file": "block",
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
    return any(get_tool_interrupt_behavior(n) == "block" for n in names)


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


# ── MCP sub-tool name encoding (v1.28.2 FOLLOW-UP-S4-B) ─────────────
#
# When ``call_mcp_tool`` is invoked, the dispatcher itself is marked
# ``"block"`` (safe default).  But the REAL remote tool is whatever
# ``tool_input["tool_name"]`` is, and *that* tool's MCP annotations
# may declare a different interrupt behavior (read-only MCP tools can
# legitimately opt into ``"cancel"``).
#
# To make ``in_flight_tools`` and ``has_any_block_tool`` see the
# correct granularity, we encode the sub-tool as
# ``mcp:{server}:{sub_tool_name}`` when registering and decode at
# resolution time.

MCP_TOOL_PREFIX: Final[str] = "mcp:"


def encode_mcp_sub_tool(server: str, sub_tool_name: str) -> str:
    """Encode an MCP sub-tool reference for ``begin_tool`` / ``end_tool``.

    The encoded string is opaque to in_flight_tools — it's just a tag —
    but ``get_tool_interrupt_behavior`` and ``parse_mcp_sub_tool``
    understand the format.

    Empty or missing pieces fall back to the literal dispatcher name
    ``"call_mcp_tool"`` so the rest of the pipeline still sees a known
    tool (and thus defaults to block).
    """
    s = (server or "").strip()
    t = (sub_tool_name or "").strip()
    if not s or not t:
        return "call_mcp_tool"
    return f"{MCP_TOOL_PREFIX}{s}:{t}"


def parse_mcp_sub_tool(name: str) -> tuple[str, str] | None:
    """Inverse of :func:`encode_mcp_sub_tool`.

    Returns ``(server, sub_tool_name)`` or ``None`` if ``name`` isn't
    an encoded MCP reference (e.g., it's a regular built-in tool name).
    """
    if not name or not name.startswith(MCP_TOOL_PREFIX):
        return None
    rest = name[len(MCP_TOOL_PREFIX) :]
    if ":" not in rest:
        return None
    server, sub_tool_name = rest.split(":", 1)
    if not server or not sub_tool_name:
        return None
    return server, sub_tool_name


def resolve_mcp_tool_behavior(
    server: str,
    sub_tool_name: str,
    *,
    mcp_client: Any = None,
) -> InterruptBehavior:
    """Resolve the interrupt behavior of a real MCP sub-tool.

    Looks up ``mcp_client._tools[f"{server}:{sub_tool_name}"].annotations``
    (the dict populated by ``MCPClient._discover_capabilities`` at
    server-connect time), reads
    ``annotations.get("interruptBehavior")``, and falls back to
    ``DEFAULT_BEHAVIOR`` ("block") when missing or invalid.

    Returns "block" when ``mcp_client`` is None — safe default for the
    "called before the catalog is available" case.
    """
    if mcp_client is None:
        return DEFAULT_BEHAVIOR
    tools_map = getattr(mcp_client, "_tools", None)
    if not isinstance(tools_map, dict):
        return DEFAULT_BEHAVIOR
    tool = tools_map.get(f"{server}:{sub_tool_name}")
    annotations = getattr(tool, "annotations", None) if tool else None
    if not isinstance(annotations, dict):
        return DEFAULT_BEHAVIOR
    return get_tool_interrupt_behavior(sub_tool_name, mcp_annotations=annotations)


def resolve_in_flight_behavior(
    name: str,
    *,
    mcp_client: Any = None,
) -> InterruptBehavior:
    """Resolve a possibly-encoded in_flight name to its behavior.

    Convenience for ``_preempt_or_queue_prev_task``: handles both the
    encoded ``mcp:server:sub_tool`` form (looks up MCP annotations) and
    the plain form (consults the static map).
    """
    parsed = parse_mcp_sub_tool(name)
    if parsed is not None:
        server, sub_tool_name = parsed
        return resolve_mcp_tool_behavior(server, sub_tool_name, mcp_client=mcp_client)
    return get_tool_interrupt_behavior(name)


def has_any_block_in_flight(
    names: list[str],
    *,
    mcp_client: Any = None,
) -> bool:
    """Like :func:`has_any_block_tool` but understands MCP sub-tool
    encoding.  Use this in the preempt-or-queue resolver where the
    in_flight list can contain encoded MCP references."""
    return any(resolve_in_flight_behavior(n, mcp_client=mcp_client) == "block" for n in names)


__all__ = [
    "DEFAULT_BEHAVIOR",
    "InterruptBehavior",
    "MCP_TOOL_PREFIX",
    "encode_mcp_sub_tool",
    "get_tool_interrupt_behavior",
    "has_any_block_in_flight",
    "has_any_block_tool",
    "is_unknown_tool",
    "known_tools",
    "parse_mcp_sub_tool",
    "partition_by_behavior",
    "resolve_in_flight_behavior",
    "resolve_mcp_tool_behavior",
    "warn_unclassified_tools",
]
