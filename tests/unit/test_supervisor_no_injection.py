"""
验证 RuntimeSupervisor token anomaly 和 ResourceBudget 不再向对话注入消息。

背景：token anomaly 警告曾以 user message 形式累积注入 LLM 对话，
导致模型行为退化（缩短回答、减少工具调用）。已移除全部注入逻辑。
"""

from openakita.core.supervisor import (
    Intervention,
    InterventionLevel,
    PatternType,
    RuntimeSupervisor,
    TOKEN_ANOMALY_THRESHOLD,
)
from openakita.core.resource_budget import ResourceBudget, BudgetConfig


class TestTokenAnomalyNoInjection:
    """token anomaly 检测不应注入对话"""

    def test_below_threshold_no_intervention(self):
        sup = RuntimeSupervisor()
        sup.record_token_usage(TOKEN_ANOMALY_THRESHOLD - 1)
        result = sup.evaluate(0)
        assert result is None

    def test_above_threshold_returns_intervention(self):
        sup = RuntimeSupervisor()
        sup.record_token_usage(TOKEN_ANOMALY_THRESHOLD + 10000)
        result = sup.evaluate(0)
        assert result is not None
        assert result.pattern == PatternType.TOKEN_ANOMALY

    def test_above_threshold_no_prompt_injection(self):
        sup = RuntimeSupervisor()
        sup.record_token_usage(TOKEN_ANOMALY_THRESHOLD + 10000)
        result = sup.evaluate(0)
        assert result is not None
        assert result.should_inject_prompt is False
        assert result.prompt_injection == ""

    def test_repeated_anomaly_still_no_injection(self):
        """即使连续多轮超阈值，也不应注入对话"""
        sup = RuntimeSupervisor()
        injections = []
        for i in range(10):
            sup.record_token_usage(TOKEN_ANOMALY_THRESHOLD + 5000 * (i + 1))
            result = sup.evaluate(i)
            if result and result.should_inject_prompt:
                injections.append(result)
        assert len(injections) == 0

    def test_event_still_recorded(self):
        """虽然不注入对话，但 SupervisionEvent 仍应记录"""
        sup = RuntimeSupervisor()
        sup.record_token_usage(TOKEN_ANOMALY_THRESHOLD + 1)
        sup.evaluate(0)
        events = sup.events
        token_events = [e for e in events if e.pattern == PatternType.TOKEN_ANOMALY]
        assert len(token_events) >= 1

    def test_disabled_supervisor_no_intervention(self):
        sup = RuntimeSupervisor(enabled=False)
        sup.record_token_usage(TOKEN_ANOMALY_THRESHOLD + 99999)
        result = sup.evaluate(0)
        assert result is None

    def test_reset_clears_state(self):
        sup = RuntimeSupervisor()
        sup.record_token_usage(TOKEN_ANOMALY_THRESHOLD + 1)
        sup.evaluate(0)
        sup.reset()
        assert len(sup.events) == 0


class TestBudgetWarningNoInjection:
    """ResourceBudget 预算警告不应返回任何注入文本"""

    def test_default_budget_returns_empty(self):
        budget = ResourceBudget(BudgetConfig())
        budget.start()
        assert budget.get_budget_prompt_warning() == ""

    def test_budget_with_limits_returns_empty(self):
        budget = ResourceBudget(BudgetConfig(max_tokens=100))
        budget.start()
        budget.record_tokens(90)
        assert budget.get_budget_prompt_warning() == ""

    def test_exceeded_budget_returns_empty(self):
        budget = ResourceBudget(BudgetConfig(max_tokens=100))
        budget.start()
        budget.record_tokens(200)
        assert budget.get_budget_prompt_warning() == ""

