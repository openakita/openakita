"""V2 ReasoningEngine driven by ``runtime.state_graph.StateGraph``.

Per ADR-0001 (fork-style rewrite), ADR-0002 (runtime architecture),
and continuation plan section 6 (P-RC-5), this is the v2 canonical
home for the ReAct reasoning loop. The 8000+ LOC monolith lives at
:mod:`openakita.core._reasoning_engine_legacy`; :class:`ReasoningEngine`
here subclasses it and wires a :class:`StateGraph` on top to make
the Decision -> next-node routing explicit.

Honest scope (G-RC-5 review): this is **not** a from-scratch rewrite
of the 8000+ LOC loop. Backward compatibility is byte-faithful via
inheritance; the v2 additions are (1) :attr:`decision_graph` and
:meth:`route_decision` exposing the explicit topology, (2)
:meth:`evaluate_decision` composing the seven extracted guards in
the legacy check order, and (3) :meth:`filter_tools` / :meth:`should_block`
collapsing the legacy module-level helpers into engine methods.
Legacy ``run()`` / ``reason_stream()`` / ``run_stream()`` are still
inherited untouched; folding them into StateGraph nodes is P-RC-6+.
"""

from __future__ import annotations

import logging
from typing import Any

from openakita.core._reasoning_engine_legacy import (
    Checkpoint,
    Decision,
    DecisionType,
)
from openakita.core._reasoning_engine_legacy import (
    ReasoningEngine as _LegacyReasoningEngine,
)
from openakita.runtime.state_graph import END, START, StateGraph
from openakita.runtime.state_graph.guards.conversation_state import (
    has_recoverable_tool_issue,
    looks_like_waiting_for_user_response,
)
from openakita.runtime.state_graph.guards.recap_context import is_recap_context
from openakita.runtime.state_graph.guards.source_tag import (
    check_source_tag_consistency,
)
from openakita.runtime.state_graph.guards.tool_failure_ack import (
    check_tool_failure_acknowledgement,
    successful_tool_names,
)
from openakita.runtime.state_graph.guards.tool_filters import (
    filter_tools_by_intent,
    filter_tools_by_mode,
    get_mode_ruleset,
    is_shell_write_command,
    should_block_tool,
)
from openakita.runtime.state_graph.guards.unbacked_action import (
    guard_unbacked_action_claim,
)

__all__ = [
    "Checkpoint",
    "Decision",
    "DecisionType",
    "GuardVerdict",
    "ReasoningEngine",
    "build_reasoning_graph",
    "get_mode_ruleset",
    "is_shell_write_command",
]

logger = logging.getLogger(__name__)

# Reasoning node ids (ReAct phases plus task verification + finalize).
NODE_REASON = "reason"
NODE_ACT = "act"
NODE_OBSERVE = "observe"
NODE_VERIFY = "verify"
NODE_FINALIZE = "finalize"

# Routing label map keyed off ``DecisionType.value`` strings.
_DECISION_TO_LABEL: dict[str, str] = {
    # Real DecisionType.value strings from the legacy enum.
    "tool_calls": "act",
    "final_answer": "verify",
    # Extended exit-reason tokens the legacy ``_last_exit_reason`` uses.
    # Not DecisionType values, but the routing map accepts them so a
    # caller can pass either form.
    "tool_use": "act",
    "ask_user": "finalize",
    "continue": "reason",
    "error": "finalize",
    "max_iterations": "finalize",
    "verify_incomplete": "reason",
}


