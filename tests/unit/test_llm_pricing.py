"""Fix-5 回归测试：内置 pricing 表 + endpoint 回退。"""

from __future__ import annotations

from openakita.llm.pricing import list_builtin_prices, lookup_builtin_price
from openakita.llm.types import EndpointConfig


def test_lookup_returns_none_for_unknown_model():
    assert lookup_builtin_price("openai", "totally-fake-model") is None


def test_lookup_returns_none_when_model_empty():
    assert lookup_builtin_price("openai", "") is None
    assert lookup_builtin_price("openai", None) is None


def test_lookup_dashscope_qwen3_5_plus():
    tier = lookup_builtin_price("dashscope", "dashscope-qwen3.5-plus-thinking")
    assert tier is not None
    assert tier["currency"] == "CNY"
    assert tier["input_price"] > 0
    assert tier["output_price"] > tier["input_price"]
    assert tier["source"] == "builtin"


def test_lookup_anthropic_claude_sonnet():
    tier = lookup_builtin_price("anthropic", "claude-sonnet-4-20250514")
    assert tier is not None
    assert tier["currency"] == "USD"


def test_lookup_falls_back_across_providers_when_provider_unknown():
    """Unknown provider slug — should still match by model substring."""
    tier = lookup_builtin_price("some-proxy", "claude-3-5-sonnet-latest")
    assert tier is not None
    assert tier["currency"] == "USD"


def test_lookup_prefers_longest_match():
    """claude-3-5-sonnet should win over claude (if both were registered)."""
    tier = lookup_builtin_price("anthropic", "claude-3-5-sonnet-20241022")
    assert tier is not None
    assert tier["matched_key"] == "claude-3-5-sonnet"


def test_endpoint_calculate_cost_with_user_pricing_wins():
    ep = EndpointConfig(
        name="test",
        provider="dashscope",
        api_type="openai",
        base_url="x",
        model="qwen3.5-plus",
        pricing_tiers=[{"max_input": -1, "input_price": 100, "output_price": 200}],
    )
    cost = ep.calculate_cost(1_000_000, 1_000_000)
    assert cost == 300.0  # 100 + 200


def test_endpoint_calculate_cost_falls_back_to_builtin_when_no_user_pricing():
    ep = EndpointConfig(
        name="test",
        provider="dashscope",
        api_type="openai",
        base_url="x",
        model="qwen3.5-plus",
        pricing_tiers=None,
    )
    cost = ep.calculate_cost(1_000_000, 100_000)
    assert cost > 0


def test_endpoint_calculate_cost_returns_zero_when_unknown_model():
    ep = EndpointConfig(
        name="test",
        provider="openai",
        api_type="openai",
        base_url="x",
        model="fake-model",
        pricing_tiers=None,
    )
    assert ep.calculate_cost(1000, 1000) == 0.0


def test_endpoint_calculate_cost_or_none_returns_none_when_unknown():
    ep = EndpointConfig(
        name="test",
        provider="openai",
        api_type="openai",
        base_url="x",
        model="fake-model",
        pricing_tiers=None,
    )
    assert ep.calculate_cost_or_none(1000, 1000) is None


def test_endpoint_calculate_cost_or_none_returns_value_when_user_priced():
    ep = EndpointConfig(
        name="test",
        provider="anywhere",
        api_type="openai",
        base_url="x",
        model="anything",
        pricing_tiers=[{"max_input": -1, "input_price": 1, "output_price": 2}],
    )
    cost = ep.calculate_cost_or_none(1_000_000, 1_000_000)
    assert cost == 3.0


def test_get_effective_pricing_user_takes_precedence():
    ep = EndpointConfig(
        name="test",
        provider="dashscope",
        api_type="openai",
        base_url="x",
        model="qwen-plus",
        pricing_tiers=[{"max_input": -1, "input_price": 999, "output_price": 999}],
        price_currency="CNY",
    )
    tier = ep.get_effective_pricing()
    assert tier is not None
    assert tier["source"] == "user"
    assert tier["input_price"] == 999


def test_get_effective_pricing_falls_back_to_builtin():
    ep = EndpointConfig(
        name="test",
        provider="dashscope",
        api_type="openai",
        base_url="x",
        model="qwen-plus",
        pricing_tiers=None,
    )
    tier = ep.get_effective_pricing()
    assert tier is not None
    assert tier["source"] == "builtin"


def test_get_effective_pricing_returns_none_for_unknown():
    ep = EndpointConfig(
        name="test",
        provider="x",
        api_type="openai",
        base_url="x",
        model="fake",
        pricing_tiers=None,
    )
    assert ep.get_effective_pricing() is None


def test_list_builtin_prices_has_known_providers():
    table = list_builtin_prices()
    for required in ("anthropic", "openai", "dashscope"):
        assert required in table
        assert isinstance(table[required], list)
        assert all("input_price" in row and "output_price" in row for row in table[required])
