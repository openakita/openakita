"""Tests for _shared.intent_verifier."""

from __future__ import annotations

import pytest

from _shared import IntentVerifier


@pytest.mark.asyncio
async def test_verify_no_llm_returns_low_confidence() -> None:
    v = IntentVerifier()  # no llm_call
    res = await v.verify("做一个柯基跑步的视频")
    assert res.summary
    assert res.confidence == "low"
    assert any("LLM" in r for r in res.risks)


@pytest.mark.asyncio
async def test_verify_parses_clean_json() -> None:
    async def fake_llm(messages, **kw):
        return (
            '{"summary":"用户要做柯基跑步视频","clarifying_questions":[],'
            '"confidence":"high","risks":[]}'
        )
    v = IntentVerifier(llm_call=fake_llm)
    res = await v.verify("做一个柯基跑步的视频")
    assert "柯基" in res.summary
    assert res.confidence == "high"
    assert res.clarifying_questions == []
    assert res.risks == []


@pytest.mark.asyncio
async def test_verify_parses_code_fenced_json() -> None:
    async def fake_llm(messages, **kw):
        return "```json\n{\"summary\":\"X\",\"clarifying_questions\":[\"a\"],\"confidence\":\"medium\",\"risks\":[\"r1\"]}\n```"
    v = IntentVerifier(llm_call=fake_llm)
    res = await v.verify("foo")
    assert res.summary == "X"
    assert res.clarifying_questions == ["a"]
    assert res.confidence == "medium"
    assert res.risks == ["r1"]


@pytest.mark.asyncio
async def test_verify_truncates_questions_to_3() -> None:
    async def fake_llm(messages, **kw):
        return '{"summary":"X","clarifying_questions":["a","b","c","d","e"],"confidence":"medium","risks":[]}'
    v = IntentVerifier(llm_call=fake_llm)
    res = await v.verify("foo")
    assert len(res.clarifying_questions) == 3


@pytest.mark.asyncio
async def test_verify_llm_failure_falls_back() -> None:
    async def boom(messages, **kw):
        raise RuntimeError("boom")
    v = IntentVerifier(llm_call=boom)
    res = await v.verify("foo")
    assert res.confidence == "low"
    assert any("失败" in r for r in res.risks)


@pytest.mark.asyncio
async def test_verify_handles_dict_response() -> None:
    async def fake_llm(messages, **kw):
        return {"choices": [{"message": {"content": '{"summary":"S","clarifying_questions":[],"confidence":"high","risks":[]}'}}]}
    v = IntentVerifier(llm_call=fake_llm)
    res = await v.verify("foo")
    assert res.summary == "S"


def test_with_context_returns_new_instance() -> None:
    a = IntentVerifier()
    b = a.with_context("x")
    assert a is not b