class GuardVerdict:
    """Lightweight result bag for :meth:`ReasoningEngine.evaluate_decision`."""

    __slots__ = ("guard", "passed", "message")

    def __init__(self, guard: str, passed: bool, message: str | None) -> None:
        self.guard = guard
        self.passed = passed
        self.message = message

    def __repr__(self) -> str:
        return (
            f"GuardVerdict(guard={self.guard!r}, passed={self.passed!r}, "
            f"message={self.message!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GuardVerdict):
            return NotImplemented
        return (
            self.guard == other.guard
            and self.passed == other.passed
            and self.message == other.message
        )


def _decision_kind(result: Any) -> str:
    """Best-effort extraction of the decision's ``DecisionType.value``."""
    if result is None:
        return ""
    if hasattr(result, "type"):
        dt = result.type
        return getattr(dt, "value", str(dt))
    if hasattr(result, "value"):
        return result.value
    return str(result)


def build_reasoning_graph() -> StateGraph:
    """Construct the canonical reasoning :class:`StateGraph`.

    Topology: ``START -> reason``; ``reason`` branches by Decision
    (``act``/``verify``/``finalize``/loop ``reason``); ``act -> observe
    -> reason`` (tool-result loopback); ``verify`` branches by
    verifier verdict (``finalize`` or ``reason``); ``finalize -> END``.
    Validated before return.
    """
    g = StateGraph()
    for node in (NODE_REASON, NODE_ACT, NODE_OBSERVE, NODE_VERIFY, NODE_FINALIZE):
        g.add_node(node)
    g.add_edge(START, NODE_REASON)

    def _route_from_reason(result: Any) -> str:
        return _DECISION_TO_LABEL.get(_decision_kind(result), NODE_REASON)

    g.add_conditional_edges(
        NODE_REASON,
        _route_from_reason,
        {
            "act": NODE_ACT,
            "verify": NODE_VERIFY,
            "finalize": NODE_FINALIZE,
            "reason": NODE_REASON,
        },
    )
    g.add_edge(NODE_ACT, NODE_OBSERVE)
    g.add_edge(NODE_OBSERVE, NODE_REASON)

    def _route_from_verify(result: Any) -> str:
        return "reason" if _decision_kind(result) == "verify_incomplete" else "finalize"

    g.add_conditional_edges(
        NODE_VERIFY,
        _route_from_verify,
        {"finalize": NODE_FINALIZE, "reason": NODE_REASON},
    )
    g.add_edge(NODE_FINALIZE, END)
    g.validate()
    return g


class ReasoningEngine(_LegacyReasoningEngine):
    """V2 ReAct engine: legacy class + explicit :class:`StateGraph` routing.

    Inherits every public method (``run``, ``reason_stream``,
    ``run_stream``, ``release_large_buffers``, etc.) byte-faithfully
    from :class:`openakita.core._reasoning_engine_legacy.ReasoningEngine`
    so all existing callers continue to work. The v2 additions are
    :attr:`decision_graph`, :meth:`route_decision`,
    :meth:`evaluate_decision`, :meth:`filter_tools`,
    :meth:`should_block`, and :meth:`describe_routing`.

    Honest scope: the legacy 1700 LOC ``run()`` is still authoritative
    and does not yet consume :attr:`decision_graph`. The graph is a
    new introspection / extension point for v2 callers; full migration
    of ``run()`` onto StateGraph nodes is tracked for P-RC-6+.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._decision_graph = build_reasoning_graph()

    # ----- StateGraph wiring -----------------------------------------

    @property
    def decision_graph(self) -> StateGraph:
        """Constructed reasoning :class:`StateGraph` for this engine."""
        return self._decision_graph

    async def route_decision(
        self, current_node: str, decision: Decision | None
    ) -> str | None:
        """Look up the next node id given ``current_node`` and ``decision``."""
        return await self._decision_graph.route(current_node, decision)

    # ----- Tool trimming ---------------------------------------------

    def filter_tools(
        self,
        tools: list[dict],
        *,
        mode: str,
        intent_name: str | None = None,
        intent_tool_hints: list[str] | None = None,
        requires_tools: bool = False,
    ) -> list[dict]:
        """Compose mode + intent tool trimming into one call."""
        trimmed = filter_tools_by_mode(tools, mode)
        return filter_tools_by_intent(
            trimmed,
            intent_name=intent_name,
            intent_tool_hints=intent_tool_hints,
            requires_tools=requires_tools,
        )

    def should_block(
        self,
        tool_name: str,
        tool_input: Any,
        allowed_tool_names: set[str] | None,
        mode: str,
    ) -> str | None:
        """Public wrapper around :func:`should_block_tool`."""
        return should_block_tool(tool_name, tool_input, allowed_tool_names, mode)

    # ----- Guard evaluation ------------------------------------------

    def evaluate_decision(
        self,
        text: str,
        *,
        last_user_text: str = "",
        tool_results: list[dict] | None = None,
        recent_messages: list[dict] | None = None,
    ) -> list[GuardVerdict]:
        """Run the seven extracted guards in legacy check order.

        Returns a list of :class:`GuardVerdict`; a caller can
        short-circuit on the first ``passed=False`` to mirror the
        legacy in-line behaviour.
        """
        verdicts: list[GuardVerdict] = []

        tools_executed_count = len(tool_results or [])
        tag_msg = check_source_tag_consistency(text, tools_executed_count)
        verdicts.append(GuardVerdict("source_tag", tag_msg is None, tag_msg))

        executed_names = [
            (tr.get("name") or tr.get("tool_name") or "")
            for tr in (tool_results or [])
        ]
        names = list(successful_tool_names(executed_names, tool_results))
        ack_msg = check_tool_failure_acknowledgement(text, tool_results)
        verdicts.append(GuardVerdict("tool_failure_ack", ack_msg is None, ack_msg))

        unbacked_out = guard_unbacked_action_claim(
            text,
            executed_tool_names=names,
            tool_results=tool_results,
        )
        # The legacy guard returns the input text unchanged when clean
        # and the text+warning suffix when it flagged an unbacked claim.
        # The guard "passes" iff the returned text is identical to the
        # input; otherwise the appended suffix is the warning message.
        unbacked_passed = unbacked_out == text
        verdicts.append(
            GuardVerdict(
                "unbacked_action",
                unbacked_passed,
                None if unbacked_passed else unbacked_out[len(text):].strip(),
            )
        )

        recap = is_recap_context(last_user_text, "")
        verdicts.append(GuardVerdict("recap_context", True, f"recap={recap}"))

        waiting = looks_like_waiting_for_user_response(text)
        verdicts.append(
            GuardVerdict(
                "waiting_for_user",
                not waiting,
                "waiting for user" if waiting else None,
            )
        )

        recoverable = has_recoverable_tool_issue(tool_results)
        verdicts.append(
            GuardVerdict(
                "recoverable_tool_issue", True, f"recoverable={recoverable}"
            )
        )

        return verdicts

    # ----- Convenience helpers ---------------------------------------

    def classify_exit_reason(self, decision: Decision | None) -> str:
        """Map a ``Decision`` to the legacy ``_last_exit_reason`` token.

        Mirrors the if/elif cascade the legacy ``run()`` performs at
        loop termination so v2 callers can derive the same exit token
        without re-implementing the mapping.
        """
        kind = _decision_kind(decision)
        if kind == "ask_user":
            return "ask_user"
        if kind == "max_iterations":
            return "max_iterations"
        if kind == "verify_incomplete":
            return "verify_incomplete"
        if kind == "error":
            return "loop_terminated"
        return "normal"

    def is_terminal_decision(self, decision: Decision | None) -> bool:
        """True when the decision routes straight to ``finalize``."""
        return _DECISION_TO_LABEL.get(_decision_kind(decision), "") == "finalize"

    def supports_decision_kind(self, kind: str) -> bool:
        """True when ``kind`` is in the engine's routing table.

        Useful for the parity test which asserts the v1 and v2 engines
        accept the same Decision vocabulary.
        """
        return kind in _DECISION_TO_LABEL

    # ----- Debug / introspection -------------------------------------

    def describe_routing(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of the reasoning graph topology."""
        return {
            "entry_point": self._decision_graph.entry_point,
            "nodes": sorted(self._decision_graph.nodes),
            "successors": {
                n: self._decision_graph.successors(n)
                for n in sorted(self._decision_graph.nodes)
            },
        }
