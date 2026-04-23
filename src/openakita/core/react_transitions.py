"""ReAct engine transition state machine.

Centralizes loop control for: max-output recovery, continuation nudges,
final-answer verification, cancellation, budget pauses, tool follow-up.

This module is the authoritative decision layer for ReAct loop transitions.
ReAct remains the authoritative loop; RalphLoop (ralph.py) is standalone legacy.
"""

from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReActTransitionReason(str, Enum):
    """Why the ReAct loop is transitioning to the next state."""
    CANCELLATION = "cancellation"
    BUDGET_PAUSE = "budget_pause"
    TOOL_FOLLOW_UP = "tool_follow_up"
    MAX_OUTPUT_RECOVERY = "max_output_recovery"
    FINAL_ANSWER_VERIFICATION = "final_answer_verification"
    CONTINUATION_NUDGE = "continuation_nudge"
    COMPLETED = "completed"
    BLOCKED = "blocked"


class ReActTerminalStatus(str, Enum):
    """Final status when the ReAct loop exits."""
    COMPLETED = "completed"
    BLOCKED = "blocked"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@dataclass
class ReActLoopState:
    """Mutable state tracked across ReAct loop iterations.

    Instances are created once per task execution and passed through
    all transition helpers. Counter fields are incremented in-place.
    Limits are populated from settings at construction time.
    """
    max_output_recovery_count: int = 0
    continuation_nudge_count: int = 0
    last_output_hash: str = ""
    max_output_recovery_limit: int = 2
    continuation_nudge_max: int = 3

    @property
    def can_recover_max_output(self) -> bool:
        return self.max_output_recovery_count < self.max_output_recovery_limit

    @property
    def can_nudge_continuation(self) -> bool:
        return self.continuation_nudge_count < self.continuation_nudge_max


@dataclass
class TransitionDecision:
    """What the ReAct loop should do after evaluating the current turn."""
    action: str  # "continue" | "nudge" | "recover" | "complete" | "block" | "pause" | "cancel"
    reason: ReActTransitionReason
    status: ReActTerminalStatus | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Internal helpers ──


def _hash_output(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def detect_repeated_output(current: str, last_hash: str) -> bool:
    """Return True if current output is identical to the previously recorded one."""
    if not last_hash or not current.strip():
        return False
    return _hash_output(current) == last_hash


COMPLETION_MARKERS = [
    "is there anything else",
    "let me know if",
    "feel free to ask",
    "happy to help",
    "don't hesitate",
    "anything i can",
    "how can i assist",
    "what else",
]


def _is_conversational(response: str) -> bool:
    """Heuristic: does the response read like a conversational sign-off?"""
    lowered = response.lower()
    return any(marker in lowered for marker in COMPLETION_MARKERS)


def _is_premature_completion(response: str) -> bool:
    """Detect short responses that claim completion but may be premature."""
    lowered = response.strip().lower()
    completion_set = {
        "done", "done!", "done.", "complete", "complete.",
        "complete!", "finished", "finished.", "finished!",
        "ready", "ready.", "ready!", "that's it", "that is it",
        "task complete", "task complete.", "task complete!",
    }
    if lowered in completion_set:
        return True
    for prefix in ("done.", "done!", "complete.", "complete!",
                   "finished.", "finished!", "task complete"):
        if lowered.startswith(prefix):
            return True
    return False


# ── Public helpers ──


def should_nudge_continuation(
    response: str,
    has_tools: bool,
    loop_state: ReActLoopState,
) -> bool:
    """Return True when the model gave a premature final-answer without tool calls.

    Conditions:
    - Not at max nudge count
    - No tool calls in the response
    - Not a conversational sign-off
    - Looks like a premature completion claim
    """
    if not loop_state.can_nudge_continuation:
        return False
    if has_tools:
        return False
    if _is_conversational(response):
        return False
    return _is_premature_completion(response)


def evaluate_transition(
    stop_reason: str | None,
    response: str,
    has_tools: bool,
    has_pending_todos: bool,
    loop_state: ReActLoopState,
) -> TransitionDecision:
    """Determine the next action for the ReAct loop.

    Priority order:
    1. cancellation
    2. budget pause
    3. tool follow-up
    4. max-output recovery
    5. final-answer verification
    6. continuation nudge
    7. completed / blocked
    """
    # 1. Cancellation
    if stop_reason == "cancelled":
        return TransitionDecision(
            action="cancel",
            reason=ReActTransitionReason.CANCELLATION,
            status=ReActTerminalStatus.CANCELLED,
        )

    # 2. Budget pause
    if stop_reason == "budget_exceeded":
        return TransitionDecision(
            action="pause",
            reason=ReActTransitionReason.BUDGET_PAUSE,
            status=ReActTerminalStatus.PAUSED,
            metadata={"message": "Token budget exceeded"},
        )

    # 3. Tool follow-up — model used a tool, need to feed result
    if has_tools:
        return TransitionDecision(
            action="continue",
            reason=ReActTransitionReason.TOOL_FOLLOW_UP,
            status=None,
        )

    # 4. Max-output recovery
    if stop_reason == "max_tokens":
        if loop_state.can_recover_max_output:
            loop_state.max_output_recovery_count += 1
            loop_state.last_output_hash = _hash_output(response)
            return TransitionDecision(
                action="recover",
                reason=ReActTransitionReason.MAX_OUTPUT_RECOVERY,
                status=None,
                metadata={
                    "recovery_attempt": loop_state.max_output_recovery_count,
                    "remaining": loop_state.max_output_recovery_limit - loop_state.max_output_recovery_count,
                },
            )
        return TransitionDecision(
            action="block",
            reason=ReActTransitionReason.MAX_OUTPUT_RECOVERY,
            status=ReActTerminalStatus.BLOCKED,
            metadata={"message": "Max output recovery limit reached"},
        )

    # 5. Final-answer verification with pending-todo awareness
    if has_pending_todos and not has_tools:
        return TransitionDecision(
            action="nudge",
            reason=ReActTransitionReason.CONTINUATION_NUDGE,
            status=None,
            metadata={"message": "Task claimed complete but pending todos remain"},
        )

    # 6. Continuation nudge — premature final-answer
    if should_nudge_continuation(response, has_tools, loop_state):
        loop_state.continuation_nudge_count += 1
        loop_state.last_output_hash = _hash_output(response)
        return TransitionDecision(
            action="nudge",
            reason=ReActTransitionReason.CONTINUATION_NUDGE,
            status=None,
            metadata={"nudge_count": loop_state.continuation_nudge_count},
        )

    # 7. Default: completed
    return TransitionDecision(
        action="complete",
        reason=ReActTransitionReason.COMPLETED,
        status=ReActTerminalStatus.COMPLETED,
    )


def build_continuation_nudge(
    loop_state: ReActLoopState,
    pending_todos: list[str] | None = None,
) -> str:
    """Build a nudge message to push the model to continue working."""
    if pending_todos:
        remaining = ", ".join(pending_todos)
        return (
            f"You indicated the task is complete, but there are still "
            f"pending steps: {remaining}. Please continue with the next step."
        )
    return (
        "You indicated the task is complete, but work remains. "
        "Please continue with the next step."
    )


def build_max_tokens_recovery_prompt(
    loop_state: ReActLoopState,
    cutoff_hint: str | None = None,
) -> str:
    """Build a recovery prompt after max_tokens truncation."""
    base = (
        "Your previous response was truncated. Please continue exactly "
        "where you left off, repeating the last 1-2 sentences for context."
    )
    if cutoff_hint:
        return f"Your previous response was truncated after: \"{cutoff_hint}\". Please continue from there."
    return base
