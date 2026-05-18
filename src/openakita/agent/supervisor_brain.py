"""SupervisorBrain adapter scaffolding for the v2 canary dispatch.

The real Phase-2 brain rewrite is reserved for P-RC-4. Until then,
the v2 dispatch path needs *something* that satisfies the
:class:`openakita.runtime.supervisor.SupervisorBrain` protocol so
``Supervisor.run`` can be exercised end-to-end on canary traffic.

This module ships :class:`DegenerateSupervisorBrain`, which reports
``is_request_satisfied = True`` on the first progress-ledger call
so the supervisor terminates with :class:`FinalOutcome.DONE` on
turn one without delegating to any node or making any LLM call.
See continuation plan section 2.1.
"""

from __future__ import annotations

import json
from typing import Any

from openakita.runtime.ledger import ProgressLedger
from openakita.runtime.supervisor import SupervisorBrain

__all__ = ["DegenerateSupervisorBrain", "default_supervisor_brain"]


class DegenerateSupervisorBrain(SupervisorBrain):
    """No-op SupervisorBrain that completes on the first turn."""

    def __init__(
        self,
        *,
        ack_text: str = "(canary v2) message acknowledged; no work scheduled.",
    ) -> None:
        self.ack_text = ack_text

    async def extract_facts(self, *, task: str) -> str:
        return f"User asked: {task[:200]}"

    async def draft_plan(self, *, task: str, facts: str) -> str:
        return "1. acknowledge the message and stop."

    async def emit_progress_ledger(
        self,
        *,
        task: str,
        facts: str,
        plan: str,
        history: list[ProgressLedger],
    ) -> str:
        payload: dict[str, Any] = {
            "is_request_satisfied":    {"answer": True,  "reason": self.ack_text},
            "is_progress_being_made":  {"answer": True,  "reason": "degenerate"},
            "is_in_loop":              {"answer": False, "reason": "single turn"},
            "instruction_or_question": {"answer": self.ack_text, "reason": "final"},
            "next_speaker":            {"answer": "supervisor", "reason": "terminal"},
        }
        return json.dumps(payload)


def default_supervisor_brain() -> SupervisorBrain:
    """Factory used by the dispatch path when no brain is injected."""
    return DegenerateSupervisorBrain()
