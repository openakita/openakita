"""Default ``AgentBuilderProtocol`` -- minimum viable LLM binding for orgs_v2.

Sprint-2 P0-1 (audit ``_orgs_business_capability_audit_v2.md`` §5 / §8):
the v13 business-capability run showed every orgs_v2 command bouncing
off ``_NullAgentBuilder`` with a ``RuntimeError`` -- 60+ commands, 0 LLM
calls, 0 artefacts. The wire-up from H3 (executor injection) was correct
but pointed at an empty default. This module ships the smallest possible
real builder so a node can produce **at least one line of LLM text**:

* :class:`DefaultAgentBuilder` -- builds one
  :class:`_BrainBackedNodeAgent` per (org_id, node_id), reusing the main
  chat ``Brain`` instance via a lazy provider so we don't reach into
  ``app.state`` at builder-construction time (the API lifespan composes
  the runtime *before* the desktop ``Agent`` is instantiated).
* :class:`_BrainBackedNodeAgent` -- has the
  ``_runtime_agent_pipeline_executor._AgentRunCallable`` shape:
  ``async run(content) -> str``. Internally it feeds a single
  user-message + persona-derived system prompt to
  ``Brain.messages_create_async`` and extracts the text content.

**Out of scope** (intentionally deferred to next sprint, see audit §8 P1):

* multi-node dispatcher / aggregator / delegation logging (D3 / D4 / D5)
* tool / skill / MCP injection per node (the main chat Brain still owns
  the 76-tool catalogue; node agents stay tool-free for now)
* persistent agent identity (no SOUL.md / AGENT.md / USER.md layering --
  builders compose a single-shot system prompt from
  ``AgentSpec.persona`` + ``role`` + an "executing on behalf of org X
  node Y" framing)
* prompt-budget / context-window management (the node prompts are tiny
  by construction; budgeting is part of the multi-node sprint)

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
from collections.abc import Callable
from typing import Any

from ._runtime_agent_pipeline import AgentSpec

__all__ = [
    "BuilderUnavailable",
    "DefaultAgentBuilder",
]

_LOGGER = logging.getLogger(__name__)

# A short marker prepended to every node system prompt so logs / debug
# dumps clearly attribute the LLM call to the orgs_v2 path (the v13
# audit's L4.1 finding: 0 LLM debug files were tagged with orgs_v2).
_NODE_SYSTEM_PREFIX = "[openakita orgs_v2 node agent]"


class BuilderUnavailable(RuntimeError):
    """Raised by :class:`DefaultAgentBuilder.build` when the brain provider
    returns ``None`` (lifespan startup race / desktop Agent not yet ready).

    The executor catches this and emits the v1-parity
    ``agent_run_failed reason=agent_build_failed`` event, identical to
    what the legacy ``_NullAgentBuilder`` produced. Naming the exception
    distinctly makes log triage easier.
    """


def _persona_system_prompt(spec: AgentSpec) -> str:
    """Compose the per-node system prompt from the resolved spec.

    Kept deliberately small (< 500 chars) so a single-shot "echo + LLM"
    call doesn't blow the per-node token budget. Multi-line layered
    identity composition is the multi-node sprint's job.
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
    parts.append(
        "Reply directly to the user instruction below. Keep your answer "
        "focused on the node's role; do not pretend to dispatch sub-tasks "
        "to other nodes (multi-node coordination is handled by the "
        "orchestrator, not by you)."
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


class _BrainBackedNodeAgent:
    """Single-shot LLM agent for one orgs_v2 node.

    Implements the
    ``_runtime_agent_pipeline_executor._AgentRunCallable`` Protocol
    (``async run(content) -> Any``). The executor handles the rest of
    the v1-parity event lifecycle.
    """

    __slots__ = ("_spec", "_brain", "_system_prompt")

    def __init__(self, spec: AgentSpec, brain: Any) -> None:
        self._spec = spec
        self._brain = brain
        self._system_prompt = _persona_system_prompt(spec)

    async def run(self, content: str) -> str:
        text = content if isinstance(content, str) else str(content or "")
        if not text.strip():
            # Empty content shouldn't land here (command_service rejects
            # blank submits) but be defensive: a noop reply keeps the
            # executor's "ok" path reachable.
            return ""
        # Tag the brain's debug dump with the node identity so the v13
        # audit's "0 orgs_v2 LLM files" finding becomes verifiable on the
        # next exploratory pass.
        set_trace = getattr(self._brain, "set_trace_context", None)
        if callable(set_trace):
            try:
                set_trace(
                    {
                        "org_id": self._spec.org_id,
                        "node_id": self._spec.node_id,
                        "caller": "orgs_v2_node_agent",
                    }
                )
            except Exception:  # noqa: BLE001 -- trace tagging is best-effort
                pass
        messages = [
            {
                "role": "user",
                "content": text,
            }
        ]
        # No tools are passed: nodes run the minimal viable LLM call for
        # Sprint-2 P0-1. Tool / skill injection is the next sprint's job.
        response = await self._brain.messages_create_async(
            messages=messages,
            system=self._system_prompt,
            tools=[],
        )
        return _extract_text_from_response(response)


class DefaultAgentBuilder:
    """Production :class:`AgentBuilderProtocol` (Sprint-2 P0-1).

    The builder is constructed by the API server lifespan
    (``api/server.py`` ``create_app``) before the desktop ``Agent`` is
    available. To handle that ordering without ``app.state`` reach-ins
    inside the orgs subsystem, callers pass a ``brain_provider``
    callable that the builder dereferences each :meth:`build` -- the
    desktop ``Agent`` is wired into ``app.state.agent`` later by
    ``main.py`` and the closure picks it up on first use.
    """

    def __init__(self, *, brain_provider: Callable[[], Any]) -> None:
        if not callable(brain_provider):
            raise TypeError("brain_provider must be callable")
        self._brain_provider = brain_provider

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
        return _BrainBackedNodeAgent(spec, brain)

    def teardown(self, agent: Any) -> None:  # noqa: ARG002
        # Brain references are shared with the main desktop Agent; we do
        # not own its lifecycle. The cache evicts node agents but nothing
        # downstream needs explicit cleanup here.
        return None
