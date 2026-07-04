"""Tests for :mod:`openakita.runtime.llm.stream`.

Hand-rolled fake LLMClient yields 3 synthetic events; we drive
:func:`stream_llm_events` against it and assert event-order parity,
empty-stream tolerance, and that :func:`llm_stream_tracking` calls
``set_context`` / ``reset_context`` symmetrically (even when the
streamed body raises).
"""

from __future__ import annotations

import pytest

from openakita.runtime.llm import llm_stream_tracking, stream_llm_events


class _FakeStreamClient:
    """Fake ``LLMClient`` that yields a configured event list."""

    def __init__(self, events, *, raise_after=None):
        self._events = list(events)
        self._raise_after = raise_after
        self.last_kwargs: dict | None = None

    async def chat_stream(self, **kwargs):
        self.last_kwargs = kwargs
        for i, event in enumerate(self._events):
            if self._raise_after is not None and i >= self._raise_after:
                raise RuntimeError("simulated provider failure")
            yield event


@pytest.mark.asyncio
async def test_stream_llm_events_yields_in_order_and_forwards_kwargs() -> None:
    client = _FakeStreamClient([
        {"type": "message_start", "id": "m-1"},
        {"type": "content_block_delta", "delta": {"text": "hello"}},
        {"type": "message_stop"},
    ])
    out = []
    async for event in stream_llm_events(
        client,
        messages=[{"role": "user", "content": "hi"}],
        system="be helpful",
        tools=None,
        max_tokens=128,
        conversation_id="conv-1",
    ):
        out.append(event)
    assert [e["type"] for e in out] == ["message_start", "content_block_delta", "message_stop"]
    assert client.last_kwargs == {
        "messages": [{"role": "user", "content": "hi"}],
        "system": "be helpful",
        "tools": None,
        "max_tokens": 128,
        "enable_thinking": None,
        "thinking_depth": None,
        "conversation_id": "conv-1",
        "extra_params": None,
    }


@pytest.mark.asyncio
async def test_stream_llm_events_empty_stream_yields_nothing() -> None:
    client = _FakeStreamClient([])
    out = []
    async for event in stream_llm_events(client, messages=[]):
        out.append(event)
    assert out == []


@pytest.mark.asyncio
async def test_stream_llm_events_propagates_provider_failure() -> None:
    client = _FakeStreamClient(
        [{"type": "message_start"}, {"type": "content_block_delta"}],
        raise_after=1,
    )
    out = []
    with pytest.raises(RuntimeError, match="simulated provider failure"):
        async for event in stream_llm_events(client, messages=[]):
            out.append(event)
    # The first event still made it through before the failure.
    assert out == [{"type": "message_start"}]


@pytest.mark.asyncio
async def test_llm_stream_tracking_sets_and_resets_symmetrically_on_success() -> None:
    set_calls: list = []
    reset_calls: list = []

    def _set(ctx):
        set_calls.append(ctx)
        return "TOKEN-1"

    def _reset(tok):
        reset_calls.append(tok)

    async with llm_stream_tracking(
        set_context=_set,
        reset_context=_reset,
        conversation_id="conv-9",
        iteration=3,
        agent_profile_id="agent-A",
    ):
        pass

    assert len(set_calls) == 1
    ctx = set_calls[0]
    assert ctx.session_id == "conv-9"
    assert ctx.iteration == 3
    assert ctx.agent_profile_id == "agent-A"
    assert reset_calls == ["TOKEN-1"]


@pytest.mark.asyncio
async def test_llm_stream_tracking_resets_even_on_exception() -> None:
    set_calls = []
    reset_calls = []

    def _set(ctx):
        set_calls.append(ctx)
        return "TOKEN-X"

    def _reset(tok):
        reset_calls.append(tok)

    with pytest.raises(ValueError, match="boom"):
        async with llm_stream_tracking(set_context=_set, reset_context=_reset):
            raise ValueError("boom")

    assert len(set_calls) == 1
    assert reset_calls == ["TOKEN-X"]
