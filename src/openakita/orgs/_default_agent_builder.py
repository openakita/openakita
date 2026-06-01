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

import asyncio
import inspect
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
    NodeToolHostProvider,
    resolve_node_tools,
    run_with_tools,
)

__all__ = [
    "MAX_DISPATCH_BLOCKS",
    "MAX_DISPATCH_DEPTH",
    "BuilderUnavailable",
    "DefaultAgentBuilder",
    "DispatchCallback",
    "NodeToolEmit",
    "NodeToolHostProvider",
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


def _callable_accepts_kwarg(fn: Callable[..., Any], name: str) -> bool:
    """Detect whether ``fn`` accepts a keyword argument named ``name``.

    Sprint-13 H1: we plumb ``cancel_event`` into the dispatch callback so
    a user cancel reaches grandchildren. Old test fakes (e.g.
    ``_dispatch_subtask_cb`` in ``test_child_dispatch.py``) were written
    before the kwarg existed and have a closed signature; calling them
    with ``cancel_event=...`` would raise ``TypeError``. We probe the
    signature once per call site and only pass the kwarg when the
    callee actually declares it (or accepts ``**kwargs``). Failures of
    :func:`inspect.signature` (C-implemented callables, slot wrappers)
    default to ``True`` because such callables typically forward via
    ``**kwargs`` themselves and we cannot prove otherwise.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    params = sig.parameters
    if name in params:
        return True
    return any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


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
        "If you need specialist nodes to handle parts of the task, you "
        "may emit one or more dispatch blocks in your reply using EXACTLY "
        "this XML syntax: <dispatch target=\"NODE_ID\">instruction for "
        "that node</dispatch>. Use the literal node id (e.g. "
        "'screenwriter', 'art-director'). Emit at most "
        f"{MAX_DISPATCH_BLOCKS} dispatch blocks. "
        "IMPORTANT — work in parallel: when several subtasks are "
        "independent of each other (e.g. a long-form post, a short-video "
        "script, a storyboard and an SEO title can all be drafted at the "
        "same time), put ALL of their dispatch blocks in THIS SAME reply. "
        "The orchestrator runs every dispatch block in the same reply "
        "concurrently, so batching independent work is far faster than "
        "handing it off one node at a time. Only split work across "
        "separate replies when a later subtask truly depends on an "
        "earlier node's output. Do not nest dispatch blocks. After the "
        "dispatch blocks the orchestrator will append each child's output "
        "to your reply, so your own text should focus on coordination "
        "(which child does what and why)."
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


def _language_consistency_rule() -> str:
    """Force every node reply to match the ORIGINAL user request language.

    Exploratory testing v11 (UI issue #10): Chinese tasks were coming
    back with English deliverables because the English system prompt
    biased the model toward English. This single rule -- appended to
    EVERY node prompt regardless of depth/tools -- pins the output
    language to the user's ORIGINAL request rather than the immediate
    routing instruction. That distinction matters: the supervisor's
    progress-ledger prompt is a strict JSON contract we cannot safely
    add a language bullet to, so the orchestrator sometimes relays an
    English ``instruction_or_question`` even for a Chinese task. The
    original request (and upstream node outputs) are inlined into every
    node's context in the user's language, so anchoring on THAT keeps
    the whole deliverable Chinese even when one routing hop is English.
    """

    return (
        "Language policy (MANDATORY): Detect the natural language of the "
        "ORIGINAL user request / the task content provided in this "
        "conversation, and write your ENTIRE reply in THAT language. If the "
        "original task is in Chinese, respond fully in Chinese -- including "
        "all prose, headings, file contents and summaries -- EVEN IF a "
        "routing or coordination instruction you receive happens to be "
        "phrased in English. Only reply in English when the user's original "
        "request itself is in English. Never switch to English merely because "
        "this system prompt or a relayed instruction is in English."
    )


def _tool_use_encouragement() -> str:
    """Sprint-7 P0-B node-level tool-use encouragement.

    Audit ``_orgs_business_capability_audit_v7.md`` §1.1 + §5 finding 2:
    8 v18 R.D4 cases explicitly asked the dispatched node to call a
    specific tool (``write_file`` / ``read_file`` / ``web_search`` /
    ``list_dir`` / ``run_shell`` / ``web_fetch``), but only 3/8 LLM
    turns actually emitted a ``tool_use`` block -- the other 5 replied
    with plain text refusing or describing what they would do. The
    Sprint-6 system prompt only said "Reply directly to the user
    instruction below" which gives the LLM no reason to prefer the
    available tools over chat-style prose.

    This block is appended **only when the resolved node has at least
    one available tool**: we don't want the encouragement to hallucinate
    tools that the node's external_tools whitelist + plugin manifest
    did not actually expose. The phrasing is deliberately conservative
    (``SHOULD use`` not ``MUST use``) so the LLM still has the latitude
    to reply with text when no listed tool applies; the goal is to
    flip the default from "narrate then maybe call" to "call when a
    listed tool clearly fits".

    The "match by intent, not by exact wording" line addresses the v18
    observation that some Chinese prompts named the tool with a verb
    ("调用 list_dir") that the LLM treated as a label rather than an
    instruction.
    """

    return (
        "Tool-use policy: You have access to the tools listed below. "
        "When the user's request can be satisfied by invoking one of "
        "these tools (e.g. write/read a file, list a directory, run a "
        "shell command, search/fetch the web, generate or edit an image "
        "or video, query plugin functions, etc.), you SHOULD emit a "
        "`tool_use` block and call the tool instead of replying with "
        "plain text describing what you would do. Match the user's "
        "intent against the available tools by purpose, not by exact "
        "wording. Do not invent tool names that are not in the list; "
        "if none of the listed tools fits the request, reply with text "
        "explaining why."
    )


def _persona_system_prompt(
    spec: AgentSpec, *, depth: int = 0, has_tools: bool = False
) -> str:
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

    Sprint-7 P0-B (audit v7 §1.1 + §5 finding 2): when the node has
    at least one resolved tool, append :func:`_tool_use_encouragement`
    so the LLM treats the tool surface as the default action path
    instead of narrating around it. The flag is opt-in (defaults to
    ``False``) so unit tests / parity gates that build the prompt
    without a tool-host context keep the byte-for-byte Sprint-5 shape.
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
    if has_tools:
        parts.append(_tool_use_encouragement())
    parts.append(_language_consistency_rule())
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

    __slots__ = (
        "_spec",
        "_brain",
        "_dispatch_callback",
        "_event_emitter",
        "_tool_host_provider",
    )

    def __init__(
        self,
        spec: AgentSpec,
        brain: Any,
        *,
        dispatch_callback: DispatchCallback | None = None,
        event_emitter: NodeToolEmit | None = None,
        tool_host_provider: NodeToolHostProvider | None = None,
    ) -> None:
        self._spec = spec
        self._brain = brain
        self._dispatch_callback = dispatch_callback
        self._event_emitter = event_emitter
        self._tool_host_provider = tool_host_provider

    async def run(
        self,
        content: str,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        # Sprint-13 H1 (RC-4 §6): ``cancel_event`` is the asyncio event
        # the supervisor wires from its ``cancel_token``. We forward it
        # straight to ``brain.messages_create_async`` (via ``run_with_tools``
        # for the tool-call path) so :meth:`LLMClient._race_with_cancel`
        # can abort the in-flight ``httpx`` request the instant a user
        # cancel fires. Child dispatches also receive the same event so
        # the cancel propagates through ``<dispatch>`` recursion without
        # leaving long-running grandchildren stranded.
        text = content if isinstance(content, str) else str(content or "")
        if not text.strip():
            # Empty content shouldn't land here (command_service rejects
            # blank submits) but be defensive: a noop reply keeps the
            # executor's "ok" path reachable.
            return ""
        depth = max(0, int(dispatch_depth_var.get(0)))
        # Sprint-6 P0-1 / P0-3: resolve the node-bound :class:`NodeToolHost`
        # via the lazy provider. When the desktop Agent has finished wiring
        # the host gives us access to the populated handler registry +
        # plugin tool catalog. When it has not we keep the Sprint-5
        # fallback path (empty registry -> failed events), which the
        # integration tests document explicitly.
        tool_host = None
        if self._tool_host_provider is not None:
            try:
                tool_host = self._tool_host_provider()
            except Exception:  # noqa: BLE001 -- provider must not crash run
                tool_host = None
        # Sprint-5 P0-1 (extended Sprint-6 P0-3): resolve the per-node
        # tools whitelist *before* we tag the trace context so the LLM
        # debug dump can carry an accurate ``tools_count``. The host
        # lookup includes plugin tools (``hh_*``) so workbench nodes
        # (``wb-hh-*``) finally see their declared whitelist instead
        # of having it silently dropped (Sprint-5 §3 limitation).
        tool_defs = resolve_node_tools(
            external_tools=self._spec.external_tools,
            enable_file_tools=self._spec.enable_file_tools,
            tool_host=tool_host,
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
        system_prompt = _persona_system_prompt(
            self._spec, depth=depth, has_tools=bool(tool_defs)
        )

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
                tool_host=tool_host,
                cancel_event=cancel_event,
            )
            parent_text = _extract_text_from_response(response)
        else:
            # No-tools (writer/leaf) path. Stream the long-form reply token by
            # token into the 编排过程 timeline (``node_run_delta`` events) so the
            # executing entry rolls characters live instead of appearing only
            # when the node finishes. Streaming is best-effort with a hard
            # fallback: any failure (provider can't stream, empty text, error)
            # transparently drops back to the original non-streaming call so
            # billing / failover / debug-dump behaviour is never weaker than
            # before.
            streamed_text, streamed_ok = await self._run_no_tools_streaming(
                system_prompt=system_prompt,
                text=text,
                command_id=command_id_for_events,
                cancel_event=cancel_event,
            )
            if streamed_ok:
                parent_text = streamed_text
            else:
                response = await self._brain.messages_create_async(
                    messages=[{"role": "user", "content": text}],
                    system=system_prompt,
                    tools=[],
                    cancel_event=cancel_event,
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

        dispatch_accepts_cancel = _callable_accepts_kwarg(
            self._dispatch_callback, "cancel_event"
        )

        async def _run_one(child_target: str, child_content: str) -> str:
            try:
                # Sprint-13 H1: forward ``cancel_event`` into child
                # dispatch so a user cancel terminates grandchildren
                # without waiting for the parent to finish its outer
                # await frame. Old test callbacks (audit
                # ``tests/runtime/orgs/test_child_dispatch.py``) have a
                # closed signature without ``cancel_event``; the probe
                # above keeps them working.
                if dispatch_accepts_cancel:
                    child_output = await self._dispatch_callback(
                        org_id=self._spec.org_id,
                        parent_node_id=self._spec.node_id,
                        child_node_id=child_target,
                        child_content=child_content,
                        cancel_event=cancel_event,
                    )
                else:
                    child_output = await self._dispatch_callback(
                        org_id=self._spec.org_id,
                        parent_node_id=self._spec.node_id,
                        child_node_id=child_target,
                        child_content=child_content,
                    )
                return child_output or ""
            except asyncio.CancelledError:
                # A user cancel must unwind the whole tree -- re-raise so
                # ``gather`` cancels the surviving siblings too (Sprint-3
                # P0-2 cancel pipeline stays intact under fan-out).
                raise
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
                return f"[child dispatch failed: {exc}]"

        # UI issue #9: fan the sibling dispatches out **concurrently** via
        # ``asyncio.gather`` instead of awaiting them one-by-one. This is the
        # root of the "一次只一个节点、一跳一跳" complaint -- e.g. the editor-in-chief
        # delegating to writer-a / writer-b / visual / seo now runs them in
        # parallel rather than serially. Convergence is preserved (we still
        # await every child before aggregating) and ``gather`` keeps results in
        # the original dispatch order, so ``_aggregate_with_children`` produces
        # byte-identical aggregation to the old serial path. The block count is
        # already bounded by ``MAX_DISPATCH_BLOCKS`` so the fan-out can't run
        # away. Per-org isolation is unchanged (each child still routes through
        # its own executor + node agent under the same org).
        results = await asyncio.gather(
            *(_run_one(t, c) for t, c in blocks)
        )
        children: list[tuple[str, str]] = [
            (blocks[i][0], results[i]) for i in range(len(blocks))
        ]
        return _aggregate_with_children(parent_text, children)

    # ------------------------------------------------------------------
    # Token-level streaming for the no-tools (writer/leaf) path
    # ------------------------------------------------------------------

    async def _run_no_tools_streaming(
        self,
        *,
        system_prompt: str,
        text: str,
        command_id: str | None,
        cancel_event: asyncio.Event | None,
    ) -> tuple[str, bool]:
        """Stream a no-tools reply, emitting ``node_run_delta`` increments.

        Returns ``(final_text, ok)``. ``ok=False`` means the caller MUST fall
        back to the non-streaming :meth:`messages_create_async` path -- this
        is the isolation contract that keeps billing / failover / debug-dump
        intact when streaming is unavailable or fails mid-flight.

        We use ``brain.messages_create_stream`` (not the bare ``stream_chat``
        primitive) on purpose: the legacy stream method writes the LLM debug
        dump and pushes a ``TokenTrackingContext`` exactly like the
        non-streaming call, so the only billing concern left to us is
        recording the final ``usage`` (done in :meth:`_record_stream_usage`).
        ``CancelledError`` is re-raised unchanged so the Sprint-3 cancel
        pipeline still aborts a streaming node.
        """
        emit = self._event_emitter
        stream_factory = getattr(self._brain, "messages_create_stream", None)
        if emit is None or not callable(stream_factory):
            return "", False

        import time as _time

        try:
            from openakita.core.stream_accumulator import StreamAccumulator
        except Exception:  # noqa: BLE001 -- accumulator import must not break run
            return "", False

        acc = StreamAccumulator()
        seq = 0
        last_emit = 0.0
        had_delta = False

        async def _emit(*, done: bool) -> None:
            nonlocal seq, last_emit
            seq += 1
            last_emit = _time.monotonic()
            payload = {
                "org_id": self._spec.org_id,
                "node_id": self._spec.node_id,
                "command_id": command_id,
                # Frontend accumulates ``text``; ``delta`` is informational.
                # Cap the rolling preview so a multi-KB reply can't bloat a
                # single SSE frame (the final artifact carries the full text).
                "text": (acc.text_content or "")[:8000],
                "seq": seq,
                "done": done,
            }
            try:
                res = emit("node_run_delta", payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001 -- a dropped delta must not abort the run
                pass

        stream = None
        try:
            stream = stream_factory(
                messages=[{"role": "user", "content": text}],
                system=system_prompt,
                tools=[],
                conversation_id=command_id or "",
            )
            async for raw in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise asyncio.CancelledError()
                if not isinstance(raw, dict):
                    continue
                if raw.get("type") == "endpoint_meta":
                    continue
                produced = False
                for hi in acc.feed(raw):
                    if hi.get("type") == "text_delta" and hi.get("content"):
                        produced = True
                        had_delta = True
                if produced and (_time.monotonic() - last_emit) >= 0.1:
                    await _emit(done=False)
        except asyncio.CancelledError:
            # Close the underlying stream so the httpx response is released,
            # then propagate so the cancel pipeline unwinds the node.
            if stream is not None:
                aclose = getattr(stream, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:  # noqa: BLE001
                        pass
            raise
        except Exception as exc:  # noqa: BLE001 -- any stream error -> fallback
            _LOGGER.warning(
                "orgs_v2 no-tools streaming failed (node=%s); falling back to "
                "non-streaming path: %s",
                self._spec.node_id,
                exc,
            )
            return "", False

        final_text = (acc.text_content or "").strip()
        if not final_text or not had_delta:
            # Nothing usable streamed -> let the caller do the resilient
            # non-streaming call (which has full multi-endpoint failover).
            return "", False

        # Billing: messages_create_stream set the tracking context but leaves
        # usage recording to the caller (see its docstring). Record it now so
        # streamed nodes are accounted exactly like non-streamed ones.
        self._record_stream_usage(acc)
        # Final marker so the frontend can settle the rolling entry.
        await _emit(done=True)
        return final_text, True

    def _record_stream_usage(self, acc: Any) -> None:
        """Record token usage from a finished stream into the brain accumulator.

        Best-effort and fail-silent: builds a tiny usage-only response object
        (``brain._record_usage`` only reads ``usage`` / ``model`` /
        ``endpoint_name``) so we don't depend on the full ``LLMResponse``
        constructor. A missing usage block simply records nothing rather than
        raising.
        """
        try:
            usage = getattr(acc, "usage", None) or {}
            in_tok = int(usage.get("input_tokens", 0) or 0)
            out_tok = int(usage.get("output_tokens", 0) or 0)
            if in_tok == 0 and out_tok == 0:
                return
            record = getattr(self._brain, "_record_usage", None)
            if not callable(record):
                return
            from openakita.llm.types import Usage

            u = Usage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_creation_input_tokens=int(
                    usage.get("cache_creation_input_tokens", 0) or 0
                ),
                cache_read_input_tokens=int(
                    usage.get("cache_read_input_tokens", 0) or 0
                ),
            )
            info: dict[str, Any] = {}
            try:
                info = self._brain.get_current_endpoint_info() or {}
            except Exception:  # noqa: BLE001
                info = {}

            class _UsageOnly:
                __slots__ = ("usage", "model", "endpoint_name")

                def __init__(self) -> None:
                    self.usage = u
                    self.model = info.get("model", "") or ""
                    self.endpoint_name = info.get("name", "") or ""

            record(_UsageOnly())
        except Exception:  # noqa: BLE001 -- billing record is best-effort
            _LOGGER.debug("orgs_v2 stream usage record failed", exc_info=True)


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
        tool_host_provider: NodeToolHostProvider | None = None,
    ) -> None:
        if not callable(brain_provider):
            raise TypeError("brain_provider must be callable")
        if dispatch_callback is not None and not callable(dispatch_callback):
            raise TypeError("dispatch_callback must be callable when provided")
        if event_emitter is not None and not callable(event_emitter):
            raise TypeError("event_emitter must be callable when provided")
        if tool_host_provider is not None and not callable(tool_host_provider):
            raise TypeError(
                "tool_host_provider must be callable when provided"
            )
        self._brain_provider = brain_provider
        self._dispatch_callback = dispatch_callback
        self._event_emitter = event_emitter
        self._tool_host_provider = tool_host_provider

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
            tool_host_provider=self._tool_host_provider,
        )

    def teardown(self, agent: Any) -> None:  # noqa: ARG002
        # Brain references are shared with the main desktop Agent; we do
        # not own its lifecycle. The cache evicts node agents but nothing
        # downstream needs explicit cleanup here.
        return None
