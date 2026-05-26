"""``_runtime_node_tools.py`` -- v2 orgs node-level tool resolution + execution.

Sprint-5 P0-1 (audit ``_orgs_business_capability_audit_v5.md`` §5.2 / §7.1)
lands the **D4 minimum-viable cut**: per-node ``external_tools`` declared on
the v1 ``OrgNode`` (which is what every aigc-video-studio v16-* test org
materialises into) are resolved into a real Anthropic-shaped tool list and
passed to :meth:`Brain.messages_create_async`. When the LLM emits a
``tool_use`` block the node agent runs the handler via the same global
:data:`openakita.tools.handlers.default_handler_registry` the v1 chat path
uses, splices the ``tool_result`` block back into the conversation, and
calls the brain a **second** time so the LLM can finalise its reply.

This is intentionally a one-round bound: see the module-level constant
:data:`MAX_TOOL_ROUNDS` and the docstring of
:func:`run_with_tools`. Multi-round ReAct, MCP servers, and skill
SKILL.md auto-loading are deferred (audit §7.1 ``Not in P0-1 scope``).

### Why mirror v1 and not re-implement

The v1 chat path already wires every system tool handler (filesystem,
research, memory, planning, web_fetch, web_search, ...) into the global
:class:`SystemHandlerRegistry`. The orgs_v2 node path was sending
``tools=[]`` to the brain and therefore the v16 LLM debug dumps reported
``tools_count = 0`` for every workbench dispatch (audit v5 §5.2.2). By
reusing the same registry we:

* avoid duplicating handler implementations,
* benefit from any future tool the main agent gains for free,
* keep the per-node *whitelist* mechanic (``external_tools``) as the
  single source of truth for what a node may call,
* and stay zero-dependency on the workbench / MCP plumbing that the
  workbench nodes will eventually need (those routes through plugin
  manifests, which v2 does not consume yet).

### What is intentionally out of scope

* **Workbench (``hh_*``) tools** -- those live in the
  ``happyhorse-video`` plugin manifest. The plugin handler registry is
  separate from :data:`default_handler_registry`; binding it requires
  the workbench wiring tracked under the D4-ext follow-up. We **filter
  unknown tool names** so the LLM still gets the standard subset
  (research / planning / filesystem / memory etc) and the node can do
  *something* useful even on a workbench node.
* **MCP servers** declared on ``node.mcp_servers`` -- ignored for now
  (audit §7.1 explicit ``Not in P0-1 scope``).
* **Multi-round ReAct loop** -- we run **exactly one** tool round and
  then ask the LLM for a final answer. Multi-round is the next-sprint
  follow-up; without it the simplest "fetch + describe" tasks already
  work, which is what the v17 audit needs to observe.
* **Skill SKILL.md auto-load** (D4-ext) -- deferred.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._runtime_agent_host import NodeToolHost

__all__ = [
    "MAX_TOOL_ROUNDS",
    "NodeToolEmit",
    "NodeToolHostProvider",
    "execute_node_tool",
    "extract_tool_use_blocks",
    "resolve_node_tools",
    "run_with_tools",
]


_LOGGER = logging.getLogger(__name__)


# Sprint-6 P0-1 (RCA ``_v17_p1_rca.md`` §1.5): per-node-agent callable
# that returns the currently-bound :class:`NodeToolHost` (or ``None``
# when the desktop Agent is not yet wired). We use a provider closure
# rather than a direct reference because :class:`DefaultAgentBuilder`
# is constructed inside the FastAPI lifespan *before* the host can
# exist (``app.state.agent`` is populated later by ``main.py``), so
# the closure picks the host up on first node activation -- mirrors
# the Sprint-2 ``brain_provider`` rationale.
NodeToolHostProvider = Callable[[], "NodeToolHost | None"]


MAX_TOOL_ROUNDS = 1
"""Hard cap on tool-call rounds per node activation. Sprint-5 ships a
single round (LLM call -> tool_use -> tool_result -> LLM final). Setting
this above 1 is a multi-round ReAct loop and is deferred to the next
sprint; we keep it as a module-level constant so the bound is explicit
and a future bump is a single-token diff."""


# Best-effort emitter signature: ``(event_name, payload_dict) -> Awaitable[None]``.
# We tolerate sync callables too (in case a test fixture passes a plain
# ``MagicMock`` or a wrapper that captures events into a list); the
# ``_safe_emit`` helper below handles both shapes.
NodeToolEmit = Callable[[str, dict[str, Any]], Any]


def _flatten_external_tools(entries: Iterable[str] | None) -> set[str]:
    """Expand category names (``research`` etc) to concrete tool names.

    Mirrors :func:`openakita.orgs._runtime_tool_categories.expand_tool_categories`
    so the orgs_v2 node path consumes the exact same whitelist semantics
    the main agent's ``Agent._effective_tools`` (v1) reaches via
    ``expand_tool_categories`` inside ``agents/factory.py``. Importing
    lazily keeps the bootstrap cycle (orgs <-> orgs._runtime_tool_categories
    <-> orgs._default_agent_builder) tight.
    """

    if not entries:
        return set()
    from ._runtime_tool_categories import expand_tool_categories

    return expand_tool_categories(list(entries))


def resolve_node_tools(
    *,
    external_tools: Iterable[str] | None,
    enable_file_tools: bool = True,
    tool_host: NodeToolHost | None = None,
) -> list[dict[str, Any]]:
    """Translate a v1-style ``external_tools`` whitelist into LLM tool dicts.

    ``enable_file_tools`` mirrors the :class:`OrgNode` flag: when ``True``
    (the v1 default), the four "basic file tools" (``write_file``,
    ``read_file``, ``edit_file``, ``list_directory``) are auto-merged in
    so non-filesystem-explicit roles can still drop deliverables. The
    aigc-video-studio template **disables** this for workbench nodes
    (``wb-hh-*``) so they only have the explicit ``hh_*`` whitelist.

    Sprint-6 P0-3 (RCA ``_v17_p1_rca.md`` §4 P0-3): when ``tool_host``
    is supplied the resolver also looks up plugin-provided
    definitions (``hh_image_create`` etc) via the host's tool catalog
    -- the plugin API extends ``agent._tools`` with their
    Anthropic-shape definitions, so the workbench ``wb-hh-*`` nodes
    finally see their declared external tools instead of having them
    silently dropped (Sprint-5 §3 P0-3 "out of scope" note).

    Tools unknown to **both** the host and :func:`get_tool_definition`
    are dropped with a debug log -- preserves Sprint-5 behaviour for
    bare-builder fixtures that never set up a host.
    """

    flat = _flatten_external_tools(external_tools)
    if enable_file_tools:
        flat.update({"write_file", "read_file", "edit_file", "list_directory"})

    # Lazy import: tools/definitions/ imports a large module graph
    # (browser / mcp / web_fetch) we do not want at orgs_v2 import time.
    from openakita.tools.definitions import get_tool_definition

    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped: list[str] = []
    for name in sorted(flat):
        if name in seen:
            continue
        seen.add(name)
        defn: dict[str, Any] | None = None
        # Sprint-6 P0-3: prefer the host's lookup so plugin tools
        # (``hh_*``) are included. The host inspects the live
        # ``agent._tools`` list -- which is what ``plugins/api.py``
        # extends after each plugin registers -- so any tool the LLM
        # might legitimately call is reachable here.
        if tool_host is not None:
            try:
                defn = tool_host.lookup_tool_definition(name)
            except Exception:  # noqa: BLE001 -- best-effort
                defn = None
        if defn is None:
            static_defn = get_tool_definition(name)
            if static_defn is not None:
                defn = {
                    "name": static_defn.get("name", name),
                    "description": static_defn.get("description", ""),
                    "input_schema": static_defn.get("input_schema", {"type": "object"}),
                }
        if defn is None:
            dropped.append(name)
            continue
        # Brain.messages_create_async accepts the canonical Anthropic
        # shape ``{name, description, input_schema}``; copy only those
        # three keys so unrelated fields (``category``, ``examples``,
        # ``detail``) do not balloon the prompt budget.
        resolved.append(
            {
                "name": defn.get("name", name),
                "description": defn.get("description", ""),
                "input_schema": defn.get("input_schema", {"type": "object"}),
            }
        )
    if dropped:
        _LOGGER.debug(
            "[orgs_v2 node tools] dropped unknown tool names (likely "
            "plugin / workbench tools not yet wired): %s",
            sorted(dropped),
        )
    return resolved


def extract_tool_use_blocks(response: Any) -> list[dict[str, Any]]:
    """Pull ``tool_use`` blocks out of a Brain ``Message``-shaped response.

    Returns a list of ``{"id", "name", "input"}`` dicts in LLM-emit
    order. Mirrors the v1 ``_parse_decision`` walk in
    ``core/_reasoning_engine_legacy.py`` but stripped to just what the
    second-round prompt needs. Robust to both Anthropic SDK objects
    (attribute access) and provider-shim dicts (``isinstance(content,
    list)`` -> nested ``.type`` / ``.name`` lookups).
    """

    content = getattr(response, "content", None)
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for raw in content:
        if isinstance(raw, dict):
            btype = raw.get("type")
            if btype != "tool_use":
                continue
            blocks.append(
                {
                    "id": str(raw.get("id") or ""),
                    "name": str(raw.get("name") or ""),
                    "input": raw.get("input") or {},
                }
            )
            continue
        btype = getattr(raw, "type", None)
        if btype != "tool_use":
            continue
        blocks.append(
            {
                "id": str(getattr(raw, "id", "") or ""),
                "name": str(getattr(raw, "name", "") or ""),
                "input": getattr(raw, "input", {}) or {},
            }
        )
    return blocks


def _content_blocks_for_assistant(response: Any) -> list[dict[str, Any]]:
    """Re-serialise a Brain response into the assistant-turn ``content``
    list expected by :func:`Brain.messages_create_async` when we feed
    the conversation back for a second round.

    Anthropic requires that when you reply with a ``tool_result`` user
    message, the *prior* assistant turn must contain the original
    ``tool_use`` block(s). We rebuild that turn here from the response
    object verbatim (text blocks + tool_use blocks); any unknown block
    type is skipped so a provider returning extra metadata does not
    poison the second call.
    """

    content = getattr(response, "content", None)
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for raw in content:
        if isinstance(raw, dict):
            btype = raw.get("type")
            if btype == "text":
                txt = raw.get("text", "")
                if txt:
                    blocks.append({"type": "text", "text": str(txt)})
            elif btype == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(raw.get("id") or ""),
                        "name": str(raw.get("name") or ""),
                        "input": raw.get("input") or {},
                    }
                )
            continue
        btype = getattr(raw, "type", None)
        if btype == "text":
            txt = getattr(raw, "text", "")
            if txt:
                blocks.append({"type": "text", "text": str(txt)})
        elif btype == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(getattr(raw, "id", "") or ""),
                    "name": str(getattr(raw, "name", "") or ""),
                    "input": getattr(raw, "input", {}) or {},
                }
            )
    return blocks


async def _safe_emit(emit: NodeToolEmit | None, event: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget event emission with all exceptions swallowed.

    The orgs_v2 event bus emits return awaitables; some test fixtures
    pass a plain ``MagicMock`` whose call result is not awaitable. We
    accept either shape so the executor wiring stays liberal.
    """

    if emit is None:
        return
    try:
        result = emit(event, payload)
        if asyncio.iscoroutine(result):
            await result
    except asyncio.CancelledError:
        # Cancellation must propagate -- the surrounding node-agent
        # ``run`` is what owns the cancel pipeline. Drop our event
        # silently and let the parent ``raise`` happen.
        raise
    except Exception:  # noqa: BLE001 -- event emission must never block tool execution
        _LOGGER.debug("node tool event emission raised", exc_info=True)


