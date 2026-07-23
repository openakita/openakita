"""Multimodal block conversion between LLM-client types and Anthropic types.

This module bridges the unified ``openakita.llm.types`` block representation
with the Anthropic API shape. Three goals:

* keep the conversion *pure* -- no Brain dependencies, no I/O;
* be the single source of truth for the
  ``StopReason -> Anthropic stop_reason string`` map and the
  ``ThinkingBlock + reasoning_content -> <thinking> tag``
  serialisation rule (interleaved-thinking parity across providers
  that already plagued duplicate-storyboard bugs in v1);
* be unit-testable against synthetic ``LLMResponse`` fixtures.

``openakita.agent.brain`` calls this module directly.
"""

from __future__ import annotations

from typing import Any

from anthropic.types import Message as AnthropicMessage
from anthropic.types import TextBlock as AnthropicTextBlock
from anthropic.types import ToolUseBlock as AnthropicToolUseBlock
from anthropic.types import Usage as AnthropicUsage

from openakita.llm.types import (
    LLMResponse,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

# Canonical map. A new ``StopReason``
# (added e.g. for a new provider) needs one edit in one place.
_STOP_REASON_MAP: dict[StopReason, str] = {
    StopReason.END_TURN: "end_turn",
    StopReason.MAX_TOKENS: "max_tokens",
    StopReason.TOOL_USE: "tool_use",
    StopReason.STOP_SEQUENCE: "stop_sequence",
}


def map_stop_reason(stop_reason: StopReason) -> str:
    """Project an :class:`openakita.llm.types.StopReason` to the Anthropic string.

    Unknown reasons fall back to ``"end_turn"`` so API consumers do not see
    unexpected enum values.
    """
    return _STOP_REASON_MAP.get(stop_reason, "end_turn")


def _wrap_thinking_text(text: str) -> str:
    """Wrap raw thinking text in the cross-provider ``<thinking>`` tag."""
    return f"<thinking>{text}</thinking>"


def collect_thinking_texts(response: LLMResponse) -> list[str]:
    """Return every ``<thinking>``-wrapped thought present in ``response``.

    Two sources are merged into one list, in priority order:

    1. ``ThinkingBlock`` entries in ``response.content`` (Anthropic /
       MiniMax M2.1 native style);
    2. the OpenAI-compatible top-level ``reasoning_content`` field
       (DeepSeek / Kimi / Zhipu / ...); used only when no native
       ``ThinkingBlock`` was emitted so we do not double-up.

    The returned list is ordered for direct ``"\n".join`` use by the
    caller.
    """
    out: list[str] = []
    for block in response.content:
        if isinstance(block, ThinkingBlock):
            out.append(_wrap_thinking_text(block.thinking))
    if response.reasoning_content and not out:
        out.append(_wrap_thinking_text(response.reasoning_content))
    return out


def _anthropic_blocks_from_response(response: LLMResponse) -> list[Any]:
    """Project ``LLMResponse.content`` into Anthropic block instances.

    Skips ``ThinkingBlock`` -- those are handled separately by
    :func:`collect_thinking_texts` so callers can splice the
    ``<thinking>`` prefix into the first text block.
    """
    blocks: list[Any] = []
    for block in response.content:
        if isinstance(block, TextBlock):
            blocks.append(AnthropicTextBlock(type="text", text=block.text))
        elif isinstance(block, ToolUseBlock):
            blocks.append(
                AnthropicToolUseBlock(
                    type="tool_use",
                    id=block.id,
                    name=block.name,
                    input=block.input,
                )
            )
    return blocks


def response_to_anthropic_message(response: LLMResponse) -> AnthropicMessage:
    """Project an :class:`LLMResponse` to an :class:`AnthropicMessage`.

    Mirrors ``Brain._convert_response_to_anthropic`` exactly. The
    thinking text (native or ``reasoning_content``) is prepended to
    the first text block; a synthetic empty text block is inserted
    when the response had no other content. Three optional
    metadata attributes (``endpoint_name``, ``_failover_from``,
    ``_thinking_fallback``) are passed through when present so the
    reasoning engine's failover surface keeps working.
    """
    content_blocks = _anthropic_blocks_from_response(response)
    thinking_texts = collect_thinking_texts(response)

    if thinking_texts:
        joined = "\n".join(thinking_texts)
        if content_blocks and hasattr(content_blocks[0], "text"):
            content_blocks[0] = AnthropicTextBlock(
                type="text", text=joined + "\n" + content_blocks[0].text
            )
        else:
            content_blocks.insert(0, AnthropicTextBlock(type="text", text=joined))

    if not content_blocks:
        content_blocks.append(AnthropicTextBlock(type="text", text=""))

    msg = AnthropicMessage(
        id=response.id,
        type="message",
        role="assistant",
        content=content_blocks,
        model=response.model,
        stop_reason=map_stop_reason(response.stop_reason),
        stop_sequence=None,
        usage=AnthropicUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        ),
    )
    # Three optional metadata fields piggy-backed on LLMResponse; the
    # reasoning engine reads them off the AnthropicMessage to render
    # failover badges in the timeline.
    for attr in ("endpoint_name", "_failover_from", "_thinking_fallback"):
        if hasattr(response, attr):
            setattr(msg, attr, getattr(response, attr))
    return msg


__all__ = [
    "collect_thinking_texts",
    "map_stop_reason",
    "response_to_anthropic_message",
]
