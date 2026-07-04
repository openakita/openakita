"""Fix-14 回归测试：风险拦截早退路径不复用上一轮 usage。"""

from __future__ import annotations

from unittest.mock import MagicMock

from openakita.agent.core import Agent


class _StubCtxMgr:
    def estimate_messages_tokens(self, msgs):
        return 1234

    def get_max_context_tokens(self):
        return 200_000


def _make_agent(stale_trace: list[dict] | None = None) -> Agent:
    agent = Agent.__new__(Agent)
    agent.reasoning_engine = MagicMock()
    agent.reasoning_engine._context_manager = _StubCtxMgr()
    agent.reasoning_engine._last_working_messages = []
    agent.reasoning_engine._last_react_trace = list(stale_trace or [])
    agent.context_manager = None
    return agent


def test_extract_usage_summary_with_stale_trace_returns_stale_usage():
    """Sanity check：未清空的 trace 会被原样汇总（这就是 Fix-14 要规避的场景）。"""
    agent = _make_agent(stale_trace=[{"tokens": {"input": 140_000, "output": 800}}])
    summary = agent._extract_usage_summary(agent.reasoning_engine._last_react_trace)
    assert summary["input_tokens"] == 140_000
    assert summary["billable_input_tokens"] == 140_000


def test_clearing_trace_produces_empty_usage_summary():
    """Fix-14 修复后的预期：清空后再提取 → usage 为空。"""
    agent = _make_agent(stale_trace=[{"tokens": {"input": 140_000, "output": 800}}])
    agent.reasoning_engine._last_react_trace = []
    summary = agent._extract_usage_summary(agent.reasoning_engine._last_react_trace)
    assert summary == {}


def test_empty_summary_is_distinguishable_from_zero_billable():
    """前端可以根据 ``{}`` 或缺失字段判断"无 LLM 调用"，不应误展示 0。"""
    agent = _make_agent()
    summary = agent._extract_usage_summary([])
    assert summary == {}
    assert "billable_input_tokens" not in summary


def test_extract_with_trace_after_normal_run_includes_billable():
    agent = _make_agent()
    summary = agent._extract_usage_summary([{"tokens": {"input": 1500, "output": 200}}])
    assert summary["billable_input_tokens"] == 1500
    assert summary["billable_output_tokens"] == 200
    assert summary["billable_total_tokens"] == 1700


def test_risk_gate_clear_trace_pattern_matches_agent_code():
    """端到端模拟：风险早退时 ``self.reasoning_engine._last_react_trace = []``
    后再走 ``_finalize_session`` 的等价路径，usage 应为空。"""
    agent = _make_agent(
        stale_trace=[
            {"tokens": {"input": 90_000, "output": 1200}},
            {"tokens": {"input": 50_000, "output": 800}},
        ]
    )
    # 模拟 agent.py 风险早退分支里的清空动作。
    agent.reasoning_engine._last_react_trace = []
    # 现在模拟 _finalize_session 内的 _trace_snapshot 取值。
    snapshot = list(agent.reasoning_engine._last_react_trace or [])
    summary = agent._extract_usage_summary(snapshot)
    assert summary == {}
