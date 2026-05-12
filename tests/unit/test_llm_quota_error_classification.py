from types import SimpleNamespace

from openakita.llm.client import _friendly_error_hint
from openakita.llm.error_types import FailoverReason
from openakita.llm.providers.base import LLMProvider, RPMRateLimiter
from openakita.llm.providers.openai import OpenAIProvider, _humanize_upstream_error
from openakita.llm.types import EndpointConfig, LLMError
from openakita.setup_center.bridge import _model_list_headers

DEEPSEEK_INSUFFICIENT_BALANCE = (
    'API error (402): {"error":{"message":"Insufficient Balance",'
    '"type":"unknown_error","param":null,"code":"invalid_request_error"}}'
)

XFYUN_APP_NO_AUTH = (
    'API error (500): {"error":{"message":"xunfei response error: '
    "AppIdNoAuthError:`app``astron-code-latest`tokens.total;business.total "
    "_F`Lacf`20000000`CL#`-1`U`20084551`N\","
    '"type":"one_api_error","code":"11200"}}'
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


def test_xfyun_app_no_auth_is_classified_as_quota_or_auth():
    assert LLMProvider._classify_error(XFYUN_APP_NO_AUTH) == FailoverReason.QUOTA


def test_humanized_xfyun_500_keeps_machine_readable_marker():
    message = _humanize_upstream_error(500, XFYUN_APP_NO_AUTH)

    assert "讯飞模型授权或额度异常" in message
    assert "xfyun_auth_or_quota" in message
    assert LLMProvider._classify_error(message) == FailoverReason.QUOTA


def test_llm_error_preserves_raw_body_for_internal_classification():
    exc = LLMError("云端服务暂时不可用 (HTTP 500)", status_code=500, raw_body=XFYUN_APP_NO_AUTH)

    assert exc.status_code == 500
    assert exc.raw_body == XFYUN_APP_NO_AUTH
    assert LLMProvider._classify_error(f"{exc}\n{exc.raw_body}") == FailoverReason.QUOTA


def test_openai_provider_avoids_zstd_accept_encoding():
    provider = OpenAIProvider(
        EndpointConfig(
            name="xfyun-test",
            provider="xfyun",
            api_type="openai",
            base_url="https://maas-coding-api.cn-huabei-1.xf-yun.com/v2",
            api_key="sk-test",
            model="astron-code-latest",
        )
    )

    headers = provider._build_headers()

    assert headers["Accept-Encoding"] == "gzip, deflate"


def test_setup_center_model_list_avoids_zstd_accept_encoding():
    headers = _model_list_headers({"Authorization": "Bearer sk-test"})

    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Accept-Encoding"] == "gzip, deflate"


def test_same_upstream_identity_shares_rpm_limiter():
    LLMProvider._shared_rate_limiters.clear()
    cfg = EndpointConfig(
        name="nvidia-a",
        provider="nvidia_nim",
        api_type="openai",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="sk-same",
        model="minimaxai/minimax-m2.7",
        rpm_limit=40,
    )

    provider_a = OpenAIProvider(cfg)
    provider_b = OpenAIProvider(
        EndpointConfig(
            name="nvidia-b",
            provider="nvidia_nim",
            api_type="openai",
            base_url="https://integrate.api.nvidia.com/v1/",
            api_key="sk-same",
            model="minimaxai/minimax-m2.7",
            rpm_limit=40,
        )
    )

    assert provider_a._rate_limiter is provider_b._rate_limiter


def test_upstream_429_sets_shared_backoff_without_second_config_path():
    LLMProvider._shared_rate_limiters.clear()
    provider = OpenAIProvider(
        EndpointConfig(
            name="nvidia",
            provider="nvidia_nim",
            api_type="openai",
            base_url="https://integrate.api.nvidia.com/v1",
            api_key="sk-test",
            model="minimaxai/minimax-m2.7",
            rpm_limit=0,
        )
    )

    provider.report_upstream_rate_limit('{"status":429,"retry_after":12}')

    assert isinstance(provider._rate_limiter, RPMRateLimiter)
    assert provider._rate_limiter._blocked_until > 0
