"""Dual-ledger record types for the v2 supervisor.

Implements ADR-0004's dual-ledger model:

* :class:`TaskLedger` is the *outer-loop* record. Long-lived. Updated
  only when the supervisor enters or replans the outer loop. Captures
  the user's task, the LLM-extracted facts, and the LLM-drafted plan.
* :class:`ProgressLedger` is the *inner-loop* record. Short-lived.
  Produced by the LLM on every inner turn. Five required keys, each
  with both ``answer`` and ``reason``. Strict JSON parsing with retry.

Together they replace the legacy `max_task_seconds` wall-clock cancel
that produced the duplicate-delegate cascade. Stall detection
(:mod:`stall_detector`) consumes ``ProgressLedger`` to decide whether
the supervisor should advance, replan, or give up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .execution_context import ExecutionPhase

__all__ = [
    "TaskLedger",
    "ProgressLedger",
    "ProgressLedgerEntry",
    "ProgressLedgerParseError",
    "REQUIRED_PROGRESS_KEYS",
    "parse_progress_ledger_json",
]

#: Five required keys per ADR-0004's strict JSON contract.
REQUIRED_PROGRESS_KEYS: tuple[str, ...] = (
    "is_request_satisfied",
    "is_progress_being_made",
    "is_in_loop",
    "instruction_or_question",
    "next_speaker",
)


# ---------------------------------------------------------------------------
# Outer-loop ledger
# ---------------------------------------------------------------------------


@dataclass
class TaskLedger:
    """Outer-loop record of intent + plan for a single user command.

    Mutated only at outer-loop boundaries. Each :meth:`revise` call
    bumps :attr:`revision`; the supervisor publishes a stream event
    when ``revision`` changes so the UI can render the replan timeline.
    """

    command_id: str
    org_id: str
    root_node_id: str
    task: str
    facts: str = ""
    plan: str = ""
    revision: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def revise(self, *, new_facts: str, new_plan: str) -> None:
        """Apply a re-extracted facts and re-drafted plan."""
        self.facts = new_facts
        self.plan = new_plan
        self.revision += 1
        self.updated_at = datetime.now(UTC)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "org_id": self.org_id,
            "root_node_id": self.root_node_id,
            "task": self.task,
            "facts": self.facts,
            "plan": self.plan,
            "revision": self.revision,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> TaskLedger:
        return cls(
            command_id=data["command_id"],
            org_id=data["org_id"],
            root_node_id=data["root_node_id"],
            task=data["task"],
            facts=data.get("facts", ""),
            plan=data.get("plan", ""),
            revision=int(data.get("revision", 0)),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )


# ---------------------------------------------------------------------------
# Inner-loop ledger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgressLedgerEntry:
    """One key/value pair within a :class:`ProgressLedger`.

    Both ``answer`` and ``reason`` are required. ``answer`` is whichever
    JSON-native type the question wants (``bool`` for the three
    yes/no questions, ``str`` for the two free-text answers).
    ``reason`` is always a string so the UI has something to render.
    """

    answer: Any
    reason: str

    def to_jsonable(self) -> dict[str, Any]:
        return {"answer": self.answer, "reason": self.reason}


@dataclass(frozen=True)
class ProgressLedger:
    """Per-turn progress evaluation produced by the LLM.

    Built by :func:`parse_progress_ledger_json` from the LLM's response
    (which we validate strictly). Once constructed, it is immutable so
    the supervisor can store it in the per-command checkpoint and
    replay it later for audit.
    """

    turn_id: int
    is_request_satisfied: ProgressLedgerEntry
    is_progress_being_made: ProgressLedgerEntry
    is_in_loop: ProgressLedgerEntry
    instruction_or_question: ProgressLedgerEntry
    next_speaker: ProgressLedgerEntry
    raw_json: str
    execution_phase: ExecutionPhase = ExecutionPhase.EXECUTION
    emitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "is_request_satisfied": self.is_request_satisfied.to_jsonable(),
            "is_progress_being_made": self.is_progress_being_made.to_jsonable(),
            "is_in_loop": self.is_in_loop.to_jsonable(),
            "instruction_or_question": self.instruction_or_question.to_jsonable(),
            "next_speaker": self.next_speaker.to_jsonable(),
            "execution_phase": self.execution_phase.value,
            "raw_json": self.raw_json,
            "emitted_at": self.emitted_at.isoformat(),
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> ProgressLedger:
        def _entry(d: dict[str, Any]) -> ProgressLedgerEntry:
            return ProgressLedgerEntry(answer=d["answer"], reason=d["reason"])

        return cls(
            turn_id=int(data["turn_id"]),
            is_request_satisfied=_entry(data["is_request_satisfied"]),
            is_progress_being_made=_entry(data["is_progress_being_made"]),
            is_in_loop=_entry(data["is_in_loop"]),
            instruction_or_question=_entry(data["instruction_or_question"]),
            next_speaker=_entry(data["next_speaker"]),
            raw_json=data.get("raw_json", ""),
            execution_phase=ExecutionPhase(
                str(data.get("execution_phase") or ExecutionPhase.EXECUTION)
            ),
            emitted_at=datetime.fromisoformat(data["emitted_at"]),
        )

    # ------------------------------------------------------------------
    # Convenience accessors for the supervisor / stall detector
    # ------------------------------------------------------------------

    @property
    def request_satisfied(self) -> bool:
        return bool(self.is_request_satisfied.answer)

    @property
    def progress_being_made(self) -> bool:
        return bool(self.is_progress_being_made.answer)

    @property
    def in_loop(self) -> bool:
        return bool(self.is_in_loop.answer)

    @property
    def next_speaker_name(self) -> str:
        return str(self.next_speaker.answer)

    @property
    def instruction(self) -> str:
        return str(self.instruction_or_question.answer)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class ProgressLedgerParseError(ValueError):
    """Raised when an LLM response cannot be coerced into a ProgressLedger."""


_JSON_BLOCK_RE = re.compile(
    r"\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}",
    re.DOTALL,
)


def _extract_first_json_object(raw: str) -> str:
    """Find and return the first balanced JSON object in ``raw``.

    LLMs frequently wrap JSON in markdown fences or add prose before /
    after the object. We accept any balanced ``{ ... }`` block starting
    at the first ``{``. Nested objects are handled correctly.
    """
    start = raw.find("{")
    if start < 0:
        raise ProgressLedgerParseError("response contains no '{' character")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    raise ProgressLedgerParseError(
        "response contains an unterminated JSON object"
    )


def parse_progress_ledger_json(
    raw: str,
    *,
    turn_id: int,
) -> ProgressLedger:
    """Parse an LLM's progress-ledger response into a :class:`ProgressLedger`.

    Tolerates markdown fencing and surrounding prose. Validates that
    every required key is present and that each entry has both
    ``answer`` and ``reason``. The boolean keys (``is_request_satisfied``,
    ``is_progress_being_made``, ``is_in_loop``) are coerced to ``bool``
    via standard truthiness so a string "true" / "false" from a
    misbehaving model still works.
    """
    if not raw or not raw.strip():
        raise ProgressLedgerParseError("response is empty")

    json_text = _extract_first_json_object(raw)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ProgressLedgerParseError(
            f"could not parse JSON: {exc.msg} at line {exc.lineno} col {exc.colno}"
        ) from exc
    if not isinstance(payload, dict):
        raise ProgressLedgerParseError(
            f"top-level value must be an object, got {type(payload).__name__}"
        )

    missing = [k for k in REQUIRED_PROGRESS_KEYS if k not in payload]
    if missing:
        raise ProgressLedgerParseError(
            f"missing required keys: {missing}"
        )

    def _coerce_entry(key: str, *, kind: str) -> ProgressLedgerEntry:
        value = payload[key]
        if not isinstance(value, dict):
            # Salvage a known flaky-model failure mode: some providers
            # intermittently FLATTEN an entry to a bare scalar
            # (e.g. ``"is_request_satisfied": false`` or
            # ``"next_speaker": "writer-b"``) instead of the required
            # ``{"answer": ..., "reason": ...}`` object. Previously this burned
            # all 10 retries and failed the whole command with
            # "must be an object with 'answer' and 'reason'", so a single bad
            # turn aborted an otherwise-complete run (no final report -> UI
            # issue #4). We wrap the scalar as the answer with an empty reason
            # rather than fail. The object shape is still PREFERRED (the prompt
            # asks for it); this is a last-resort coercion. A dict that is
            # merely missing ``answer``/``reason`` is still rejected below, so
            # the strict contract for malformed objects is unchanged.
            value = {"answer": value, "reason": ""}
        if "answer" not in value or "reason" not in value:
            raise ProgressLedgerParseError(
                f"{key!r} must have both 'answer' and 'reason'"
            )
        answer = value["answer"]
        if kind == "bool":
            answer = _coerce_bool(answer, key)
        elif kind == "str":
            answer = str(answer)
        reason = str(value["reason"])
        return ProgressLedgerEntry(answer=answer, reason=reason)

    raw_phase = str(payload.get("execution_phase") or ExecutionPhase.EXECUTION).strip().lower()
    try:
        execution_phase = ExecutionPhase(raw_phase)
    except ValueError as exc:
        raise ProgressLedgerParseError(
            "execution_phase must be 'planning', 'execution', or 'finalization'"
        ) from exc

    return ProgressLedger(
        turn_id=int(turn_id),
        is_request_satisfied=_coerce_entry("is_request_satisfied", kind="bool"),
        is_progress_being_made=_coerce_entry("is_progress_being_made", kind="bool"),
        is_in_loop=_coerce_entry("is_in_loop", kind="bool"),
        instruction_or_question=_coerce_entry("instruction_or_question", kind="str"),
        next_speaker=_coerce_entry("next_speaker", kind="str"),
        raw_json=json_text,
        execution_phase=execution_phase,
    )


def _coerce_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "1"}:
            return True
        if v in {"false", "no", "0"}:
            return False
    raise ProgressLedgerParseError(
        f"{key}.answer must be a boolean, got {type(value).__name__}: {value!r}"
    )
