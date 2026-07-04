"""Fix-13 回归测试：_extract_usage_summary 双写新旧字段。"""

from __future__ import annotations

from unittest.mock import MagicMock

from openakita.agent.core import Agent


class _StubCtxMgr:
    def __init__(self, *, ctx_tokens: int, ctx_limit: int):
        self._ctx_tokens = ctx_tokens
        self._ctx_limit = ctx_limit

    def estimate_messages_tokens(self, msgs):
        return self._ctx_tokens

    def get_max_context_tokens(self):
        return self._ctx_limit


def _make_agent_with_ctx(ctx_tokens: int = 1234, ctx_limit: int = 200_000) -> Agent:
    agent = Agent.__new__(Agent)
    agent.reasoning_engine = MagicMock()
    agent.reasoning_engine._context_manager = _StubCtxMgr(
        ctx_tokens=ctx_tokens, ctx_limit=ctx_limit
    )
    agent.reasoning_engine._last_working_messages = [{"role": "user", "content": "hi"}]
    agent.context_manager = None
    return agent


def test_extract_usage_summary_empty_trace_returns_empty_dict():
    agent = _make_agent_with_ctx()
    assert agent._extract_usage_summary([]) == {}


def test_extract_usage_summary_includes_legacy_field_names():
    agent = _make_agent_with_ctx()
    trace = [
        {"tokens": {"input": 100, "output": 20}},
        {"tokens": {"input": 50, "output": 30}},
    ]
    summary = agent._extract_usage_summary(trace)
    assert summary["input_tokens"] == 150
    assert summary["output_tokens"] == 50
    assert summary["total_tokens"] == 200
    assert summary["context_tokens"] == 1234
    assert summary["context_limit"] == 200_000


def test_extract_usage_summary_includes_new_field_names():
    """Fix-13: 新字段必须与旧字段一一对应且数值相等。"""
    agent = _make_agent_with_ctx()
    trace = [{"tokens": {"input": 7, "output": 3}}]
    summary = agent._extract_usage_summary(trace)
    assert summary["billable_input_tokens"] == 7
    assert summary["billable_output_tokens"] == 3
    assert summary["billable_total_tokens"] == 10
    assert summary["history_context_tokens"] == 1234
    assert summary["history_context_limit"] == 200_000


def test_extract_usage_summary_field_pairs_consistent():
    agent = _make_agent_with_ctx()
    trace = [{"tokens": {"input": 11, "output": 22}}]
    summary = agent._extract_usage_summary(trace)
    assert summary["input_tokens"] == summary["billable_input_tokens"]
    assert summary["output_tokens"] == summary["billable_output_tokens"]
    assert summary["total_tokens"] == summary["billable_total_tokens"]
    assert summary["context_tokens"] == summary["history_context_tokens"]
    assert summary["context_limit"] == summary["history_context_limit"]


def test_extract_usage_summary_without_ctx_mgr_skips_context_fields():
    agent = Agent.__new__(Agent)
    agent.reasoning_engine = MagicMock()
    agent.reasoning_engine._context_manager = None
    agent.reasoning_engine._last_working_messages = []
    agent.context_manager = None
    summary = agent._extract_usage_summary([{"tokens": {"input": 5, "output": 1}}])
    assert summary["billable_total_tokens"] == 6
    assert "context_tokens" not in summary
    assert "history_context_tokens" not in summary