async def execute_node_tool(
    *,
    tool_name: str,
    tool_input: dict[str, Any],
    org_id: str,
    node_id: str,
    command_id: str | None,
    emit: NodeToolEmit | None = None,
    tool_host: NodeToolHost | None = None,
) -> tuple[str, bool]:
    """Run one tool via :class:`NodeToolHost` (Sprint-6 P0-1) with safety net.

    Returns ``(text, is_error)``:

    * ``is_error=False`` -- the handler returned a string (or coerced
      result). We use it as the ``content`` of the ``tool_result``
      block sent back to the LLM.
    * ``is_error=True`` -- the handler raised or no handler was mapped.
      The error text is still surfaced (inline in ``tool_result.content``)
      so the LLM can decide how to proceed; this matches the v1
      :class:`ToolExecutor` policy (an unknown / failing tool returns a
      structured error string rather than blowing up the whole turn).

    Sprint-6 P0-1 (RCA ``_v17_p1_rca.md`` §1.5): the host's
    ``handler_registry`` is the *populated* one from the desktop
    Agent (filesystem / memory / web_search / 20 system handlers +
    every plugin-registered tool). When ``tool_host`` is ``None`` we
    fall back to the Sprint-5 global registry path so headless test
    fixtures and the FastAPI lifespan-race window (host not yet
    wired) keep working -- the fallback will still emit
    ``node_tool_failed`` for unknown tools, byte-for-byte v17
    observable.

    Cancellation is propagated unchanged -- if the surrounding task is
    cancelled we re-raise :class:`asyncio.CancelledError` so the cancel
    pipeline (Sprint-3 P0-2) keeps working through tool execution.
    """

    args_preview = ""
    if isinstance(tool_input, Mapping):
        try:
            import json as _json

            args_preview = _json.dumps(tool_input, ensure_ascii=False)[:200]
        except Exception:  # noqa: BLE001 -- preview is best-effort
            args_preview = repr(tool_input)[:200]
    await _safe_emit(
        emit,
        "node_tool_called",
        {
            "org_id": org_id,
            "node_id": node_id,
            "command_id": command_id,
            "tool_name": tool_name,
            "args_preview": args_preview,
        },
    )

    # Lazy import: the host module pulls a small graph but the
    # exception class is hashable so a late import keeps the orgs_v2
    # package import-time light.
    from ._runtime_agent_host import ToolNotAvailable

    try:
        if tool_host is not None:
            result = await tool_host.execute_tool(
                tool_name,
                dict(tool_input),
                node_id=node_id,
                command_id=command_id,
            )
        else:
            # Sprint-5 fallback path (RCA §1.5.4 rollback): the global
            # registry stays empty in production, so the lookup will
            # raise ``ValueError`` and we surface it as the same
            # ``node_tool_failed`` payload v17 observed. This branch
            # only fires in test fixtures that monkeypatch
            # ``default_handler_registry.execute_by_tool``.
            from openakita.tools.handlers import default_handler_registry

            result = await default_handler_registry.execute_by_tool(
                tool_name, dict(tool_input)
            )
    except asyncio.CancelledError:
        # User cancel must propagate to the outer node-agent run so the
        # outcome cache resolves to ``cancelled`` instead of failing
        # this tool as an error.
        raise
    except ToolNotAvailable as exc:
        # Sprint-6 P0-3: classify "plugin tool not loaded" distinctly
        # from a generic handler crash so events.jsonl readers can
        # tell whether ``hh_*`` failed because the plugin manifest
        # is missing vs the API is down. The Sprint-5 path turned
        # both into ``error="No handler mapped for tool: <name>"``.
        _LOGGER.warning(
            "[orgs_v2 node tool] %s.%s unavailable: %s",
            node_id,
            tool_name,
            exc.reason,
        )
        await _safe_emit(
            emit,
            "node_tool_failed",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "reason": "plugin_not_loaded",
                "error": exc.reason,
            },
        )
        return (
            f"[tool {tool_name} unavailable: {exc.reason}]",
            True,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning(
            "[orgs_v2 node tool] %s.%s raised: %s",
            node_id,
            tool_name,
            exc,
        )
        await _safe_emit(
            emit,
            "node_tool_failed",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "reason": "handler_raised",
                "error": str(exc),
            },
        )
        return (f"[tool {tool_name} failed: {exc}]", True)

    text = result if isinstance(result, str) else str(result)
    await _safe_emit(
        emit,
        "node_tool_completed",
        {
            "org_id": org_id,
            "node_id": node_id,
            "command_id": command_id,
            "tool_name": tool_name,
            "result_len": len(text),
        },
    )
    return (text, False)


