import pytest

from openakita.core._reasoning_runtime import ReasoningEngine
from openakita.llm.providers.openai import (
    REMOTE_STREAM_READ_TIMEOUT_CAP_SECONDS,
    OpenAIProvider,
)
from openakita.llm.providers.openai_responses import OpenAIResponsesProvider
from openakita.llm.types import EndpointConfig, LLMError


def _provider(
    *,
    base_url: str = "https://api.example.com/v1",
    timeout: int = 180,
) -> OpenAIProvider:
    return OpenAIProvider(
        EndpointConfig(
            name="test",
            provider="custom",
            api_type="openai",
            base_url=base_url,
            api_key="sk-test",
            model="test-model",
            timeout=timeout,
        )
    )


def test_remote_stream_timeout_is_capped_for_large_context() -> None:
    provider = _provider()
    body = {"messages": [{"content": "x" * 120_000}], "tools": []}

    request_timeout = provider._estimate_request_timeout(body)
    stream_timeout = provider._estimate_stream_timeout(body)

    assert request_timeout is not None
    assert request_timeout.read > REMOTE_STREAM_READ_TIMEOUT_CAP_SECONDS
    assert stream_timeout is not None
    assert stream_timeout.read == REMOTE_STREAM_READ_TIMEOUT_CAP_SECONDS


def test_remote_stream_timeout_preserves_shorter_user_setting() -> None:
    provider = _provider(timeout=30)

    stream_timeout = provider._estimate_stream_timeout({"messages": [], "tools": []})

    assert stream_timeout is not None
    assert stream_timeout.read == 30


def test_local_stream_timeout_is_not_capped() -> None:
    provider = _provider(base_url="http://127.0.0.1:11434/v1")
    body = {"messages": [{"content": "x" * 120_000}], "tools": []}

    stream_timeout = provider._estimate_stream_timeout(body)

    assert stream_timeout is not None
    assert stream_timeout.read > REMOTE_STREAM_READ_TIMEOUT_CAP_SECONDS


def test_responses_stream_timeout_uses_same_remote_cap() -> None:
    provider = OpenAIResponsesProvider(
        EndpointConfig(
            name="responses-test",
            provider="openai",
            api_type="openai_responses",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="test-model",
            timeout=180,
        )
    )
    body = {"input": [{"content": "x" * 180_000}], "tools": []}

    stream_timeout = provider._estimate_stream_timeout(body)

    assert stream_timeout is not None
    assert stream_timeout.read == REMOTE_STREAM_READ_TIMEOUT_CAP_SECONDS


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            LLMError("Stream first-byte timeout: ReadTimeout"),
            "AI 服务响应较慢，正在尝试恢复连接（2/3）...",
        ),
        (LLMError("HTTP 500"), "AI 服务暂时不可用，正在重试（2/3）..."),
    ],
)
def test_retry_progress_describes_action_and_attempt_count(
    error: Exception,
    expected: str,
) -> None:
    message = ReasoningEngine._retry_progress_message(error, 2, 3)

    assert message == expected
    assert "2/3" in message
