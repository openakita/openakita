"""V2 :class:`Agent` -- canonical home for the top-level Agent class.

Per ADR-0001 (fork-style rewrite), ADR-0003 (agent/ packaging), and
continuation plan section 7 (P-RC-6), this is the v2 canonical home
for the top-level Agent lifecycle (``__init__`` -> ``run_task`` ->
``shutdown``). The legacy ~9200 LOC ``Agent`` class lives at
:mod:`openakita.core._agent_legacy` and is the source of inherited
behaviour during the cutover; the v2 :class:`Agent` here subclasses
it and wires a :class:`StateGraph` on top to make the high-level
lifecycle explicit, plus exposes v2-native entry points that route
through the extracted helpers under ``runtime/desktop/`` and
``agent/safety/``.

Honest scope (G-RC-6 review): this is **not** a from-scratch rewrite
of the 9000+ LOC class.  Backward compatibility is byte-faithful via
inheritance; the v2 additions are

1.  :attr:`lifecycle_graph` / :meth:`route_lifecycle` / :meth:`describe_lifecycle`
    -- the explicit StateGraph driver for the Agent lifecycle, which
    callers can introspect or extend without subclassing the legacy
    god-class;
2.  :meth:`classify_inbound_risk` -- v2-native pre-LLM risk classifier
    routed through :func:`openakita.agent.safety.destructive_intent.classify_risk_intent`;
3.  :meth:`build_destructive_question` -- v2-native confirmation prompt
    builder routed through
    :func:`openakita.agent.safety.destructive_intent.build_destructive_intent_question`;
4.  :meth:`format_attachment_reference` -- v2-native attachment text
    formatter routed through
    :func:`openakita.runtime.desktop.attachments.format_desktop_attachment_reference`;
5.  :meth:`should_skip_risk_gate` -- composed trust-mode +
    trusted-path skip decision, returning a structured tuple instead
    of the legacy two-call sequence.

Legacy ``__init__`` / ``run_task`` / ``chat`` / ``shutdown`` and the
~120 deep methods are still inherited untouched; their migration to
real v2 implementations is tracked for P-RC-7 / P-RC-8.

Routing back-compat: ``from openakita.core.agent import Agent`` still
works -- the ``core/agent.py`` shim re-exports both the v2 :class:`Agent`
defined here (when called through the v2 surface) and the legacy
class via ``_agent_legacy`` fallback.
"""

from __future__ import annotations

import logging
from typing import Any

from openakita.agent.safety.destructive_intent import (
    build_destructive_intent_question,
    check_trust_mode_skip,
    check_trusted_path_skip,
    classify_risk_intent,
)
from openakita.core._agent_legacy import (
    Agent as _LegacyAgent,
)
from openakita.core._agent_legacy import (
    PromptStrategy,
    get_primary_agent,
    set_primary_agent,
)
from openakita.core.risk_intent import RiskIntentResult
from openakita.runtime.desktop.attachments import (
    format_desktop_attachment_reference,
    maybe_inline_local_image,
)
from openakita.runtime.state_graph import END, START, StateGraph

__all__ = [
    "Agent",
    "PromptStrategy",
    "RiskGateDecision",
    "build_agent_lifecycle_graph",
    "get_primary_agent",
    "set_primary_agent",
]

logger = logging.getLogger(__name__)

# Lifecycle node ids (high-level Agent state machine).
NODE_INIT = "init"
NODE_VALIDATE = "validate_input"
NODE_RISK_GATE = "classify_risk"
NODE_RUN_LOOP = "run_loop"
NODE_FINALIZE = "finalize"
NODE_ERROR = "error"

# Routing labels emitted by RiskGate -> next-node mapping.
_RISK_LABEL_TO_NODE: dict[str, str] = {
    "skip": NODE_RUN_LOOP,           # trust mode / trusted path / session grant
    "confirm": NODE_FINALIZE,        # confirmation question surfaced to user
    "run": NODE_RUN_LOOP,            # default low-risk path
    "abort": NODE_ERROR,             # classification failure / unrecoverable
}


