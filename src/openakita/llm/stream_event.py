"""
统一流式事件协议

解决 Anthropic / OpenAI provider 输出格式不一致的根本问题。
所有 provider 的 chat_stream 都应该 yield StreamEvent 而非原始 dict。

参考 Claude Code 的 BetaRawMessageStreamEvent 设计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

StreamEventType = Literal[
    "message_start",
    "content_start",
    "content_delta",
    "content_stop",
    "message_delta",
    "message_stop",
    "ping",
    "error",
]


@dataclass
class StreamEvent:
    """统一的流式事件"""

    type: StreamEventType
    data: dict = field(default_factory=dict)
    index: int | None = None
    content_type: str | None = None  # 'text' / 'tool_use' / 'thinking'

    # ── Factory methods for Anthropic raw events ──

    @classmethod
    def from_anthropic_raw(cls, raw: dict) -> StreamEvent | None:
        """从 Anthropic API 原始 SSE 事件转换。"""
        event_type = raw.get("type", "")

        if event_type == "message_start":
            msg = raw.get("message", {})
            return cls(
                type="message_start",
                data={
                    "id": msg.get("id", ""),
                    "model": msg.get("model", ""),
                    "usage": msg.get("usage", {}),
                    "stop_reason": msg.get("stop_reason"),
                },
            )

        if event_type == "content_block_start":
            idx = raw.get("index", 0)
            block = raw.get("content_block", {})
            block_type = block.get("type", "text")
            data: dict[str, Any] = {"block": block}
            return cls(
                type="content_start",
                data=data,
                index=idx,
                content_type=block_type,
            )

        if event_type == "content_block_delta":
            idx = raw.get("index", 0)
            delta = raw.get("delta", {})
            delta_type = delta.get("type", "")

            content_type = "text"
            if "tool" in delta_type:
                content_type = "tool_use"
            elif "thinking" in delta_type or "signature" in delta_type:
                content_type = "thinking"

            return cls(
                type="content_delta",
                data={"delta": delta},
                index=idx,
                content_type=content_type,
            )

        if event_type == "content_block_stop":
            idx = raw.get("index", 0)
            return cls(type="content_stop", index=idx)

        if event_type == "message_delta":
            delta = raw.get("delta", {})
            usage = raw.get("usage", {})
            return cls(
                type="message_delta",
                data={
                    "stop_reason": delta.get("stop_reason"),
                    "usage": usage,
                },
            )

        if event_type == "message_stop":
            return cls(type="message_stop")

        if event_type == "ping":
            return cls(type="ping")

        if event_type == "error":
            return cls(type="error", data={"error": raw.get("error", {})})

        return cls(type="ping", data={"_raw_type": event_type})

    # ── Factory methods for OpenAI chat completion chunks ──

    @classmethod
    def from_openai_chunk(cls, chunk: dict) -> StreamEvent | None:
        """从 OpenAI Chat Completions streaming chunk 转换。"""
        choices = chunk.get("choices", [])
        if not choices:
            usage = chunk.get("usage")
            if usage:
                return cls(
                    type="message_delta",
                    data={
                        "usage": {
                            "input_tokens": usage.get("prompt_tokens", 0),
                            "output_tokens": usage.get("completion_tokens", 0),
                        },
                    },
                )
            return cls(type="ping")

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        if finish_reason:
            stop_reason_map = {
                "stop": "end_turn",
                "length": "max_tokens",
                "tool_calls": "tool_use",
                "function_call": "tool_use",
                "content_filter": "end_turn",
            }
            return cls(
                type="message_delta",
                data={
                    "stop_reason": stop_reason_map.get(finish_reason, "end_turn"),
                    "usage": chunk.get("usage", {}),
                },
            )

        # Tool call delta
        tool_calls = delta.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                idx = tc.get("index", 0)
                func = tc.get("function", {})
                if tc.get("id"):
                    return cls(
                        type="content_start",
                        data={
                            "block": {
                                "type": "tool_use",
                                "id": tc["id"],
                                "name": func.get("name", ""),
                            }
                        },
                        index=idx,
                        content_type="tool_use",
                    )
                args = func.get("arguments", "")
                if args:
                    return cls(
                        type="content_delta",
                        data={"delta": {"type": "input_json_delta", "partial_json": args}},
                        index=idx,
                        content_type="tool_use",
                    )

        # Text or reasoning delta
        content = delta.get("content")
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if reasoning:
            return cls(
                type="content_delta",
                data={"delta": {"type": "thinking_delta", "thinking": reasoning}},
                index=0,
                content_type="thinking",
            )
        if content:
            return cls(
                type="content_delta",
                data={"delta": {"type": "text_delta", "text": content}},
                index=0,
                content_type="text",
            )

        return cls(type="ping")
