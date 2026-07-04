"""EX-P2-8 — retry/backoff classification + loop behaviour.

Three scenarios:

1. ``is_retryable_llm_error`` classifies 5xx / timeout / rate-limit
   as retryable and 4xx as permanent.
2. The router retries a transient ``503`` failure three times and
   then succeeds.
3. The router gives up immediately on a ``400`` (permanent) error.
"""

from __future__ import annotations

import asyncio

import pytest

from finance_auto_backend.ai.router import (
    DEFAULT_LLM_RETRIES,
    FinanceAIRouter,
    LLM_BACKOFF_BASE_ENV,
    LLM_BACKOFF_JITTER_ENV,
    LLM_RETRIES_ENV,
    LLMResponse,
    is_retryable_llm_error,
)


def test_classifier_5xx_retryable() -> None:
    assert is_retryable_llm_error(RuntimeError("HTTP 503 Service Unavailable"))
    assert is_retryable_llm_error(RuntimeError("upstream timeout"))
    assert is_retryable_llm_error(RuntimeError("rate limit exceeded"))
    assert is_retryable_llm_error(asyncio.TimeoutError())
    assert is_retryable_llm_error(RuntimeError("HTTP 429 too many requests"))


def test_classifier_4xx_permanent() -> None:
    assert not is_retryable_llm_error(RuntimeError("HTTP 400 invalid request"))
    assert not is_retryable_llm_error(RuntimeError("HTTP 401 unauthorized"))
    assert not is_retryable_llm_error(RuntimeError("HTTP 422 unprocessable"))


class _FlakyResponder:
    def __init__(self, *, fail_n: int, exc_msg: str) -> None:
        self.fail_n = fail_n
        self.exc_msg = exc_msg
        self.calls = 0

    async def complete(self, *, prompt, endpoint_name, sensitivity_level, scenario_id=""):  # noqa: ANN001
        self.calls += 1
        if self.calls <= self.fail_n:
            raise RuntimeError(self.exc_msg)
        return LLMResponse(
            text="ok",
            model_id="x",
            provider="mock",
            is_local=True,
        )


def test_router_retries_transient_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LLM_BACKOFF_BASE_ENV, "0.01")
    monkeypatch.setenv(LLM_BACKOFF_JITTER_ENV, "0")
    monkeypatch.setenv(LLM_RETRIES_ENV, str(DEFAULT_LLM_RETRIES))

    responder = _FlakyResponder(fail_n=3, exc_msg="HTTP 503 Service Unavailable")
    router = FinanceAIRouter(responder=responder)

    async def _go() -> LLMResponse:
        return await router.complete(
            scenario_id="t", level="aggregated", prompt="hi",
        )

    resp = asyncio.run(_go())
    assert resp.text == "ok"
    assert responder.calls == 4  # 1 initial + 3 retries


def test_router_gives_up_on_permanent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LLM_BACKOFF_BASE_ENV, "0.01")
    monkeypatch.setenv(LLM_BACKOFF_JITTER_ENV, "0")

    responder = _FlakyResponder(fail_n=10, exc_msg="HTTP 400 invalid prompt")
    router = FinanceAIRouter(responder=responder)

    async def _go() -> LLMResponse:
        return await router.complete(
            scenario_id="t", level="aggregated", prompt="hi",
        )

    with pytest.raises(RuntimeError, match="HTTP 400"):
        asyncio.run(_go())
    assert responder.calls == 1, "permanent error must not be retried"


def test_router_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LLM_BACKOFF_BASE_ENV, "0.01")
    monkeypatch.setenv(LLM_BACKOFF_JITTER_ENV, "0")
    monkeypatch.setenv(LLM_RETRIES_ENV, "2")

    responder = _FlakyResponder(fail_n=10, exc_msg="HTTP 503 Service Unavailable")
    router = FinanceAIRouter(responder=responder)

    async def _go() -> LLMResponse:
        return await router.complete(
            scenario_id="t", level="aggregated", prompt="hi",
        )

    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(_go())
    assert responder.calls == 3, "should have tried 1 + 2 retries = 3"
