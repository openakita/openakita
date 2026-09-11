"""Cache reads are subsets of DeepSeek input, in every response path."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from openakita.core import token_tracking
from openakita.core._brain_runtime import Brain
from openakita.core.stream_accumulator import StreamAccumulator
from openakita.llm.providers.openai import OpenAIProvider
from openakita.llm.types import EndpointConfig, LLMResponse, StopReason, Usage


def endpoint(api_type="openai"):
    return EndpointConfig(
        name="test",
        provider="deepseek",
        api_type=api_type,
        base_url="https://example.invalid",
        model="deepseek-v4-flash",
        api_key="test",
        pricing_tiers=[
            {"max_input": -1, "input_price": 2, "output_price": 3, "cache_read_price": 0.2}
        ],
    )


@pytest.mark.parametrize(
    "cache_fields",
    [
        {"prompt_cache_hit_tokens": 640, "prompt_cache_miss_tokens": 360},
        {"prompt_tokens_details": {"cached_tokens": 640}},
        {"cached_tokens": 640},
    ],
)
def test_cache_usage_matches_across_all_response_paths(cache_fields):
    provider = OpenAIProvider(endpoint())
    usage = {"prompt_tokens": 1000, "completion_tokens": 10, **cache_fields}
    response = provider._parse_response(
        {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": usage,
        }
    )
    expected = {
        "input_tokens": 1000,
        "output_tokens": 10,
        "cache_read_input_tokens": 640,
        "cache_creation_input_tokens": 0,
    }
    assert asdict(response.usage) == expected
    for choices in ([], [{"delta": {}, "finish_reason": "stop"}]):
        event = provider._convert_stream_event({"choices": choices, "usage": usage})
        assert event["usage"] == expected
        accumulator = StreamAccumulator()
        accumulator.feed(event)
        assert accumulator.usage == expected


@pytest.mark.parametrize(
    "cache_fields,expected",
    [
        ({}, 0),
        ({"prompt_cache_hit_tokens": 0, "cached_tokens": 640}, 0),
        ({"prompt_tokens_details": None, "cached_tokens": "640"}, 640),
        ({"prompt_cache_hit_tokens": "bad"}, 0),
        ({"prompt_cache_hit_tokens": -1}, 0),
    ],
)
def test_missing_or_invalid_cache_counters_do_not_break_stream(cache_fields, expected):
    event = OpenAIProvider(endpoint())._convert_stream_event(
        {
            "choices": [],
            "usage": {"prompt_tokens": 1000, **cache_fields},
        }
    )
    assert event["usage"]["cache_read_input_tokens"] == expected
    assert event["usage"]["input_tokens"] == 1000


@pytest.mark.parametrize(
    "api_type,input_tokens,includes_cache",
    [
        ("openai", 1000, True),
        ("openai_responses", 1000, True),
        ("anthropic", 360, False),
    ],
)
def test_cache_hits_are_not_charged_or_budgeted_twice(
    api_type, input_tokens, includes_cache, monkeypatch
):
    assert endpoint(api_type).calculate_cost(input_tokens, 10, 640) == pytest.approx(0.000878)
    monkeypatch.setattr(token_tracking, "_initialized", False)
    budget = token_tracking.TokenBudgetState(name="test", max_tokens=1500)
    token = token_tracking.set_token_budget(budget)
    try:
        token_tracking.record_usage(
            input_tokens=input_tokens,
            output_tokens=10,
            cache_read_tokens=640,
            input_tokens_include_cache=includes_cache,
        )
        assert budget.used_tokens == 1010
        assert not budget.exceeded
    finally:
        token_tracking.reset_token_budget(token)


def test_compiler_usage_resolves_its_own_endpoint_for_cache_accounting(monkeypatch):
    monkeypatch.setattr(token_tracking, "_initialized", False)
    budget = token_tracking.TokenBudgetState(name="compiler", max_tokens=1500)
    brain = SimpleNamespace(
        _acc_calls=0,
        _acc_tokens_in=0,
        _acc_tokens_out=0,
        _llm_client=SimpleNamespace(endpoints=[]),
        _compiler_client=SimpleNamespace(endpoints=[endpoint()]),
    )
    response = LLMResponse(
        id="test",
        content=[],
        model="deepseek-v4-flash",
        endpoint_name="test",
        stop_reason=StopReason.END_TURN,
        usage=Usage(input_tokens=1000, output_tokens=10, cache_read_input_tokens=640),
    )
    token = token_tracking.set_token_budget(budget)
    try:
        Brain._record_usage(brain, response)
        assert budget.used_tokens == 1010
        assert brain._acc_calls == 1
    finally:
        token_tracking.reset_token_budget(token)


def test_deepseek_hit_and_miss_can_reconstruct_missing_total():
    event = OpenAIProvider(endpoint())._convert_stream_event(
        {
            "choices": [],
            "usage": {"prompt_cache_hit_tokens": 640, "prompt_cache_miss_tokens": 360},
        }
    )
    assert event["usage"]["input_tokens"] == 1000
    assert event["usage"]["cache_read_input_tokens"] == 640


def test_pricing_tier_uses_total_input_before_cache_discount():
    ep = endpoint()
    ep.pricing_tiers = [
        {"max_input": 500, "input_price": 1, "output_price": 1, "cache_read_price": 0.1},
        {"max_input": -1, "input_price": 2, "output_price": 3, "cache_read_price": 0.2},
    ]
    assert ep.calculate_cost(1000, 10, 640) == pytest.approx(0.000878)
