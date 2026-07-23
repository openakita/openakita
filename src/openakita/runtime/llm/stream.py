"""LLM streaming and token-tracking helpers.

This module exposes a lightweight ``LLMClient.chat_stream`` primitive and a
separate token-tracking context manager so callers can compose only the
concerns they need.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol


class _LLMClientLike(Protocol):
    """Structural subset of :class:`openakita.llm.client.LLMClient` we need."""

    async def chat_stream(self, **kwargs: Any) -> AsyncIterator[Any]: ...


@asynccontextmanager
async def llm_stream_tracking(
    *,
    set_context,
    reset_context,
    conversation_id: str = "",
    operation_type: str = "chat_react_iteration_stream",
    channel: str = "api",
    iteration: int = 0,
    agent_profile_id: str = "default",
):
    """Apply and reliably reset token tracking around a streaming call.

    The setter and resetter are injection points so this helper depends only
    on the public token-tracking surface.
    """
    from openakita.core.token_tracking import TokenTrackingContext

    token = set_context(
        TokenTrackingContext(
            session_id=conversation_id,
            operation_type=operation_type,
            channel=channel,
            iteration=iteration,
            agent_profile_id=agent_profile_id,
        )
    )
    try:
        yield
    finally:
        reset_context(token)


async def stream_llm_events(
    client: _LLMClientLike,
    *,
    messages: list[Any],
    system: str = "",
    tools: list[Any] | None = None,
    max_tokens: int = 0,
    enable_thinking: bool | None = None,
    thinking_depth: str | None = None,
    conversation_id: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> AsyncIterator[Any]:
    """Async-iterate raw provider events from ``client.chat_stream``.

    Pure wrapper around ``LLMClient.chat_stream`` -- no token tracking,
    no debug dump, no multimodal conversion. Callers that need those
    concerns layer them around this primitive.

    Args:
        client: any object that implements ``async def chat_stream(...)``.
        messages: already-converted ``openakita.llm.types.Message`` list.
        system, tools, max_tokens, enable_thinking, thinking_depth,
        conversation_id, extra_params: forwarded verbatim.

    Yields:
        Provider-native event dicts; callers feed them to a
        ``StreamAccumulator`` (or the equivalent v2 helper) to assemble
        a final :class:`LLMResponse`.
    """
    async for event in client.chat_stream(
        messages=messages,
        system=system,
        tools=tools,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
        thinking_depth=thinking_depth,
        conversation_id=conversation_id,
        extra_params=extra_params,
    ):
        yield event


__all__ = ["llm_stream_tracking", "stream_llm_events"]
