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

Sprint-5 shipped a one-round bound; the test7 quality RCA (2026-06)
lifted it to a **bounded, budget-guarded multi-round ReAct loop** (see
:data:`MAX_TOOL_ROUNDS` / :data:`MAX_TOOL_CALLS` and the docstring of
:func:`run_with_tools`) so a node can iterate
(search -> search again -> write) instead of delivering its raw
mid-reasoning. MCP servers and skill SKILL.md auto-loading remain
deferred (audit §7.1 ``Not in P0-1 scope``).

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
* **Skill SKILL.md auto-load** (D4-ext) -- deferred.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._runtime_agent_host import NodeToolHost

# ── Org-node write sandbox (isolation guard) ──────────────────────────────
# Org node agents share the desktop Agent's FileTool, whose ``_resolve_path``
# returns absolute paths verbatim and resolves relative paths under CWD (= the
# repo root in a source run). A node that wrote a relative path such as
# ``src/openakita/orgs/tool_handler.py`` therefore landed INSIDE the source
# tree — exactly the "stray tool_handler.py" pollution incident. Deliverables
# are already auto-persisted to ``data/orgs/<id>/artifacts/`` by the executor,
# so org nodes never have a legitimate reason to write into the project's own
# source/config tree. This guard rejects write-class tool calls whose target
# resolves into the OpenAkita source tree (or the VCS dir), anchored on the
# real package location so it holds regardless of CWD. It deliberately does
# NOT restrict writes to ``data/`` or to user-chosen output paths.

# tool_name -> list of arg keys that carry a writable destination path.
_WRITE_PATH_KEYS: dict[str, tuple[str, ...]] = {
    "write_file": ("path", "file_path"),
    "edit_file": ("path", "file_path"),
    "append_file": ("path", "file_path"),
    "create_file": ("path", "file_path"),
    "delete_file": ("path", "file_path"),
    "move_file": ("src", "dst", "source", "destination", "dest"),
    "copy_file": ("src", "dst", "source", "destination", "dest"),
    "rename_file": ("src", "dst", "source", "destination", "dest"),
    "create_directory": ("path", "dir_path"),
}


@lru_cache(maxsize=1)
def _guarded_source_roots() -> tuple[Path, ...]:
    """Absolute dirs an org node must never write into.

    Anchored on ``openakita.__file__`` so the source tree is protected even
    when the process CWD differs from the repo root.
    """
    try:
        import openakita

        pkg_dir = Path(openakita.__file__).resolve().parent  # .../src/openakita
        src_dir = pkg_dir.parent  # .../src
        repo_root = src_dir.parent  # repo root
        roots = [pkg_dir, repo_root / ".git", repo_root / "apps", repo_root / "tests"]
        return tuple(r for r in roots if r)
    except Exception:  # noqa: BLE001 -- never let the guard import break a run
        return ()


# tool_name -> destination arg keys whose RELATIVE values get redirected into
# the org's artifacts dir. Deliberately excludes ``src``/``source`` (read
# sources for move/copy must not be relocated) and ``delete_file`` (relocating a
# delete target would silently change semantics; the source-tree guard still
# protects it). Absolute paths are never redirected here — they fall through to
# :func:`_guarded_write_violation`.
_WRITE_DEST_KEYS: dict[str, tuple[str, ...]] = {
    "write_file": ("path", "file_path"),
    "edit_file": ("path", "file_path"),
    "append_file": ("path", "file_path"),
    "create_file": ("path", "file_path"),
    "move_file": ("dst", "destination", "dest"),
    "copy_file": ("dst", "destination", "dest"),
    "rename_file": ("dst", "destination", "dest"),
    "create_directory": ("path", "dir_path"),
}


def _org_artifacts_dir(org_id: str) -> Path | None:
    """Resolve ``data/orgs/<org_id>/artifacts`` (the org-scoped output dir).

    Reuses the artifacts module's resolver so this matches exactly where the
    executor auto-persists node deliverables (download paths stay consistent).
    """
    try:
        from ._runtime_node_artifacts import _resolve_org_dir, safe_path_segment

        org_dir = _resolve_org_dir(None, org_id)
        if org_dir is None:
            return None
        # Anchor under the same per-org tree; ``safe_path_segment`` already
        # ran inside _resolve_org_dir for the fallback path.
        _ = safe_path_segment  # imported for parity / future use
        return org_dir / "artifacts"
    except Exception:  # noqa: BLE001 -- never let redirect break a run
        return None


