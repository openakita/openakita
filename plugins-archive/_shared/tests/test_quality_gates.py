"""Tests for _shared.quality_gates."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from _shared import (
    ErrorCoach,
    GateResult,
    GateStatus,
    QualityGates,
    RenderedError,
)


class _Result(BaseModel):
    id: str
    ok: bool


def test_g1_pass_when_all_required_present() -> None:
    r = QualityGates.check_input_integrity(
        {"prompt": "hi", "n": 1}, required=["prompt"], non_empty_strings=["prompt"],
    )
    assert r.passed
    assert r.gate_id == "G1.input_integrity"


def test_g1_fail_when_field_missing() -> None:
    r = QualityGates.check_input_integrity({}, required=["prompt"])
    assert r.blocking
    assert "missing=['prompt']" in r.message


def test_g1_fail_when_required_string_is_whitespace() -> None:
    r = QualityGates.check_input_integrity(
        {"prompt": "   "}, required=["prompt"], non_empty_strings=["prompt"],
    )
    assert r.blocking
    assert "empty=['prompt']" in r.message


def test_g2_pydantic_pass() -> None:
    r = QualityGates.check_output_schema({"id": "abc", "ok": True}, schema=_Result)
    assert r.passed


def test_g2_pydantic_fail() -> None:
    r = QualityGates.check_output_schema({"id": "abc"}, schema=_Result)
    assert r.blocking


def test_g2_required_keys_when_no_schema() -> None:
    r = QualityGates.check_output_schema({"a": 1}, required_keys=["a", "b"])
    assert r.blocking
    assert "['b']" in r.message


def test_g3_pass_for_well_formed_error() -> None:
    rendered = RenderedError(
        pattern_id="rate_limit",
        cause_category="请求太频繁",
        problem="供应商限流了",
        evidence="HTTP 429",
        next_step="等 10 秒再点重试",
    )
    r = QualityGates.check_error_readability(rendered)
    assert r.passed


def test_g3_warn_on_fallback_pattern() -> None:
    coach = ErrorCoach()
    rendered = coach.render(status=999, raw_message="weird")
    r = QualityGates.check_error_readability(rendered)
    assert r.status == GateStatus.WARN.value


def test_g3_fail_on_missing_next_step() -> None:
    rendered = RenderedError(
        pattern_id="x", cause_category="c", problem="p", evidence="e", next_step="",
    )
    r = QualityGates.check_error_readability(rendered)
    assert r.blocking
    assert "missing next_step" in r.message


def test_aggregate_blocking_dominates() -> None:
    a = GateResult(gate_id="G1", status=GateStatus.PASS.value, message="ok")
    b = GateResult(gate_id="G2", status=GateStatus.FAIL.value, message="bad")
    c = GateResult(gate_id="G3", status=GateStatus.WARN.value, message="meh")
    out = QualityGates.aggregate([a, b, c])
    assert out.blocking
    assert "G2" in out.message


def test_aggregate_pass_when_all_pass() -> None:
    out = QualityGates.aggregate([
        GateResult(gate_id="G1", status="pass", message="ok"),
        GateResult(gate_id="G2", status="pass", message="ok"),
    ])
    assert out.passed
