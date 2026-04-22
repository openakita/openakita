from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from openakita.llm.client import LLMClient
from openakita.llm.providers.base import LLMProvider
from openakita.llm.types import EndpointConfig, LLMRequest, LLMResponse, Message


class FakeStreamProvider(LLMProvider):
    def __init__(self, config: EndpointConfig, events: list[dict]) -> None:
        super().__init__(config)
        self.events = events
        self.calls = 0
        self.enable_thinking_seen: list[bool] = []

    async def chat(self, request: LLMRequest) -> LLMResponse:
        raise AssertionError("chat() should not be called by streaming tests")

    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        self.calls += 1
        self.enable_thinking_seen.append(request.enable_thinking)
        for event in self.events:
            yield event


def _provider(
    name: str,
    *,
    priority: int,
    capabilities: list[str],
    events: list[dict] | None = None,
) -> FakeStreamProvider:
    return FakeStreamProvider(
        EndpointConfig(
            name=name,
            provider="test",
            api_type="openai",
            base_url="https://example.test/v1",
            api_key="sk-test",
            model=f"{name}-model",
            priority=priority,
            capabilities=capabilities,
        ),
        events or [],
    )


def _client_with(*providers: FakeStreamProvider) -> LLMClient:
    LLMClient._auth_failed_endpoints.clear()
    client = LLMClient(endpoints=[])
    client._providers = {provider.name: provider for provider in providers}
    client._endpoints = [provider.config for provider in providers]
    client._settings = {}
    return client


def test_selected_non_thinking_endpoint_stays_first_when_thinking_requested():
    primary = _provider(
        "primary",
        priority=1,
        capabilities=["text", "tools"],
    )
    thinking = _provider(
        "thinking",
        priority=2,
        capabilities=["text", "tools", "thinking"],
    )
    client = _client_with(primary, thinking)

    ok, message = client.switch_model("primary", reason="user selected")
    assert ok, message

    eligible = client._filter_eligible_endpoints(require_tools=True, require_thinking=True)

    assert [provider.name for provider in eligible] == ["primary", "thinking"]


async def test_empty_semantic_stream_fails_over_to_next_endpoint():
    empty = _provider(
        "empty",
        priority=1,
        capabilities=["text", "tools"],
        events=[
            {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
            {"type": "message_stop", "stop_reason": "end_turn"},
        ],
    )
    good = _provider(
        "good",
        priority=2,
        capabilities=["text", "tools"],
        events=[
            {"type": "content_block_delta", "delta": {"type": "text", "text": "ok"}},
            {"type": "message_stop", "stop_reason": "end_turn"},
        ],
    )
    client = _client_with(empty, good)

    events = [
        event
        async for event in client.chat_stream(messages=[Message(role="user", content="hi")])
    ]

    assert empty.calls == 1
    assert good.calls == 1
    assert not empty.is_healthy
    assert events == good.events


@pytest.mark.parametrize(
    "semantic_event",
    [
        {"type": "content_block_delta", "delta": {"type": "text", "text": "visible"}},
        {"type": "content_block_delta", "delta": {"type": "thinking", "text": "reasoning"}},
        {
            "type": "content_block_delta",
            "delta": {"type": "tool_use", "id": "call_1", "name": "search"},
        },
    ],
)
async def test_semantic_stream_output_is_accepted_without_failover(semantic_event: dict):
    first = _provider(
        "first",
        priority=1,
        capabilities=["text", "tools"],
        events=[semantic_event, {"type": "message_stop", "stop_reason": "end_turn"}],
    )
    second = _provider(
        "second",
        priority=2,
        capabilities=["text", "tools"],
        events=[
            {
                "type": "content_block_delta",
                "delta": {"type": "text", "text": "should not run"},
            }
        ],
    )
    client = _client_with(first, second)

    events = [
        event
        async for event in client.chat_stream(messages=[Message(role="user", content="hi")])
    ]

    assert first.calls == 1
    assert second.calls == 0
    assert events == first.events
