"""LLM-side streaming helper extracted from ``core.brain`` (P-RC-4).

Note: the continuation plan literal calls for ``runtime/stream/llm.py``,
but ``runtime/stream.py`` already exists as the StreamBus module
(ADR-0006). Adding a ``runtime/stream/`` package next to it would
collide with that import path; we place this submodule inside the
``runtime/llm/`` package instead -- the deviation is captured in the
G-RC-4 gate review.

The legacy ``Brain`` streaming entry point wrapped
``LLMClient.chat_stream`` with a
``TokenTrackingContext`` set/reset, a debug-dump call, and the v1
multimodal conversion. The conversion is the heavy part; this helper
exposes the lightweight streaming primitive itself so the v2 agent
rewrite can compose its own dump / tracking concerns instead of
inheriting them from the giant.
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
    """Token-tracking context manager mirroring the legacy Brain wrapping.

    The legacy ``Brain`` always called ``set_tracking_context(...)``
    before the streaming generator and ``reset_tracking_context(...)``
    in the ``finally`` block. We accept the two functions as injection
    points so this helper depends only on the public token-tracking
    surface, not on the legacy module path.
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
    concerns layer them around this primitive (the v2 agent rewrite
    will compose :func:`llm_stream_tracking` and a debug-dump callable;
    the legacy Brain keeps its inline composition until the shim swap).

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
