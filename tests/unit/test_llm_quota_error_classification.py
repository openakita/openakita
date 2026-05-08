from types import SimpleNamespace

from openakita.llm.client import _friendly_error_hint
from openakita.llm.error_types import FailoverReason
from openakita.llm.providers.base import LLMProvider
from openakita.llm.providers.openai import _humanize_upstream_error

DEEPSEEK_INSUFFICIENT_BALANCE = (
    'API error (402): {"error":{"message":"Insufficient Balance",'
    '"type":"unknown_error","param":null,"code":"invalid_request_error"}}'
)


def test_insufficient_balance_takes_priority_over_invalid_request():
    assert LLMProvider._classify_error(DEEPSEEK_INSUFFICIENT_BALANCE) == FailoverReason.QUOTA


def test_humanized_402_keeps_quota_marker():
    body = '{"error":{"message":"Insufficient Balance","code":"invalid_request_error"}}'
    message = _humanize_upstream_error(402, body)

    assert "余额不足" in message
    assert "quota_exhausted" in message
    assert LLMProvider._classify_error(message) == FailoverReason.QUOTA


def test_quota_hint_does_not_suggest_model_compatibility():
    provider = SimpleNamespace(
        error_category=FailoverReason.QUOTA,
        _last_error=DEEPSEEK_INSUFFICIENT_BALANCE,
    )

    hint = _friendly_error_hint([provider])

    assert "配额耗尽" in hint
    assert "充值" in hint
    assert "请求格式错误" not in hint
    assert "模型兼容" not in hint


def test_last_error_quota_keyword_is_enough_for_hint():
    hint = _friendly_error_hint(
        failed_providers=None,
        last_error=DEEPSEEK_INSUFFICIENT_BALANCE,
    )

    assert "配额耗尽" in hint
    assert "请求格式错误" not in hint
