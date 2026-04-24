"""
验证小模型上下文窗口自适应修复。

测试 BudgetConfig.for_context_window() 和 get_max_context_tokens() 的行为。
"""

import pytest
from unittest.mock import MagicMock, PropertyMock

from openakita.prompt.budget import BudgetConfig, estimate_tokens
from openakita.core.context_utils import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    get_max_context_tokens,
    get_raw_context_window,
)


class TestBudgetConfigForContextWindow:
    """BudgetConfig.for_context_window 工厂方法测试"""

    def test_large_model_uses_default(self):
        config = BudgetConfig.for_context_window(200000)
        default = BudgetConfig()
        assert config.total_budget == default.total_budget
        assert config.identity_budget == default.identity_budget

    def test_zero_uses_default(self):
        config = BudgetConfig.for_context_window(0)
        default = BudgetConfig()
        assert config.total_budget == default.total_budget

    def test_negative_uses_default(self):
        config = BudgetConfig.for_context_window(-1)
        default = BudgetConfig()
        assert config.total_budget == default.total_budget

    def test_32k_uses_reduced_budget(self):
        """32K 模型应使用中间档预算，而非默认 21K"""
        config = BudgetConfig.for_context_window(32000)
        default = BudgetConfig()
        assert config.total_budget < default.total_budget
        assert config.total_budget <= 18000

    def test_64k_plus_uses_default(self):
        config = BudgetConfig.for_context_window(65000)
        default = BudgetConfig()
        assert config.total_budget == default.total_budget

    def test_16k_model_reduced_budget(self):
        config = BudgetConfig.for_context_window(16000)
        assert config.total_budget <= 12000
        assert config.total_budget > 0
        assert config.identity_budget < BudgetConfig().identity_budget

    def test_8k_model_compressed_budget(self):
        config = BudgetConfig.for_context_window(8000)
        assert config.total_budget <= 8000
        assert config.identity_budget <= 2500

    def test_4k_model_minimal_budget(self):
        config = BudgetConfig.for_context_window(4096)
        assert config.total_budget <= 2000
        assert config.identity_budget <= 800
        assert config.catalogs_budget <= 800

    def test_2k_model_extreme_compression(self):
        config = BudgetConfig.for_context_window(2048)
        assert config.total_budget <= 2000

    def test_budget_scales_proportionally(self):
        """验证预算不超过 context_window 的 40%"""
        for ctx in [4096, 8192, 16384, 32000]:
            config = BudgetConfig.for_context_window(ctx)
            assert config.total_budget <= int(ctx * 0.40), (
                f"ctx={ctx}: total_budget={config.total_budget} > 40% of ctx={int(ctx*0.40)}"
            )

    def test_no_discontinuity_at_boundaries(self):
        """边界处预算变化不应有过大跳变"""
        prev_budget = 0
        for ctx in range(2000, 70000, 2000):
            config = BudgetConfig.for_context_window(ctx)
            if prev_budget > 0:
                ratio = config.total_budget / prev_budget
                assert ratio < 2.5, (
                    f"Discontinuity at ctx={ctx}: budget jumped {prev_budget} -> "
                    f"{config.total_budget} (ratio={ratio:.1f})"
                )
            prev_budget = config.total_budget


class TestGetMaxContextTokens:
    """get_max_context_tokens 共享函数测试"""

    def _make_brain(self, context_window: int, max_tokens: int = 4096):
        brain = MagicMock()
        brain.get_current_model_info.return_value = {"name": "test-ep"}
        ep = MagicMock()
        ep.name = "test-ep"
        ep.context_window = context_window
        ep.max_tokens = max_tokens
        brain._llm_client.endpoints = [ep]
        return brain

    def test_large_context_window(self):
        brain = self._make_brain(200000)
        result = get_max_context_tokens(brain)
        assert result > 100000

    def test_small_context_window_not_inflated(self):
        """核心修复: 4K 模型不应被膨胀为 200K"""
        brain = self._make_brain(4096, max_tokens=512)
        result = get_max_context_tokens(brain)
        assert result < 4096, f"4K model got inflated to {result}"
        assert result > 0

    def test_8k_context_window_respected(self):
        brain = self._make_brain(8192, max_tokens=1024)
        result = get_max_context_tokens(brain)
        assert result < 8192
        assert result > 2000

    def test_zero_context_window_uses_fallback(self):
        brain = self._make_brain(0)
        result = get_max_context_tokens(brain)
        assert result > 100000

    def test_missing_context_window_uses_fallback(self):
        brain = MagicMock()
        brain.get_current_model_info.return_value = {"name": "test-ep"}
        ep = MagicMock()
        ep.name = "test-ep"
        del ep.context_window  # simulate missing attribute
        type(ep).context_window = PropertyMock(side_effect=AttributeError)
        ep.max_tokens = 4096
        brain._llm_client.endpoints = [ep]
        result = get_max_context_tokens(brain)
        assert result > 100000

    def test_exception_returns_default(self):
        brain = MagicMock()
        brain.get_current_model_info.side_effect = RuntimeError("no model")
        result = get_max_context_tokens(brain)
        assert result == DEFAULT_MAX_CONTEXT_TOKENS


class TestGetRawContextWindow:
    """get_raw_context_window 共享函数测试"""

    def test_returns_configured_value(self):
        brain = MagicMock()
        brain.get_current_model_info.return_value = {"name": "ep1"}
        ep = MagicMock()
        ep.name = "ep1"
        ep.context_window = 4096
        brain._llm_client.endpoints = [ep]
        assert get_raw_context_window(brain) == 4096

    def test_returns_zero_on_error(self):
        brain = MagicMock()
        brain.get_current_model_info.side_effect = RuntimeError
        assert get_raw_context_window(brain) == 0


class TestEndToEndScenario:
    """端到端场景: 模拟小模型的完整流程"""

    def test_4k_model_prompt_fits(self):
        """4K 模型的系统提示词预算应远小于 4K"""
        config = BudgetConfig.for_context_window(4096)
        assert config.total_budget <= 2000

        max_prompt = (
            config.identity_budget
            + config.catalogs_budget
            + config.user_budget
            + config.memory_budget
        )
        assert max_prompt <= 4096, f"Prompt budget sum {max_prompt} exceeds 4K"

    def test_8k_model_prompt_fits(self):
        """8K 模型的系统提示词预算应合理"""
        config = BudgetConfig.for_context_window(8192)
        max_prompt = (
            config.identity_budget
            + config.catalogs_budget
            + config.user_budget
            + config.memory_budget
        )
        assert max_prompt <= 8192, f"Prompt budget sum {max_prompt} exceeds 8K"