def _redirect_relative_writes(
    tool_name: str, tool_input: dict[str, Any], org_id: str
) -> list[tuple[str, str]]:
    """Rewrite RELATIVE write destinations to live under the org artifacts dir.

    Mutates ``tool_input`` in place. Returns a list of ``(original, rewritten)``
    pairs for logging/transparency. Absolute paths are left untouched (the
    source-tree guard handles them). Any ``..`` that would escape the artifacts
    dir is clamped to the artifacts root using just the basename, so a node can
    never traverse out of its sandbox via a relative path.
    """
    keys = _WRITE_DEST_KEYS.get(tool_name)
    if not keys or not isinstance(tool_input, dict):
        return []
    artifacts = _org_artifacts_dir(org_id)
    if artifacts is None:
        return []
    rewrites: list[tuple[str, str]] = []
    for key in keys:
        raw = tool_input.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            p = Path(raw)
            if p.is_absolute():
                continue  # absolute paths handled by the source-tree guard
            artifacts_root = artifacts.resolve()
            candidate = (artifacts_root / p).resolve()
            # Clamp ``..`` traversal: if the joined path escapes the artifacts
            # root, fall back to <artifacts>/<basename>.
            if candidate != artifacts_root and not candidate.is_relative_to(
                artifacts_root
            ):
                candidate = (artifacts_root / Path(raw).name).resolve()
            artifacts_root.mkdir(parents=True, exist_ok=True)
            tool_input[key] = str(candidate)
            rewrites.append((raw, str(candidate)))
        except Exception:  # noqa: BLE001 -- a malformed path can't be redirected
            continue
    return rewrites


def _guarded_write_violation(tool_name: str, tool_input: Mapping[str, Any]) -> str | None:
    """Return a rejection message if this write escapes into the source tree."""
    keys = _WRITE_PATH_KEYS.get(tool_name)
    if not keys or not isinstance(tool_input, Mapping):
        return None
    roots = _guarded_source_roots()
    if not roots:
        return None
    base = Path.cwd()
    for key in keys:
        raw = tool_input.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            p = Path(raw)
            resolved = (p if p.is_absolute() else base / p).resolve()
        except Exception:  # noqa: BLE001 -- a malformed path can't be validated
            continue
        for root in roots:
            try:
                if resolved == root or resolved.is_relative_to(root):
                    return (
                        f"[拒绝写入：{tool_name} 的目标路径 '{raw}' 落在 OpenAkita 工程源码"
                        f"目录内（{root.name}）。节点产出请写入 data/ 工作区或交付目录，"
                        f"不要写入工程源码树。]"
                    )
            except Exception:  # noqa: BLE001 -- is_relative_to edge cases
                continue
    return None

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


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    """Read a bounded int from the environment (clamped to ``[lo, hi]``)."""
    import os

    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return default


MAX_TOOL_ROUNDS = _env_int("OPENAKITA_ORG_MAX_TOOL_ROUNDS", 6, lo=1, hi=12)
"""Cap on tool-call ROUNDS per node activation (one round == one
``tool_use`` -> ``tool_result`` -> next LLM call cycle).

Quality root-fix (test7 RCA, 2026-06): the Sprint-5 ``= 1`` bound made
:func:`run_with_tools` return the LLM's *second* response verbatim even
when that response was still mid-reasoning ("搜索结果不理想，让我再搜一次")
or another ``tool_use`` request. The node therefore "delivered" its raw
chain-of-thought (76-413B ``thinking…`` artifacts) instead of doing the
work. A bounded ReAct loop lets a node iterate (search -> search again ->
write) until it produces a real answer.

The roadmap (``docs/follow-ups/skipped-items-roadmap.md``) gated this
behind "validate per-org behaviour under the node's tool budget": we
ship the loop together with :data:`MAX_TOOL_CALLS` (a hard per-activation
tool-execution budget) and validate token/round counts on test7. The
bound stays env-overridable so an operator can dial it back to ``1``
(byte-for-byte Sprint-5 behaviour) without a code change."""