class RiskGateDecision:
    """Lightweight result bag for :meth:`Agent.should_skip_risk_gate`.

    Three fields:

    - ``label``: one of ``"skip" | "confirm" | "run" | "abort"``,
      maps to the next lifecycle node via :data:`_RISK_LABEL_TO_NODE`.
    - ``reason``: human-readable reason string (e.g. ``"trust_mode"``,
      ``"trusted_workspace_path"``, ``"session_grant"``); ``None`` for
      the default ``run`` label.
    - ``classification``: the :class:`RiskIntentResult` returned by the
      deep classifier; preserved verbatim for downstream confirmation
      prompts.
    """

    __slots__ = ("label", "reason", "classification")

    def __init__(
        self,
        label: str,
        reason: str | None,
        classification: RiskIntentResult | None,
    ) -> None:
        self.label = label
        self.reason = reason
        self.classification = classification

    def __repr__(self) -> str:
        return (
            f"RiskGateDecision(label={self.label!r}, reason={self.reason!r}, "
            f"classification={self.classification!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RiskGateDecision):
            return NotImplemented
        return (
            self.label == other.label
            and self.reason == other.reason
            and self.classification == other.classification
        )

    @property
    def next_node(self) -> str:
        """Look up the lifecycle node id this decision routes to."""
        return _RISK_LABEL_TO_NODE.get(self.label, NODE_RUN_LOOP)


def build_agent_lifecycle_graph() -> StateGraph:
    """Construct the canonical Agent-lifecycle :class:`StateGraph`.

    Topology: ``START -> init -> validate_input -> classify_risk``;
    ``classify_risk`` branches into ``run_loop`` / ``finalize`` /
    ``error`` based on a :class:`RiskGateDecision` label; ``run_loop
    -> finalize -> END``; ``error -> finalize -> END``.

    The graph is purely a routing description / introspection point
    for v2 callers; the actual execution still goes through
    ``_LegacyAgent.run_task``. Migrating the legacy ladder onto this
    graph is tracked for P-RC-7+.
    """
    g = StateGraph()
    for node in (
        NODE_INIT,
        NODE_VALIDATE,
        NODE_RISK_GATE,
        NODE_RUN_LOOP,
        NODE_FINALIZE,
        NODE_ERROR,
    ):
        g.add_node(node)
    g.add_edge(START, NODE_INIT)
    g.add_edge(NODE_INIT, NODE_VALIDATE)
    g.add_edge(NODE_VALIDATE, NODE_RISK_GATE)

    def _route_from_risk_gate(result: Any) -> str:
        if isinstance(result, RiskGateDecision):
            return result.next_node
        # Defensive: unknown result types route to the safe default.
        return NODE_RUN_LOOP

    g.add_conditional_edges(
        NODE_RISK_GATE,
        _route_from_risk_gate,
        {
            "run_loop": NODE_RUN_LOOP,
            "finalize": NODE_FINALIZE,
            "error": NODE_ERROR,
        },
    )
    g.add_edge(NODE_RUN_LOOP, NODE_FINALIZE)
    g.add_edge(NODE_ERROR, NODE_FINALIZE)
    g.add_edge(NODE_FINALIZE, END)
    g.validate()
    return g


class Agent(_LegacyAgent):
    """V2 Agent: legacy class + explicit lifecycle :class:`StateGraph`.

    Inherits every public method (``run_task``, ``chat``, ``shutdown``,
    ``handle_message``, etc.) byte-faithfully from
    :class:`openakita.core._agent_legacy.Agent` so all existing
    callers continue to work. The v2 additions are
    :attr:`lifecycle_graph`, :meth:`route_lifecycle`,
    :meth:`describe_lifecycle`, :meth:`classify_inbound_risk`,
    :meth:`build_destructive_question`,
    :meth:`format_attachment_reference`, and
    :meth:`should_skip_risk_gate`.

    Honest scope: the legacy ``__init__`` chain (Ralph loop wiring,
    skill catalogue load, MCP discovery, ...) is still authoritative
    and does not yet consume :attr:`lifecycle_graph`. The graph is a
    new introspection / extension point for v2 callers; full
    migration of the lifecycle onto StateGraph nodes is tracked for
    P-RC-7+.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lifecycle_graph: StateGraph = build_agent_lifecycle_graph()

    # ----- StateGraph wiring -----------------------------------------

    @property
    def lifecycle_graph(self) -> StateGraph:
        """Constructed Agent-lifecycle :class:`StateGraph` for this agent."""
        return self._lifecycle_graph

    async def route_lifecycle(
        self, current_node: str, decision: Any
    ) -> str | None:
        """Look up the next lifecycle node id given ``current_node`` and ``decision``."""
        return await self._lifecycle_graph.route(current_node, decision)

    def describe_lifecycle(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of the lifecycle graph topology."""
        g = self._lifecycle_graph
        return {
            "entry_point": g.entry_point,
            "nodes": sorted(g.nodes),
            "successors": {n: g.successors(n) for n in sorted(g.nodes)},
        }

    # ----- Risk gate (v2-native composition over agent.safety) -------

    def classify_inbound_risk(
        self, message: str, intent: Any = None
    ) -> RiskIntentResult:
        """V2-native pre-LLM risk classifier.

        Thin wrapper over
        :func:`openakita.agent.safety.destructive_intent.classify_risk_intent`
        kept on the :class:`Agent` surface so callers can do
        ``agent.classify_inbound_risk(msg)`` without reaching into the
        helper package.  The result is a :class:`RiskIntentResult`.
        """
        return classify_risk_intent(intent, message)

    def build_destructive_question(
        self, message: str, classification: RiskIntentResult | None = None
    ) -> str:
        """V2-native confirmation prompt builder for destructive intents."""
        return build_destructive_intent_question(message, classification)

    def should_skip_risk_gate(
        self,
        session: Any,
        message: str,
        classification: RiskIntentResult | None,
    ) -> RiskGateDecision:
        """Composed trust-mode + trusted-path skip decision.

        Returns a :class:`RiskGateDecision`.  ``label="skip"`` indicates
        the gate can be bypassed (with the underlying reason populated),
        ``label="run"`` means the normal gate must run.  ``label="abort"``
        is reserved for classifier failures (currently never emitted by
        this method; populated by callers that catch downstream errors).

        Composition order mirrors the legacy ``RiskGate.evaluate`` body:

        1.  trust-mode skip (sensitive targets always fall through);
        2.  trusted-path / session-grant skip (HIGH-risk always falls
            through).
        """
        trust_reason = check_trust_mode_skip(classification)
        if trust_reason:
            return RiskGateDecision("skip", trust_reason, classification)
        path_reason = check_trusted_path_skip(session, message, classification)
        if path_reason:
            return RiskGateDecision("skip", path_reason, classification)
        return RiskGateDecision("run", None, classification)

    # ----- Desktop / IM attachment routing ---------------------------

    def format_attachment_reference(
        self,
        *,
        att_type: str,
        att_name: str,
        att_mime: str,
        att_url: str,
        att_local_path: str | None = None,
        att_size: int | None = None,
    ) -> str:
        """V2-native attachment text formatter for non-image/video attachments.

        Routes through
        :func:`openakita.runtime.desktop.attachments.format_desktop_attachment_reference`
        -- preserves byte-faithful behaviour with the legacy
        ``Agent._format_desktop_attachment_reference`` wrapper that
        existed before P-RC-6.
        """
        return format_desktop_attachment_reference(
            att_type=att_type,
            att_name=att_name,
            att_mime=att_mime,
            att_url=att_url,
            att_local_path=att_local_path,
            att_size=att_size,
        )

    def inline_local_image_if_eligible(
        self, att_url: str, att_mime: str
    ) -> str | None:
        """V2-native local-image inlining helper.

        Returns a ``data:image/...`` URL when the upload is local and
        within ``INLINE_IMAGE_MAX_BYTES`` (5 MB), ``None`` otherwise.
        Routes through
        :func:`openakita.runtime.desktop.attachments.maybe_inline_local_image`.
        """
        return maybe_inline_local_image(att_url, att_mime)

    # ----- Debug / introspection -------------------------------------

    def supports_lifecycle_node(self, node_id: str) -> bool:
        """True when ``node_id`` is in the lifecycle graph."""
        return node_id in self._lifecycle_graph.nodes
