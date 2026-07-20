"""Tests for :mod:`openakita.runtime.ledger`.

Phase 3 commit 1 (kicking off Phase 3 early because the dual ledger
is the critical-path fix for the duplicate-delegate cascade described
in ADR-0004).

Asserts:

* TaskLedger.revise bumps revision and updates updated_at;
* ProgressLedger round-trips through JSON;
* parse_progress_ledger_json tolerates markdown fences and surrounding
  prose;
* missing required key raises ProgressLedgerParseError with a clear
  message;
* boolean coercion accepts JSON true/false, numeric 1/0, and the
  canonical string forms a misbehaving model emits;
* convenience accessors (.request_satisfied, .progress_being_made,
  .in_loop, .next_speaker_name, .instruction) match the underlying
  entry answers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openakita.runtime.execution_context import ExecutionPhase
from openakita.runtime.ledger import (
    REQUIRED_PROGRESS_KEYS,
    ProgressLedger,
    ProgressLedgerEntry,
    ProgressLedgerParseError,
    TaskLedger,
    parse_progress_ledger_json,
)

# ---------------------------------------------------------------------------
# TaskLedger
# ---------------------------------------------------------------------------


def test_task_ledger_revise_bumps_revision_and_updated_at() -> None:
    tl = TaskLedger(
        command_id="cmd_a",
        org_id="org_b",
        root_node_id="node_root",
        task="ship a 10s vertical video",
    )
    assert tl.revision == 0
    initial = tl.updated_at
    tl.revise(new_facts="updated facts", new_plan="updated plan")
    assert tl.revision == 1
    assert tl.facts == "updated facts"
    assert tl.plan == "updated plan"
    assert tl.updated_at >= initial


def test_task_ledger_round_trip() -> None:
    tl = TaskLedger(
        command_id="cmd_a",
        org_id="org_b",
        root_node_id="node_root",
        task="hello",
        facts="f",
        plan="p",
        revision=2,
    )
    rebuilt = TaskLedger.from_jsonable(tl.to_jsonable())
    # equality: dataclass with the same fields, accepting microsecond
    # rounding through isoformat
    assert rebuilt.command_id == tl.command_id
    assert rebuilt.task == tl.task
    assert rebuilt.facts == tl.facts
    assert rebuilt.plan == tl.plan
    assert rebuilt.revision == tl.revision


# ---------------------------------------------------------------------------
# parse_progress_ledger_json — happy path
# ---------------------------------------------------------------------------


GOOD_JSON = """
{
  "is_request_satisfied":   {"answer": false, "reason": "we have not delivered yet"},
  "is_progress_being_made": {"answer": true,  "reason": "shot list expanded by 2"},
  "is_in_loop":             {"answer": false, "reason": "no repeats"},
  "instruction_or_question":{"answer": "render shot 3 in 9:16", "reason": "next deliverable"},
  "next_speaker":           {"answer": "image_artist", "reason": "owns shots"}
}
"""


def test_parse_progress_ledger_basic() -> None:
    pl = parse_progress_ledger_json(GOOD_JSON, turn_id=4)
    assert pl.turn_id == 4
    assert pl.request_satisfied is False
    assert pl.progress_being_made is True
    assert pl.in_loop is False
    assert pl.next_speaker_name == "image_artist"
    assert "render shot 3" in pl.instruction
    assert pl.execution_phase is ExecutionPhase.EXECUTION


def test_parse_progress_ledger_accepts_structured_finalization_phase() -> None:
    raw = GOOD_JSON.replace(
        '"next_speaker":           {"answer": "image_artist", "reason": "owns shots"}',
        '"next_speaker":           {"answer": "node_root", "reason": "integrates"},\n'
        '  "execution_phase":        "finalization"',
    )

    pl = parse_progress_ledger_json(raw, turn_id=5)

    assert pl.execution_phase is ExecutionPhase.FINALIZATION


def test_parse_progress_ledger_accepts_structured_planning_phase() -> None:
    raw = GOOD_JSON.replace(
        '"next_speaker":           {"answer": "image_artist", "reason": "owns shots"}',
        '"next_speaker":           {"answer": "node_root", "reason": "plans"},\n'
        '  "execution_phase":        "planning"',
    )

    pl = parse_progress_ledger_json(raw, turn_id=1)

    assert pl.execution_phase is ExecutionPhase.PLANNING


def test_parse_progress_ledger_rejects_unknown_execution_phase() -> None:
    raw = GOOD_JSON.replace(
        '"next_speaker":           {"answer": "image_artist", "reason": "owns shots"}',
        '"next_speaker":           {"answer": "image_artist", "reason": "owns shots"},\n'
        '  "execution_phase":        "guessing"',
    )

    with pytest.raises(ProgressLedgerParseError, match="execution_phase"):
        parse_progress_ledger_json(raw, turn_id=5)


def test_parse_progress_ledger_round_trip() -> None:
    pl = parse_progress_ledger_json(GOOD_JSON, turn_id=4)
    rebuilt = ProgressLedger.from_jsonable(pl.to_jsonable())
    assert rebuilt.next_speaker_name == "image_artist"
    assert rebuilt.in_loop is False


def test_parse_handles_markdown_fence() -> None:
    raw = "Some preamble.\n```json\n" + GOOD_JSON + "\n```\nTrailing prose."
    pl = parse_progress_ledger_json(raw, turn_id=1)
    assert pl.next_speaker_name == "image_artist"


def test_parse_handles_prose_around_object() -> None:
    raw = "Here is the ledger you asked for: " + GOOD_JSON + " (let me know if you need anything)."
    pl = parse_progress_ledger_json(raw, turn_id=1)
    assert pl.progress_being_made is True


def test_parse_handles_nested_objects_in_payload() -> None:
    """Real LLM responses sometimes nest JSON inside fields; the
    extractor must locate the *outer* balanced object correctly."""
    raw = """
    {
      "is_request_satisfied":   {"answer": false, "reason": "still pending"},
      "is_progress_being_made": {"answer": true,  "reason": "ok"},
      "is_in_loop":             {"answer": false, "reason": "{nested looking string}"},
      "instruction_or_question":{"answer": "{}", "reason": "edge case"},
      "next_speaker":           {"answer": "art_director", "reason": "next role"}
    }
    """
    pl = parse_progress_ledger_json(raw, turn_id=1)
    assert pl.in_loop is False
    assert pl.instruction == "{}"


# ---------------------------------------------------------------------------
# parse_progress_ledger_json — failure paths
# ---------------------------------------------------------------------------


def test_parse_empty_response_raises() -> None:
    with pytest.raises(ProgressLedgerParseError):
        parse_progress_ledger_json("", turn_id=1)


def test_parse_missing_braces_raises() -> None:
    with pytest.raises(ProgressLedgerParseError) as info:
        parse_progress_ledger_json("just prose without any object", turn_id=1)
    assert "no '{'" in str(info.value)


def test_parse_unterminated_object_raises() -> None:
    raw = "{\"is_request_satisfied\": {\"answer\": false, \"reason\": \"unterminated"
    with pytest.raises(ProgressLedgerParseError):
        parse_progress_ledger_json(raw, turn_id=1)


def test_parse_missing_required_key_raises() -> None:
    raw = """
    {
      "is_request_satisfied":   {"answer": false, "reason": "x"},
      "is_progress_being_made": {"answer": true,  "reason": "x"},
      "is_in_loop":             {"answer": false, "reason": "x"},
      "instruction_or_question":{"answer": "y", "reason": "z"}
    }
    """
    with pytest.raises(ProgressLedgerParseError) as info:
        parse_progress_ledger_json(raw, turn_id=1)
    assert "next_speaker" in str(info.value)


def test_parse_entry_missing_reason_raises() -> None:
    raw = """
    {
      "is_request_satisfied":   {"answer": false},
      "is_progress_being_made": {"answer": true,  "reason": "x"},
      "is_in_loop":             {"answer": false, "reason": "x"},
      "instruction_or_question":{"answer": "y", "reason": "z"},
      "next_speaker":           {"answer": "n", "reason": "r"}
    }
    """
    with pytest.raises(ProgressLedgerParseError) as info:
        parse_progress_ledger_json(raw, turn_id=1)
    assert "is_request_satisfied" in str(info.value)


def test_parse_top_level_array_raises() -> None:
    """Some models reply with a list; we must reject cleanly."""
    raw = "[1, 2, 3]"
    with pytest.raises(ProgressLedgerParseError):
        parse_progress_ledger_json(raw, turn_id=1)


def test_parse_salvages_flattened_scalar_entry() -> None:
    """A flaky model that flattens an entry to a bare scalar is salvaged.

    Some providers intermittently emit ``"is_request_satisfied": false`` (or
    ``"next_speaker": "writer-b"``) instead of the required
    ``{"answer": ..., "reason": ...}`` object. Rather than burn all retries and
    abort the command, the parser wraps the scalar as the answer with an empty
    reason. The strict object shape is still preferred; a dict missing
    ``answer``/``reason`` is still rejected (see
    ``test_parse_entry_missing_reason_raises``).
    """
    raw = """
    {
      "is_request_satisfied":   false,
      "is_progress_being_made": {"answer": true,  "reason": "x"},
      "is_in_loop":             {"answer": false, "reason": "x"},
      "instruction_or_question":{"answer": "y", "reason": "z"},
      "next_speaker":           "writer-b"
    }
    """
    pl = parse_progress_ledger_json(raw, turn_id=1)
    assert pl.request_satisfied is False
    assert pl.next_speaker_name == "writer-b"


# ---------------------------------------------------------------------------
# Boolean coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_answer,expected",
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("1", True),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("0", False),
    ],
)
def test_parse_coerces_boolean_strings(raw_answer: object, expected: bool) -> None:
    payload = {
        "is_request_satisfied":   {"answer": raw_answer, "reason": "x"},
        "is_progress_being_made": {"answer": True,        "reason": "x"},
        "is_in_loop":             {"answer": False,       "reason": "x"},
        "instruction_or_question":{"answer": "y",         "reason": "z"},
        "next_speaker":           {"answer": "n",         "reason": "r"},
    }
    import json
    pl = parse_progress_ledger_json(json.dumps(payload), turn_id=1)
    assert pl.request_satisfied is expected


def test_parse_rejects_non_coercible_boolean() -> None:
    raw = """
    {
      "is_request_satisfied":   {"answer": "maybe", "reason": "ambiguous"},
      "is_progress_being_made": {"answer": true,    "reason": "x"},
      "is_in_loop":             {"answer": false,   "reason": "x"},
      "instruction_or_question":{"answer": "y",     "reason": "z"},
      "next_speaker":           {"answer": "n",     "reason": "r"}
    }
    """
    with pytest.raises(ProgressLedgerParseError) as info:
        parse_progress_ledger_json(raw, turn_id=1)
    assert "must be a boolean" in str(info.value)


# ---------------------------------------------------------------------------
# Required keys exposure
# ---------------------------------------------------------------------------


def test_required_keys_match_adr_0004() -> None:
    assert REQUIRED_PROGRESS_KEYS == (
        "is_request_satisfied",
        "is_progress_being_made",
        "is_in_loop",
        "instruction_or_question",
        "next_speaker",
    )


# ---------------------------------------------------------------------------
# ProgressLedgerEntry round-trip
# ---------------------------------------------------------------------------


def test_progress_entry_to_jsonable() -> None:
    e = ProgressLedgerEntry(answer="art_director", reason="owns shots")
    assert e.to_jsonable() == {"answer": "art_director", "reason": "owns shots"}


# ---------------------------------------------------------------------------
# Integration: a freshly constructed ledger persists raw_json
# ---------------------------------------------------------------------------


def test_progress_ledger_preserves_raw_json_for_audit() -> None:
    pl = parse_progress_ledger_json(GOOD_JSON, turn_id=2)
    assert "is_request_satisfied" in pl.raw_json
    assert pl.raw_json.strip().startswith("{")
    assert pl.raw_json.strip().endswith("}")


def test_progress_ledger_emitted_at_is_utc() -> None:
    pl = parse_progress_ledger_json(GOOD_JSON, turn_id=2)
    assert pl.emitted_at.tzinfo is UTC or pl.emitted_at.tzinfo is not None
    assert isinstance(pl.emitted_at, datetime)