MAX_TOOL_CALLS = _env_int("OPENAKITA_ORG_MAX_TOOL_CALLS", 16, lo=1, hi=40)
"""Hard budget on TOTAL tool executions per node activation, independent
of rounds (a single round may emit several ``tool_use`` blocks). Once the
budget is spent the loop forces ONE final tool-less LLM call so the node
still returns a clean text answer instead of leaking another tool_use /
thinking turn. This is the cost guard the roadmap required before
lifting :data:`MAX_TOOL_ROUNDS` above 1."""


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

    # Org-scope sandbox: redirect RELATIVE write destinations into the org's
    # ``artifacts/`` dir BEFORE preview/exec so a bare filename like
    # ``jianlai_points.md`` lands in data/orgs/<id>/artifacts/ instead of the
    # process CWD (repo root). Absolute paths still hit the source-tree guard.
    redirects = _redirect_relative_writes(tool_name, tool_input, org_id)
    if redirects:
        _LOGGER.info(
            "[node-tool] redirected %s relative write(s) into org artifacts "
            "(org=%s node=%s): %s",
            tool_name,
            org_id,
            node_id,
            "; ".join(f"{o} -> {n}" for o, n in redirects),
        )

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

    # Isolation guard: block write-class tools whose target escapes into the
    # OpenAkita source tree (the "stray tool_handler.py" pollution incident).
    violation = _guarded_write_violation(tool_name, tool_input)
    if violation is not None:
        await _safe_emit(
            emit,
            "node_tool_failed",
            {
                "org_id": org_id,
                "node_id": node_id,
                "command_id": command_id,
                "tool_name": tool_name,
                "reason": "write_path_blocked",
            },
        )
        _LOGGER.warning(
            "[node-tool] blocked source-tree write org=%s node=%s tool=%s",
            org_id,
            node_id,
            tool_name,
        )
        return (violation, True)

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
    cancel_event: asyncio.Event | None = None,
) -> tuple[Any, int]:
    """Bounded multi-round ReAct loop on :meth:`Brain.messages_create_async`.

    Returns ``(final_response, tool_rounds)`` where ``tool_rounds`` is
    how many tool-use rounds ran (0 when the first response was already
    a final answer). ``final_response`` is the last
    ``messages_create_async`` result so the caller can extract text +
    attribute it to events / artefacts unchanged.

    Loop semantics (quality root-fix, test7 RCA 2026-06):

    * Each iteration calls the brain, extracts ``tool_use`` blocks and,
      if there are none, returns the response as the FINAL answer.
    * When the LLM emits ``tool_use`` we run each tool sequentially,
      splice the ``tool_result`` blocks back in, and loop -- so the node
      can iterate (search -> search again -> write) instead of being
      forced to "finalise" after a single tool call (which is what made
      it deliver raw ``thinking…`` mid-reasoning).
    * The loop is bounded by :data:`MAX_TOOL_ROUNDS` rounds AND
      :data:`MAX_TOOL_CALLS` total tool executions. When either budget is
      exhausted we make ONE last call with ``tools=[]`` so the LLM is
      forced to emit a clean text answer instead of another ``tool_use``
      turn that we would otherwise return verbatim.

    The ``second_round_caller`` parameter is a test hook: when given it
    is used for every brain call AFTER the first, so existing one-round
    fixtures keep asserting on the spliced ``tool_result`` history.

    Sprint-13 H1 (RC-4 §6 H1): ``cancel_event`` is forwarded to every
    ``brain.messages_create_async`` call so :meth:`LLMClient._race_with_cancel`
    can abort the in-flight ``httpx`` request the moment a user cancel
    fires.
    """

    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]

    async def _call_brain(turn_messages: list[dict[str, Any]], *, with_tools: bool) -> Any:
        # Subsequent turns prefer the test hook so fixtures can capture
        # the spliced tool_result history. The hook ignores the
        # tools/system kwargs (it stubs the LLM entirely).
        if second_round_caller is not None and len(turn_messages) > 1:
            return await second_round_caller(turn_messages)
        return await brain.messages_create_async(
            messages=turn_messages,
            system=system_prompt,
            tools=tools if with_tools else [],
            cancel_event=cancel_event,
        )

    rounds = 0
    total_tool_calls = 0
    # First turn always offers tools (unless the caller passed none).
    response = await _call_brain(messages, with_tools=bool(tools))

    while True:
        tool_blocks = extract_tool_use_blocks(response) if tools else []
        if not tool_blocks:
            return response, rounds

        # Capture the LLM's tool_use turn verbatim (Anthropic requires the
        # prior assistant turn to contain the tool_use blocks the
        # following tool_result references).
        assistant_blocks = _content_blocks_for_assistant(response)
        if not assistant_blocks:
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

        # Run each tool sequentially (deterministic ordering + trivial
        # cancellation propagation, matching the Sprint-4 child-dispatch
        # rationale).
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
            total_tool_calls += 1
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": text,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})
        rounds += 1

        budget_spent = rounds >= MAX_TOOL_ROUNDS or total_tool_calls >= MAX_TOOL_CALLS
        if budget_spent:
            _LOGGER.info(
                "[node-tool] tool budget reached (org=%s node=%s rounds=%d calls=%d); "
                "forcing final tool-less answer",
                org_id,
                node_id,
                rounds,
                total_tool_calls,
            )
            # Force a clean text answer: no tools on the last call so the
            # LLM cannot emit another tool_use we would return as "output".
            return await _call_brain(messages, with_tools=False), rounds

        response = await _call_brain(messages, with_tools=True)
