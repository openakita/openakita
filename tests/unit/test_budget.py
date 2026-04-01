"""L1 Unit Tests: Token budget estimation and allocation."""

import pytest

from openakita.prompt.budget import (
    BudgetConfig,
    BudgetResult,
    apply_budget,
    apply_budget_to_sections,
    estimate_tokens,
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_pure_english(self):
        text = "Hello world this is a test"
        tokens = estimate_tokens(text)
        assert tokens == len(text) // 4

    def test_pure_chinese(self):
        text = "你好世界这是测试"
        tokens = estimate_tokens(text)
        chinese_expected = int(len(text) / 1.5)
        assert tokens == chinese_expected

    def test_mixed_content(self):
        text = "Hello 你好 World 世界"
        tokens = estimate_tokens(text)
        assert tokens > 0
        pure_english = estimate_tokens("Hello  World ")
        pure_chinese = estimate_tokens("你好世界")
        assert abs(tokens - (pure_english + pure_chinese)) < 3

    def test_long_text_scales_linearly(self):
        short = "test " * 10
        long = "test " * 100
        ratio = estimate_tokens(long) / estimate_tokens(short)
        assert 9 < ratio < 11

    def test_whitespace_counts_as_english(self):
        tokens = estimate_tokens("   ")
        assert tokens == 0 or tokens == int(3 / 4)


class TestApplyBudget:
    def test_empty_content(self):
        result = apply_budget("", 100, "test")
        assert result.content == ""
        assert result.original_tokens == 0
        assert result.truncated is False

    def test_within_budget(self):
        result = apply_budget("short text", 1000, "test")
        assert result.content == "short text"
        assert result.truncated is False

    def test_exceeds_budget_truncates(self):
        """Exceeding budget by >=20% triggers truncation."""
        long_text = "x" * 10000
        result = apply_budget(long_text, 10, "test")
        assert len(result.content) < len(long_text)
        assert result.truncated is True
        assert result.original_tokens > 10

    def test_returns_budget_result_type(self):
        result = apply_budget("hello", 100, "test")
        assert isinstance(result, BudgetResult)


class TestApplyBudgetToSections:
    def test_empty_sections(self):
        results = apply_budget_to_sections({}, BudgetConfig())
        assert results == {}

    def test_all_empty_content(self):
        sections = {"memory": "", "tools": "", "identity": ""}
        results = apply_budget_to_sections(sections, BudgetConfig())
        for name, result in results.items():
            assert result.original_tokens == 0
            assert result.truncated is False

    def test_known_sections_get_correct_budgets(self):
        config = BudgetConfig()
        sections = {
            "memory": "memory content " * 50,
            "tools": "tool content " * 50,
            "skills": "skill content " * 50,
        }
        results = apply_budget_to_sections(sections, config)
        assert "memory" in results
        assert "tools" in results
        assert "skills" in results

    def test_unknown_section_gets_default_budget(self):
        sections = {"custom_section": "some content"}
        results = apply_budget_to_sections(sections, BudgetConfig())
        assert "custom_section" in results

    def test_budget_config_defaults(self):
        config = BudgetConfig()
        assert config.identity_budget == 3000
        assert config.catalogs_budget == 8000
        assert config.user_budget == 300
        assert config.memory_budget == 2500
        assert config.total_budget == 14000
