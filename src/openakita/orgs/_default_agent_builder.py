"""Default ``AgentBuilderProtocol`` -- minimum viable LLM binding for orgs_v2.

Sprint-2 P0-1 (audit ``_orgs_business_capability_audit_v2.md`` §5 / §8)
shipped the bare LLM binding: every node could finally produce one line of
LLM text instead of bouncing off ``_NullAgentBuilder``. Sprint-3 P0-1
(audit v3 §5.2) plumbed the real entry-node id end-to-end so the executor
stopped seeing ``node_id=None``.

Sprint-4 P0-1 (audit ``_orgs_business_capability_audit_v4.md`` §6.2 /
§8) tackles the remaining "single-LLM cosplay" symptom: even after
Sprint-3 the v15 run showed 35/35 commands had only one unique node id
in their LLM debug files (always ``producer``). The producer LLM was
inventing screenwriter / art-director "voices" inside one call rather
than really handing work off to those nodes.

This module now supports an **explicit XML dispatch syntax** that the
root node's system prompt teaches:

* The producer's LLM may emit zero or more
  ``<dispatch target="screenwriter">child instruction</dispatch>``
  blocks inside its reply.
* :class:`_BrainBackedNodeAgent` parses up to
  :data:`MAX_DISPATCH_BLOCKS` such blocks and, for each one, calls back
  into the injected ``dispatch_callback`` (wired by the executor) with
  the child node id + content.
* The callback recurses through the same executor pipeline so each
  child gets its own :class:`_BrainBackedNodeAgent`, its own
  ``agent_run_started`` / ``agent_run_finished`` events, its own
  artefact write (Sprint-4 P0-2), and its own ``context.node_id`` in
  LLM debug.
* Recursion is bounded by :data:`MAX_DISPATCH_DEPTH` so a runaway LLM
  cannot trigger an unbounded fan-out. Depth tracking flows through a
  module-level :class:`contextvars.ContextVar` set by the executor in
  ``activate_and_run``.
* Children run **serially** in this commit: it keeps cancel propagation
  simple (a single ``CancelledError`` unwinds the whole tree) and makes
  the LLM debug ordering deterministic; parallel ``asyncio.gather``
  fan-out is reserved for the next sprint.

The dispatch tutorial is only spliced into the system prompt at
``depth == 0`` so children (and grandchildren) do not get the "you may
dispatch" instructions and therefore cannot recurse further on their
own initiative even before the depth gate fires.

Sprint-4 P0-2 (audit v4 §5.4 / §6.2 #2) -- node artefact persistence -
is implemented at the executor layer (see
:mod:`._runtime_node_artifacts`), not here, because the executor is the
only layer that already owns the post-success bookkeeping (events,
emit, error mapping) and has clean access to the
``get_org_dir`` lookup. The builder stays small and stateless.

**Out of scope** (intentionally deferred to next sprint):

* Parallel ``asyncio.gather`` child fan-out.
* Inter-node memory retrieval at prompt time (the next node's prompt
  does not yet read the previous node's persisted memory).
* Node-level tool / skill / MCP injection (D4).
* Aggregator / Router / Retriever / Persister builder classes
  (still encoded inside this module + ``_runtime_dispatch.py``).

The builder is intentionally fail-fast: if the brain provider returns
``None`` (lifespan ordering -- HTTP up before the desktop ``Agent`` is
ready) we raise :class:`BuilderUnavailable`, which the executor catches
and turns into ``agent_run_failed reason=agent_build_failed``. That is
the same observable as the legacy ``_NullAgentBuilder`` path, so
downstream contracts (events.jsonl + SSE shape, ``get_status`` reading)
keep working unchanged.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ._runtime_agent_pipeline import (
    MAX_DISPATCH_BLOCKS,
    MAX_DISPATCH_DEPTH,
    AgentSpec,
    dispatch_depth_var,
)
from ._runtime_node_tools import (
    NodeToolEmit,
    resolve_node_tools,
    run_with_tools,
)

__all__ = [
    "MAX_DISPATCH_BLOCKS",
    "MAX_DISPATCH_DEPTH",
    "BuilderUnavailable",
    "DefaultAgentBuilder",
    "NodeToolEmit",
    "dispatch_depth_var",
    "parse_dispatch_blocks",
]

_LOGGER = logging.getLogger(__name__)

# A short marker prepended to every node system prompt so logs / debug
# dumps clearly attribute the LLM call to the orgs_v2 path (the v13
# audit's L4.1 finding: 0 LLM debug files were tagged with orgs_v2).
_NODE_SYSTEM_PREFIX = "[openakita orgs_v2 node agent]"


# Sprint-4 P0-1 recursion-safety constants and the depth ContextVar
# live in :mod:`._runtime_agent_pipeline` and are re-exported above
# (rationale: import-cycle avoidance with the executor shard; see the
# note next to the constant definitions in the pipeline module). The
# ``MAX_DISPATCH_*`` numbers chosen there cover the typical
# "producer (root) -> mid-tier (screenwriter/art-director) ->
# workbench leaf (wb-hh-*)" three-layer pattern of the
# ``aigc-video-studio`` template. Anything deeper is almost always
# a hallucinated runaway; we'd rather drop the tail than burn token
# budget on a 7-level pyramid that nobody asked for.


# ``<dispatch target="...">...</dispatch>`` -- ``DOTALL`` so multi-line
# child content is captured verbatim. We tolerate either quote style
# and arbitrary whitespace between the tag name and the ``target=``
# attribute so small LLM formatting drift (single vs double quotes,
# leading newline inside the tag) does not silently drop the block.
_DISPATCH_RE = re.compile(
    r"<dispatch\s+target\s*=\s*[\"']([^\"']+)[\"']\s*>(.*?)</dispatch>",
    re.IGNORECASE | re.DOTALL,
)


# Type alias for the dispatch callback the executor wires in. Keeping
# it on the module so tests / docstrings can refer to a single name.
DispatchCallback = Callable[..., Awaitable[str]]


# Type alias for the artefact persistor (Sprint-4 P0-2). The executor
# wires this in too so the builder stays free of filesystem concerns.
ArtifactPersistor = Callable[..., None]


class BuilderUnavailable(RuntimeError):
    """Raised by :class:`DefaultAgentBuilder.build` when the brain provider
    returns ``None`` (lifespan startup race / desktop Agent not yet ready).

    The executor catches this and emits the v1-parity
    ``agent_run_failed reason=agent_build_failed`` event, identical to
    what the legacy ``_NullAgentBuilder`` produced. Naming the exception
    distinctly makes log triage easier.
    """


def _dispatch_instructions() -> str:
    """Return the producer-level system-prompt tutorial for child dispatch.

    Kept as a single short paragraph so the per-node token budget stays
    bounded. The format is intentionally rigid (XML attribute syntax,
    one target per block) -- a relaxed format would let the LLM emit
    ambiguous text that the regex either over-matches or under-matches.
    The "do not nest" rule is enforced anyway by the depth gate; we
    surface it in the prompt so the model does not waste tokens on a
    nested ``<dispatch>`` the parent would just ignore.
    """

    return (
        "If you need a specialist node to handle part of the task, you "
        "may emit one or more dispatch blocks in your reply using EXACTLY "
        "this XML syntax: <dispatch target=\"NODE_ID\">instruction for "
        "that node</dispatch>. Use the literal node id (e.g. "
        "'screenwriter', 'art-director'). Emit at most "
        f"{MAX_DISPATCH_BLOCKS} dispatch blocks. Do not nest dispatch "
        "blocks. After the dispatch blocks the orchestrator will append "
        "each child's output to your reply, so your own text should "
        "focus on coordination (which child does what and why)."
    )


def _available_nodes_block(spec: AgentSpec) -> str:
    """Render the closed list of dispatch targets for the producer prompt.

    Sprint-5 unexpected-finding #1 (audit v5 §4.2 + §5.3): the v16 run
    showed the producer LLM inventing a ``director`` node that did not
    exist in the spec. The ``<dispatch target="...">`` parser tolerated
    it (unknown_target -> skip + warning, see Sprint-4 ``Decision F``)
    but the invention still cost one LLM round and polluted the
    aggregated reply. Listing the *actual* node ids inline -- with their
    role label so the LLM picks the right one -- measurably reduces
    invention without going as far as a structured-output / JSON-mode
    constraint that would also raise the bar for legitimate creative
    coordination text.

    Only emitted at depth 0; children get the classic "stay in your
    lane" instruction.
    """

    if not spec.available_nodes:
        return ""
    lines = ["Available child nodes you may dispatch to (use the exact id):"]
    for node_id, label in spec.available_nodes:
        if label:
            lines.append(f"- {node_id}: {label}")
        else:
            lines.append(f"- {node_id}")
    lines.append(
        "Do NOT invent new node ids. If none of the listed nodes fits "
        "the user request, do the work yourself instead of dispatching."
    )
    return "\n".join(lines)


def _persona_system_prompt(spec: AgentSpec, *, depth: int = 0) -> str:
    """Compose the per-node system prompt from the resolved spec.

    Kept deliberately small (< 1 KB on a typical node) so single-shot
    calls don't blow the per-node token budget. The Sprint-4 dispatch
    tutorial is only spliced in at the entry level (``depth == 0``) --
    children get the classic Sprint-2 "stay in your lane" instruction,
    so even if a child's LLM decided to emit a ``<dispatch>`` tag the
    gate would skip it AND the prompt did not teach it the syntax in
    the first place.

    Sprint-5 P0-1 / unexpected-finding #1 splices the closed list of
    available child node ids in at depth 0 (after the dispatch
    tutorial) so the producer LLM dispatches to real targets only.
    """

    persona = (spec.persona or "").strip()
    role = (spec.role or "worker").strip()
    parts: list[str] = [
        _NODE_SYSTEM_PREFIX,
        f"You are running as node `{spec.node_id}` (role: {role}) "
        f"inside organisation `{spec.org_id}`.",
    ]
    if persona:
        parts.append(f"Persona: {persona}.")
    if depth == 0:
        parts.append(_dispatch_instructions())
        available_block = _available_nodes_block(spec)
        if available_block:
            parts.append(available_block)
    else:
        parts.append(
            "Reply directly to the user instruction below. Keep your "
            "answer focused on the node's role; do not pretend to "
            "dispatch sub-tasks to other nodes (multi-node coordination "
            "is handled by the orchestrator at the entry level, not by "
            "you)."
        )
    return "\n".join(parts)


def _extract_text_from_response(resp: Any) -> str:
    """Pull a plain-text reply out of the Anthropic-shaped ``Message``.

    Mirrors the loose duck-type the desktop ``Agent`` uses elsewhere:
    we walk ``content`` blocks looking for ``.text``; if nothing
    surfaces we fall back to ``str(resp)`` so the executor still sees a
    non-empty output rather than ``None`` (the ``_invoke_agent``
    contract).
    """

    content = getattr(resp, "content", None)
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                chunks.append(text)
                continue
            block_type = getattr(block, "type", "")
            if block_type == "text":
                value = getattr(block, "value", None)
                if isinstance(value, str):
                    chunks.append(value)
        if chunks:
            return "\n".join(chunks).strip()
    if isinstance(content, str):
        return content.strip()
    return str(resp).strip()


def parse_dispatch_blocks(text: str) -> list[tuple[str, str]]:
    """Extract ``(target_node_id, child_content)`` pairs from an LLM reply.

    Returns at most :data:`MAX_DISPATCH_BLOCKS` pairs in the order they
    appear (LLM-ordered, not alphabetised: ordering carries intent
    when the model says "first ask screenwriter, then art-director").
    Target / content are stripped of leading and trailing whitespace
    because regex captures preserve newlines around the tags. Empty
    targets are filtered out so a malformed ``<dispatch target="">`` is
    skipped instead of being forwarded to a non-existent node.
    """

    if not isinstance(text, str) or "<dispatch" not in text.lower():
        return []
    pairs: list[tuple[str, str]] = []
    for match in _DISPATCH_RE.finditer(text):
        target = (match.group(1) or "").strip()
        content = (match.group(2) or "").strip()
        if not target:
            continue
        pairs.append((target, content))
        if len(pairs) >= MAX_DISPATCH_BLOCKS:
            break
    return pairs


def _strip_dispatch_blocks(text: str) -> str:
    """Replace each ``<dispatch>...</dispatch>`` block with a short marker.

    The original LLM text is preserved verbatim around the dispatch
    blocks so the parent's "coordination commentary" still surfaces to
    the user. Pre-fix attempts to keep the raw blocks made the
    aggregated output illegible (the user saw the XML on screen and
    then the same child reply repeated underneath); the marker makes
    the flow obvious ("here producer asked X, here is X's reply").
    """

    if not isinstance(text, str) or "<dispatch" not in text.lower():
        return text

    def _sub(match: re.Match[str]) -> str:
        target = (match.group(1) or "").strip() or "?"
        return f"[dispatched to {target}]"

    return _DISPATCH_RE.sub(_sub, text)


def _aggregate_with_children(
    parent_text: str,
    children: list[tuple[str, str]],
) -> str:
    """Combine the parent's stripped text with serial child outputs.

    The aggregation format is intentionally markdown-light so it shows
    up readably in chat UIs that escape HTML but render newlines. Each
    child is fenced by its node id so the user can tell which voice is
    speaking even when several children replied. Empty child outputs
    are kept (with an explicit ``(no output)`` placeholder) because the
    absence of output is itself signal -- pre-fix we silently dropped
    empties and the user could not tell whether the child failed or
    just had nothing to say.
    """

    parent_stripped = _strip_dispatch_blocks(parent_text).strip()
    sections: list[str] = []
    if parent_stripped:
        sections.append(parent_stripped)
    for target, output in children:
        body = (output or "").strip() or "(no output)"
        sections.append(f"[from node `{target}`]\n{body}")
    return "\n\n".join(sections)


class _BrainBackedNodeAgent:
    """Single-shot LLM agent for one orgs_v2 node.

    Implements the
    ``_runtime_agent_pipeline_executor._AgentRunCallable`` Protocol
    (``async run(content) -> Any``). The executor handles the rest of
    the v1-parity event lifecycle.

    Sprint-4 P0-1: when an injected ``dispatch_callback`` is wired and
    the LLM emits ``<dispatch target="...">...</dispatch>`` blocks,
    :meth:`run` calls the callback once per block (serially), then
    aggregates the children outputs into the returned text. Recursion
    depth is controlled by :data:`dispatch_depth_var` (set by the
    executor) and capped at :data:`MAX_DISPATCH_DEPTH`.

    Sprint-5 P0-1: when the resolved :class:`AgentSpec` carries an
    ``external_tools`` whitelist that maps to at least one known
    handler in :data:`openakita.tools.handlers.default_handler_registry`,
    :meth:`run` swaps the empty Sprint-4 ``tools=[]`` brain call for a
    real one-round tool-use loop (see
    :func:`._runtime_node_tools.run_with_tools`). The dispatch parser
    still runs after the loop so a node can both call a tool *and*
    dispatch to siblings in the same turn (the LLM's text after the
    tool round may contain ``<dispatch>`` blocks too).
    """

    __slots__ = ("_spec", "_brain", "_dispatch_callback", "_event_emitter")

    def __init__(
        self,
        spec: AgentSpec,
        brain: Any,
        *,
        dispatch_callback: DispatchCallback | None = None,
        event_emitter: NodeToolEmit | None = None,
    ) -> None:
        self._spec = spec
        self._brain = brain
        self._dispatch_callback = dispatch_callback
        self._event_emitter = event_emitter

    async def run(self, content: str) -> str:
        text = content if isinstance(content, str) else str(content or "")
        if not text.strip():
            # Empty content shouldn't land here (command_service rejects
            # blank submits) but be defensive: a noop reply keeps the
            # executor's "ok" path reachable.
            return ""
        depth = max(0, int(dispatch_depth_var.get(0)))
        # Sprint-5 P0-1: resolve the per-node tools whitelist *before*
        # we tag the trace context so the LLM debug dump can carry an
        # accurate ``tools_count``. Workbench nodes that disable file
        # tools (``enable_file_tools=False``) still get an empty list
        # when their ``external_tools`` are plugin-only and not yet
        # bridged into ``default_handler_registry`` -- consistent with
        # the "out of scope: workbench plugin wiring" note on the
        # module docstring.
        tool_defs = resolve_node_tools(
            external_tools=self._spec.external_tools,
            enable_file_tools=self._spec.enable_file_tools,
        )
        # Tag the brain's debug dump with the node identity + tool
        # count so the v13 audit's "0 orgs_v2 LLM files" finding stays
        # verifiable AND the v17 audit can confirm tools_count > 0 on
        # workbench dispatch.
        set_trace = getattr(self._brain, "set_trace_context", None)
        if callable(set_trace):
            try:
                set_trace(
                    {
                        "org_id": self._spec.org_id,
                        "node_id": self._spec.node_id,
                        "caller": "orgs_v2_node_agent",
                        "tools_count": str(len(tool_defs)),
                    }
                )
            except Exception:  # noqa: BLE001 -- trace tagging is best-effort
                pass
        system_prompt = _persona_system_prompt(self._spec, depth=depth)

        # Sprint-5 P0-1: branch on whether the node has any resolved
        # tools. Zero-tool nodes still use the Sprint-4 single-shot
        # call (no risk of an unintended provider feature flip when
        # ``tools`` is an empty list vs absent); >0-tool nodes go
        # through the one-round tool-use loop helper.
        from ._runtime_agent_pipeline import current_command_id_var

        command_id_for_events = current_command_id_var.get("") or None
        if tool_defs:
            response, _rounds = await run_with_tools(
                brain=self._brain,
                system_prompt=system_prompt,
                user_content=text,
                tools=tool_defs,
                org_id=self._spec.org_id,
                node_id=self._spec.node_id,
                command_id=command_id_for_events,
                emit=self._event_emitter,
            )
        else:
            response = await self._brain.messages_create_async(
                messages=[{"role": "user", "content": text}],
                system=system_prompt,
                tools=[],
            )
        parent_text = _extract_text_from_response(response)

        # Sprint-4 P0-1: parse + recurse on child dispatch blocks.
        # Skip if the dispatch callback is not wired (unit tests /
        # bare-builder users get exactly the Sprint-3 behaviour) or
        # the current depth is at the cap (recursion would exceed
        # MAX_DISPATCH_DEPTH).
        if self._dispatch_callback is None or depth >= MAX_DISPATCH_DEPTH - 1:
            return parent_text
        blocks = parse_dispatch_blocks(parent_text)
        if not blocks:
            return parent_text

        children: list[tuple[str, str]] = []
        for child_target, child_content in blocks:
            try:
                child_output = await self._dispatch_callback(
                    org_id=self._spec.org_id,
                    parent_node_id=self._spec.node_id,
                    child_node_id=child_target,
                    child_content=child_content,
                )
            except Exception as exc:  # noqa: BLE001 -- one child failure
                # must not poison siblings or the parent's reply. The
                # executor inside the callback already emitted
                # ``agent_run_failed`` / persisted artefacts for the
                # surviving children; here we surface the failure
                # textually so the aggregated reply still shows the
                # parent "tried" all branches.
                _LOGGER.warning(
                    "child dispatch failed (parent=%s target=%s): %s",
                    self._spec.node_id,
                    child_target,
                    exc,
                )
                child_output = f"[child dispatch failed: {exc}]"
            children.append((child_target, child_output or ""))
        return _aggregate_with_children(parent_text, children)


class DefaultAgentBuilder:
    """Production :class:`AgentBuilderProtocol` (Sprint-2 P0-1).

    The builder is constructed by the API server lifespan
    (``api/server.py`` ``create_app``) before the desktop ``Agent`` is
    available. To handle that ordering without ``app.state`` reach-ins
    inside the orgs subsystem, callers pass a ``brain_provider``
    callable that the builder dereferences each :meth:`build` -- the
    desktop ``Agent`` is wired into ``app.state.agent`` later by
    ``main.py`` and the closure picks it up on first use.

    Sprint-4 P0-1: optionally accepts a ``dispatch_callback`` that the
    built :class:`_BrainBackedNodeAgent` invokes when the LLM emits
    ``<dispatch target="...">...</dispatch>`` blocks. The callback is
    wired by the runtime composition root to point back at
    :meth:`AgentPipelineExecutor.dispatch_subtask`. Leaving it as
    ``None`` (the default) restores Sprint-3 behaviour byte-for-byte,
    so unit tests / parity gates that instantiate the builder
    standalone keep working without changes.
    """

    def __init__(
        self,
        *,
        brain_provider: Callable[[], Any],
        dispatch_callback: DispatchCallback | None = None,
        event_emitter: NodeToolEmit | None = None,
    ) -> None:
        if not callable(brain_provider):
            raise TypeError("brain_provider must be callable")
        if dispatch_callback is not None and not callable(dispatch_callback):
            raise TypeError("dispatch_callback must be callable when provided")
        if event_emitter is not None and not callable(event_emitter):
            raise TypeError("event_emitter must be callable when provided")
        self._brain_provider = brain_provider
        self._dispatch_callback = dispatch_callback
        self._event_emitter = event_emitter

    def build(self, spec: AgentSpec) -> Any:
        try:
            brain = self._brain_provider()
        except Exception as exc:  # noqa: BLE001 -- propagate as builder-unavailable
            raise BuilderUnavailable(
                f"brain_provider raised: {type(exc).__name__}: {exc}"
            ) from exc
        if brain is None:
            raise BuilderUnavailable(
                "main agent brain not yet initialised "
                f"(org={spec.org_id} node={spec.node_id}); "
                "the API loop came up before the desktop Agent finished "
                "wiring -- retry the command in a moment"
            )
        # Sanity: the brain must expose ``messages_create_async``;
        # alternative LLM frontends will need their own builder until
        # the multi-node sprint introduces a richer adapter layer.
        if not hasattr(brain, "messages_create_async"):
            raise BuilderUnavailable(
                f"brain of type {type(brain).__name__} has no "
                "messages_create_async; cannot bind orgs_v2 node "
                f"(org={spec.org_id} node={spec.node_id})"
            )
        _LOGGER.debug(
            "DefaultAgentBuilder built node agent (org=%s node=%s role=%s persona=%s)",
            spec.org_id,
            spec.node_id,
            spec.role,
            (spec.persona or "")[:40],
        )
        return _BrainBackedNodeAgent(
            spec,
            brain,
            dispatch_callback=self._dispatch_callback,
            event_emitter=self._event_emitter,
        )

    def teardown(self, agent: Any) -> None:  # noqa: ARG002
        # Brain references are shared with the main desktop Agent; we do
        # not own its lifecycle. The cache evicts node agents but nothing
        # downstream needs explicit cleanup here.
        return None
