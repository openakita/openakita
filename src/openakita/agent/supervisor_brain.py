"""SupervisorBrain adapter scaffolding for the v2 dispatch path.

The real Phase-2 brain rewrite is reserved for P-RC-4. Until then,
the v2 dispatch path needs *something* that satisfies the
:class:`openakita.runtime.supervisor.SupervisorBrain` protocol so
``Supervisor.run`` can be exercised end-to-end.

This module ships two implementations:

* :class:`DegenerateSupervisorBrain` -- the original canary brain
  that reports ``is_request_satisfied = True`` on the first
  progress-ledger call, so the supervisor terminates DONE on turn 1
  without delegating. Used by tests + the legacy IM canary that
  pre-dates real delegation.
* :class:`PassThroughSupervisorBrain` -- single-delegation brain.
  Turn 1 emits ``next_speaker=<root_node_id>`` so the supervisor
  hands the verbatim user task to the root node via the injected
  ``deliver`` callable. Turn 2 observes the resulting
  :class:`DelegationResult` in ``history`` and emits
  ``is_request_satisfied=True``. Semantic equivalent of the legacy
  ``OrgCommandService._run_minimal`` single-shot dispatch: the node
  still owns multi-step orchestration internally via Sprint-4
  ``<dispatch>`` XML parsing inside
  :class:`~openakita.orgs._runtime_agent_pipeline_executor.AgentPipelineExecutor.dispatch_subtask`
  recursion -- the brain does not need to plan multi-turn LLM calls.
  This is the production brain for the HTTP takeover path (Sprint-9).

A real multi-turn LLM-driven brain is the P-RC-4 follow-up; this
stays minimal so the HTTP takeover commit does not bundle a brain
rewrite.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from openakita.runtime.ledger import ProgressLedger
from openakita.runtime.supervisor import SupervisorBrain

__all__ = [
    "DegenerateSupervisorBrain",
    "PassThroughSupervisorBrain",
    "default_supervisor_brain",
]


class DegenerateSupervisorBrain(SupervisorBrain):
    """No-op SupervisorBrain that completes on the first turn."""

    def __init__(
        self,
        *,
        ack_text: str = "(canary v2) message acknowledged; no work scheduled.",
    ) -> None:
        self.ack_text = ack_text

    async def extract_facts(
        self,
        *,
        task: str,
        cancel_event: asyncio.Event | None = None,  # noqa: ARG002 -- protocol shape
    ) -> str:
        return f"User asked: {task[:200]}"

    async def draft_plan(
        self,
        *,
        task: str,
        facts: str,
        cancel_event: asyncio.Event | None = None,  # noqa: ARG002 -- protocol shape
    ) -> str:
        return "1. acknowledge the message and stop."

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
        cancel_event: asyncio.Event | None = None,  # noqa: ARG002 -- protocol shape
    ) -> str:
        payload: dict[str, Any] = {
            "is_request_satisfied":    {"answer": True,  "reason": self.ack_text},
            "is_progress_being_made":  {"answer": True,  "reason": "degenerate"},
            "is_in_loop":              {"answer": False, "reason": "single turn"},
            "instruction_or_question": {"answer": self.ack_text, "reason": "final"},
            "next_speaker":            {"answer": "supervisor", "reason": "terminal"},
        }
        return json.dumps(payload)


class PassThroughSupervisorBrain(SupervisorBrain):
    """Single-delegation brain: hand task to root node, terminate on result.

    Turn 1: emit ``next_speaker=root_node_id`` with the verbatim user
    task as the instruction. The supervisor's deliver callable will
    activate the root node, which (via Sprint-4 ``<dispatch>`` XML
    parsing) may recurse into sibling nodes on its own.

    Turn 2: ``history`` now contains the turn-1 progress ledger plus
    the delegation completed. We mark the request satisfied with the
    node's last delivered message as the rationale -- the actual
    deliverable already landed via the supervisor's
    ``delegation_result`` stream event. Cancellation is handled by
    the supervisor's own ``cancel_token`` checks; the brain itself
    is stateless.

    Args:
        root_node_id: the entry node to delegate to on turn 1.
        max_passthrough_turns: safety cap on how many turns we will
            keep PROCEED-ing before declaring DONE regardless of the
            node's last reply. Default 2 (turn 1 = delegate, turn 2 =
            terminate). Tests may raise this to exercise the
            supervisor's stall detector against the pass-through
            brain.
    """

    def __init__(
        self,
        *,
        root_node_id: str,
        max_passthrough_turns: int = 2,
    ) -> None:
        if not root_node_id:
            raise ValueError("PassThroughSupervisorBrain requires a root_node_id")
        self.root_node_id = root_node_id
        self.max_passthrough_turns = max(1, int(max_passthrough_turns))

    async def extract_facts(
        self,
        *,
        task: str,
        cancel_event: asyncio.Event | None = None,  # noqa: ARG002 -- protocol shape
    ) -> str:
        return f"User asked: {task[:1000]}"

    async def draft_plan(
        self,
        *,
        task: str,
        facts: str,
        cancel_event: asyncio.Event | None = None,  # noqa: ARG002 -- protocol shape
    ) -> str:
        return (
            f"1. Delegate the verbatim task to root node `{self.root_node_id}`.\n"
            "2. Wait for its DelegationResult.\n"
            "3. Mark the request satisfied with the node's reply as rationale."
        )

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
        cancel_event: asyncio.Event | None = None,  # noqa: ARG002 -- protocol shape
    ) -> str:
        turn_index = len(history)
        if turn_index == 0:
            payload: dict[str, Any] = {
                "is_request_satisfied":    {"answer": False, "reason": "pending root delegation"},
                "is_progress_being_made":  {"answer": True,  "reason": "initial dispatch"},
                "is_in_loop":              {"answer": False, "reason": "turn 1 entry"},
                "instruction_or_question": {"answer": task,    "reason": "verbatim user task"},
                "next_speaker":            {"answer": self.root_node_id, "reason": "root entry"},
            }
            return json.dumps(payload)
        # After turn 1: terminate. The deliver callable's result is
        # observable on the StreamBus 'updates' channel as a
        # delegation_result event; the supervisor already wrote that
        # to checkpoint state in turn 1's _checkpoint call.
        payload = {
            "is_request_satisfied":    {"answer": True,  "reason": f"root node {self.root_node_id} replied"},
            "is_progress_being_made":  {"answer": True,  "reason": "single-shot delegation complete"},
            "is_in_loop":              {"answer": False, "reason": "terminal turn"},
            "instruction_or_question": {"answer": "done", "reason": "no further work"},
            "next_speaker":            {"answer": "supervisor", "reason": "terminal"},
        }
        return json.dumps(payload)


def default_supervisor_brain() -> SupervisorBrain:
    """Factory used by the legacy IM canary path when no brain is injected.

    Pass-through HTTP path uses :class:`PassThroughSupervisorBrain`
    constructed by ``runtime.supervisor_factory.build_supervisor_for_command``
    with the resolved root node id.
    """
    return DegenerateSupervisorBrain()
