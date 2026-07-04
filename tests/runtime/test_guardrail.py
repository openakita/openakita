"""Tests for :mod:`openakita.runtime.guardrail`.

Phase 3 commit 5. Asserts:

* runner returns OK when no guardrails are registered;
* OK when every guardrail passes;
* RETRY when one guardrail returns RETRY and the rest pass;
* HARD_FAIL trumps RETRY, but every failure is collected so the
  supervisor can render them in a stream event;
* builtins:
    - MinLengthGuardrail / MaxLengthGuardrail RETRY on out-of-bounds;
    - RequiredFieldsGuardrail HARD_FAIL on non-JSON, RETRY on missing
      fields;
    - RegexGuardrail RETRY when pattern does not match.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from openakita.runtime.guardrail import (
    Guardrail,
    GuardrailContext,
    GuardrailRunner,
    GuardrailVerdict,
    MaxLengthGuardrail,
    MinLengthGuardrail,
    RegexGuardrail,
    RequiredFieldsGuardrail,
)
from openakita.runtime.supervisor import DelegationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(message: str = "ok") -> DelegationResult:
    return DelegationResult(success=True, speaker="node_a", message=message)


def _ctx() -> GuardrailContext:
    return GuardrailContext(speaker="art_director", instruction="i")


# ---------------------------------------------------------------------------
# Empty / pass-through
# ---------------------------------------------------------------------------


def test_runner_with_no_guardrails_returns_ok() -> None:
    decision = GuardrailRunner().evaluate(_result(), _ctx())
    assert decision.verdict is GuardrailVerdict.OK
    assert decision.failures == []


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _AlwaysOK(Guardrail):
    name: str = "ok_one"

    def check(self, result, ctx) -> tuple[GuardrailVerdict, str]:  # type: ignore[override]
        return GuardrailVerdict.OK, ""


@dataclass(frozen=True)
class _AlwaysRetry(Guardrail):
    name: str = "retry_one"
    reason: str = "please rewrite"

    def check(self, result, ctx) -> tuple[GuardrailVerdict, str]:  # type: ignore[override]
        return GuardrailVerdict.RETRY, self.reason


@dataclass(frozen=True)
class _AlwaysHardFail(Guardrail):
    name: str = "hard_one"
    reason: str = "structurally wrong"

    def check(self, result, ctx) -> tuple[GuardrailVerdict, str]:  # type: ignore[override]
        return GuardrailVerdict.HARD_FAIL, self.reason


def test_all_pass_returns_ok() -> None:
    runner = GuardrailRunner([_AlwaysOK(), _AlwaysOK(name="ok_two")])
    decision = runner.evaluate(_result(), _ctx())
    assert decision.verdict is GuardrailVerdict.OK
    assert decision.failures == []


def test_one_retry_among_passes_returns_retry() -> None:
    runner = GuardrailRunner([_AlwaysOK(), _AlwaysRetry(reason="fix prose")])
    decision = runner.evaluate(_result(), _ctx())
    assert decision.verdict is GuardrailVerdict.RETRY
    assert decision.failures == [("retry_one", "fix prose")]


def test_hard_fail_trumps_retry_but_collects_both() -> None:
    runner = GuardrailRunner(
        [_AlwaysRetry(reason="r1"), _AlwaysHardFail(reason="hf"), _AlwaysOK()]
    )
    decision = runner.evaluate(_result(), _ctx())
    assert decision.verdict is GuardrailVerdict.HARD_FAIL
    failure_names = [name for name, _ in decision.failures]
    assert failure_names == ["retry_one", "hard_one"]


# ---------------------------------------------------------------------------
# Builtin: length
# ---------------------------------------------------------------------------


def test_min_length_retry_when_too_short() -> None:
    g = MinLengthGuardrail(n=10)
    runner = GuardrailRunner([g])
    decision = runner.evaluate(_result(message="short"), _ctx())
    assert decision.verdict is GuardrailVerdict.RETRY
    assert "minimum" in decision.failures[0][1]


def test_min_length_ok_at_threshold() -> None:
    g = MinLengthGuardrail(n=5)
    runner = GuardrailRunner([g])
    decision = runner.evaluate(_result(message="exact"), _ctx())
    assert decision.verdict is GuardrailVerdict.OK


def test_max_length_retry_when_too_long() -> None:
    g = MaxLengthGuardrail(n=4)
    runner = GuardrailRunner([g])
    decision = runner.evaluate(_result(message="abcdefg"), _ctx())
    assert decision.verdict is GuardrailVerdict.RETRY


# ---------------------------------------------------------------------------
# Builtin: required fields
# ---------------------------------------------------------------------------


def test_required_fields_hard_fail_when_not_json() -> None:
    g = RequiredFieldsGuardrail(fields=("title",))
    runner = GuardrailRunner([g])
    decision = runner.evaluate(_result(message="not json at all"), _ctx())
    assert decision.verdict is GuardrailVerdict.HARD_FAIL
    assert "JSON" in decision.failures[0][1]


def test_required_fields_hard_fail_when_top_level_array() -> None:
    g = RequiredFieldsGuardrail(fields=("title",))
    runner = GuardrailRunner([g])
    decision = runner.evaluate(_result(message=json.dumps([1, 2])), _ctx())
    assert decision.verdict is GuardrailVerdict.HARD_FAIL


def test_required_fields_retry_when_field_missing() -> None:
    g = RequiredFieldsGuardrail(fields=("title", "shots"))
    runner = GuardrailRunner([g])
    decision = runner.evaluate(
        _result(message=json.dumps({"title": "ok"})), _ctx()
    )
    assert decision.verdict is GuardrailVerdict.RETRY
    assert "shots" in decision.failures[0][1]


def test_required_fields_ok_when_all_present() -> None:
    g = RequiredFieldsGuardrail(fields=("title",))
    runner = GuardrailRunner([g])
    decision = runner.evaluate(
        _result(message=json.dumps({"title": "ok", "extra": 1})), _ctx()
    )
    assert decision.verdict is GuardrailVerdict.OK


# ---------------------------------------------------------------------------
# Builtin: regex
# ---------------------------------------------------------------------------


def test_regex_retry_when_no_match() -> None:
    g = RegexGuardrail(pattern=r"\bSHOT_\d{3}\b")
    runner = GuardrailRunner([g])
    decision = runner.evaluate(_result(message="forgot the id"), _ctx())
    assert decision.verdict is GuardrailVerdict.RETRY


def test_regex_ok_with_match_and_flags() -> None:
    g = RegexGuardrail(pattern=r"^shot_\d+", flags=re.IGNORECASE | re.MULTILINE)
    runner = GuardrailRunner([g])
    decision = runner.evaluate(_result(message="SHOT_007 begins"), _ctx())
    assert decision.verdict is GuardrailVerdict.OK


# ---------------------------------------------------------------------------
# Add API
# ---------------------------------------------------------------------------


def test_add_extends_runner() -> None:
    runner = GuardrailRunner()
    assert len(runner) == 0
    runner.add(MinLengthGuardrail(n=1))
    runner.add(MaxLengthGuardrail(n=10))
    assert len(runner) == 2


def test_decision_to_jsonable() -> None:
    runner = GuardrailRunner([_AlwaysRetry(reason="please rewrite")])
    decision = runner.evaluate(_result(), _ctx())
    j = decision.to_jsonable()
    assert j["verdict"] == "retry"
    assert j["failures"] == [["retry_one", "please rewrite"]]