async def run_with_tools(
    *,
    brain: Any,
    system_prompt: str,
    user_content: str,
    tools: list[dict[str, Any]],
    org_id: str,
    node_id: str,
    command_id: str | None,
    emit: NodeToolEmit | None = None,
    second_round_caller: Callable[[list[dict[str, Any]]], Awaitable[Any]] | None = None,
    tool_host: NodeToolHost | None = None,
) -> tuple[Any, int]:
    """One-round tool-use loop on top of :meth:`Brain.messages_create_async`.

    Returns ``(final_response, tool_rounds)`` where ``tool_rounds`` is
    how many tool-use rounds ran (0 when the first response was already
    a final answer; 1 when the LLM emitted at least one ``tool_use``
    block and we called it back). ``final_response`` is the
    last ``messages_create_async`` result so the caller can extract
    text + attribute it to events / artefacts the same way it did
    pre-Sprint-5.

    The ``second_round_caller`` parameter is a test hook: when not
    given, we call ``brain.messages_create_async(messages=..., system=...,
    tools=tools)`` for the second round directly. Tests pass a stub
    that captures the messages list for assertion without touching a
    real brain.
    """

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    response = await brain.messages_create_async(
        messages=messages,
        system=system_prompt,
        tools=tools,
    )

    tool_blocks = extract_tool_use_blocks(response) if tools else []
    if not tool_blocks:
        return response, 0

    # Round 1: capture the LLM's tool_use turn verbatim, run each
    # tool sequentially, then ask the LLM to wrap up. Sequential
    # (not gather) keeps cancellation propagation trivial and the
    # LLM debug ordering deterministic, matching the Sprint-4
    # ``Decision C`` rationale for child dispatch.
    assistant_blocks = _content_blocks_for_assistant(response)
    if not assistant_blocks:
        # Defensive: a provider returned only metadata. Synthesise
        # the tool_use blocks we already extracted so the second
        # round still validates server-side.
        assistant_blocks = [
            {
                "type": "tool_use",
                "id": block["id"],
                "name": block["name"],
                "input": block["input"],
            }
            for block in tool_blocks
        ]
    messages.append({"role": "assistant", "content": assistant_blocks})

    tool_results: list[dict[str, Any]] = []
    for block in tool_blocks:
        tool_name = block["name"]
        tool_input = block["input"] if isinstance(block["input"], dict) else {}
        text, is_error = await execute_node_tool(
            tool_name=tool_name,
            tool_input=tool_input,
            org_id=org_id,
            node_id=node_id,
            command_id=command_id,
            emit=emit,
            tool_host=tool_host,
        )
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": block["id"],
                "content": text,
                "is_error": is_error,
            }
        )
    messages.append({"role": "user", "content": tool_results})

    if second_round_caller is not None:
        final = await second_round_caller(messages)
    else:
        final = await brain.messages_create_async(
            messages=messages,
            system=system_prompt,
            tools=tools,
        )
    return final, 1
