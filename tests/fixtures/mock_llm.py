"""
Mock LLM infrastructure for deterministic testing.

Provides MockLLMClient (programmable responses), LLMRecorder (capture real interactions),
and ReplayLLMClient (replay recorded interactions).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openakita.llm.types import (
    ContentBlockType,
    LLMResponse,
    StopReason,
    TextBlock,
    ToolUseBlock,
    Usage,
)


@dataclass
class MockResponse:
    """A pre-programmed LLM response."""

    content: str = ""
    tool_calls: list[dict] | None = None
    stop_reason: StopReason = StopReason.END_TURN
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=100, output_tokens=50))
    model: str = "mock-model"
    reasoning_content: str | None = None

    def to_llm_response(self) -> LLMResponse:
        blocks: list[ContentBlockType] = []
        if self.content:
            blocks.append(TextBlock(text=self.content))
        if self.tool_calls:
            for tc in self.tool_calls:
                blocks.append(
                    ToolUseBlock(
                        id=tc.get("id", f"toolu_{uuid.uuid4().hex[:12]}"),
                        name=tc["name"],
                        input=tc.get("input", tc.get("arguments", {})),
                    )
                )
        stop = StopReason.TOOL_USE if self.tool_calls else self.stop_reason
        return LLMResponse(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            content=blocks,
            stop_reason=stop,
            usage=self.usage,
            model=self.model,
            reasoning_content=self.reasoning_content,
        )


class MockLLMClient:
    """
    Programmable LLM client for deterministic testing.

    Usage:
        mock = MockLLMClient()
        mock.preset_response("Hello!")
        response = await mock.chat(messages)
        assert response.content[0].text == "Hello!"
    """

    def __init__(self) -> None:
        self._responses: list[MockResponse] = []
        self._default_response: MockResponse | None = None
        self.call_log: list[dict] = []
        self._stream_responses: list[MockResponse] = []

    def preset_response(
        self,
        content: str = "",
        tool_calls: list[dict] | None = None,
        stop_reason: StopReason = StopReason.END_TURN,
        reasoning_content: str | None = None,
    ) -> None:
        """Queue a single response for the next chat() call."""
        self._responses.append(
            MockResponse(
                content=content,
                tool_calls=tool_calls,
                stop_reason=stop_reason,
                reasoning_content=reasoning_content,
            )
        )

    def preset_sequence(self, responses: list[MockResponse]) -> None:
        """Queue multiple responses for consecutive chat() calls (e.g., multi-step ReAct)."""
        self._responses.extend(responses)

    def set_default_response(self, content: str = "Mock default response") -> None:
        """Set a fallback response when the queue is empty."""
        self._default_response = MockResponse(content=content)

    def reset_all_cooldowns(self, **kwargs: Any) -> None:
        """No-op: mock has no cooldown state."""
        pass

    async def chat(
        self,
        messages: list[Any],
        system: str = "",
        tools: list[Any] | None = None,
        max_tokens: int = 0,
        temperature: float = 1.0,
        enable_thinking: bool = False,
        thinking_depth: str | None = None,
        conversation_id: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self.call_log.append(
            {
                "messages": messages,
                "system": system,
                "tools": tools,
                "max_tokens": max_tokens,
                "enable_thinking": enable_thinking,
                "conversation_id": conversation_id,
                "kwargs": kwargs,
            }
        )
        if self._responses:
            return self._responses.pop(0).to_llm_response()
        if self._default_response:
            return self._default_response.to_llm_response()
        return MockResponse(content="No mock response configured").to_llm_response()

    def chat_sync(self, messages: list[Any], **kwargs: Any) -> "LLMResponse":
        """Synchronous version of chat() for non-async tests."""
        self.call_log.append(
            {
                "messages": messages,
                "system": "",
                "tools": None,
                "max_tokens": 0,
                "enable_thinking": False,
                "conversation_id": None,
                "kwargs": kwargs,
            }
        )
        if self._responses:
            return self._responses.pop(0).to_llm_response()
        if self._default_response:
            return self._default_response.to_llm_response()
        from openakita.llm.types import LLMResponse

        return MockResponse(content="No mock response configured").to_llm_response()

    async def chat_stream(
        self,
        messages: list[Any],
        system: str = "",
        tools: list[Any] | None = None,
        max_tokens: int = 0,
        temperature: float = 1.0,
        enable_thinking: bool = False,
        thinking_depth: str | None = None,
        conversation_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        resp = await self.chat(
            messages,
            system,
            tools,
            max_tokens,
            temperature,
            enable_thinking,
            thinking_depth,
            conversation_id,
            **kwargs,
        )
        for block in resp.content:
            if hasattr(block, "text"):
                yield {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": block.text},
                }
            elif hasattr(block, "name"):
                yield {
                    "type": "content_block_start",
                    "content_block": {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    },
                }
        yield {
            "type": "message_stop",
            "message": {
                "stop_reason": resp.stop_reason.value,
                "usage": {
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens,
                },
            },
        }

    @property
    def total_calls(self) -> int:
        return len(self.call_log)

    @property
    def last_call(self) -> dict | None:
        return self.call_log[-1] if self.call_log else None

    def reset(self) -> None:
        self._responses.clear()
        self.call_log.clear()


class MockBrain:
    """
    Mock Brain that wraps MockLLMClient, matching the Brain interface.
    Used where Agent code calls brain.messages_create_async() or brain.think().
    """

    def __init__(self, mock_client: MockLLMClient | None = None) -> None:
        self.llm_client = mock_client or MockLLMClient()
        self.compiler_client = mock_client or MockLLMClient()
        self.model = "mock-model"

    async def messages_create_async(
        self,
        messages: list[dict] | None = None,
        system: str | list | None = None,
        tools: list | None = None,
        max_tokens: int = 4096,
        use_thinking: bool | None = None,
        thinking_depth: str | None = None,
        conversation_id: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        sys_str = ""
        if isinstance(system, str):
            sys_str = system
        elif isinstance(system, list):
            sys_str = " ".join(s.get("text", "") if isinstance(s, dict) else str(s) for s in system)
        return await self.llm_client.chat(
            messages=messages or [],
            system=sys_str,
            tools=tools,
            max_tokens=max_tokens,
            enable_thinking=bool(use_thinking),
            thinking_depth=thinking_depth,
            conversation_id=conversation_id,
        )

    async def think(self, prompt: str, **kwargs: Any) -> LLMResponse:
        from openakita.llm.types import Message

        return await self.llm_client.chat(
            messages=[Message(role="user", content=prompt)],
        )


def _hash_messages(messages: list) -> str:
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class LLMRecorder:
    """
    Wraps a real LLMClient, records interactions for later replay.
    Usage:
        recorder = LLMRecorder(real_client, Path("tests/fixtures/recordings"))
        response = await recorder.chat(messages)  # records to disk
    """

    def __init__(self, real_client: Any, recording_dir: Path) -> None:
        self.real_client = real_client
        self.recording_dir = recording_dir
        self.recording_dir.mkdir(parents=True, exist_ok=True)

    async def chat(self, messages: list, **kwargs: Any) -> LLMResponse:
        response = await self.real_client.chat(messages, **kwargs)
        self._save(messages, kwargs, response)
        return response

    def _save(self, messages: list, kwargs: dict, response: LLMResponse) -> None:
        msg_hash = _hash_messages([self._serialize_msg(m) for m in messages])
        recording = {
            "messages_hash": msg_hash,
            "messages": [self._serialize_msg(m) for m in messages],
            "kwargs": {k: str(v) for k, v in kwargs.items()},
            "response": {
                "id": response.id,
                "content": [self._serialize_block(b) for b in response.content],
                "stop_reason": response.stop_reason.value,
                "model": response.model,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            },
        }
        path = self.recording_dir / f"{msg_hash}.json"
        path.write_text(json.dumps(recording, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _serialize_msg(msg: Any) -> dict:
        if isinstance(msg, dict):
            return msg
        return {"role": getattr(msg, "role", "user"), "content": getattr(msg, "content", str(msg))}

    @staticmethod
    def _serialize_block(block: Any) -> dict:
        if hasattr(block, "text"):
            return {"type": "text", "text": block.text}
        if hasattr(block, "name"):
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        return {"type": "unknown"}


class ReplayLLMClient:
    """
    Replays recorded LLM responses from disk.
    Falls back to a default response if no recording matches.
    """

    def __init__(self, recording_dir: Path) -> None:
        self.recording_dir = recording_dir
        self._cache: dict[str, dict] = {}
        self._load_recordings()

    def _load_recordings(self) -> None:
        if not self.recording_dir.exists():
            return
        for path in self.recording_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._cache[data["messages_hash"]] = data["response"]
            except (json.JSONDecodeError, KeyError):
                continue

    async def chat(self, messages: list, **kwargs: Any) -> LLMResponse:
        msg_hash = _hash_messages([self._normalize_msg(m) for m in messages])
        if msg_hash in self._cache:
            return self._deserialize_response(self._cache[msg_hash])
        return MockResponse(content="[replay] No recording found for this input").to_llm_response()

    @staticmethod
    def _normalize_msg(msg: Any) -> dict:
        if isinstance(msg, dict):
            return msg
        return {"role": getattr(msg, "role", "user"), "content": getattr(msg, "content", str(msg))}

    @staticmethod
    def _deserialize_response(data: dict) -> LLMResponse:
        blocks: list[ContentBlockType] = []
        for b in data.get("content", []):
            if b["type"] == "text":
                blocks.append(TextBlock(text=b["text"]))
            elif b["type"] == "tool_use":
                blocks.append(ToolUseBlock(id=b["id"], name=b["name"], input=b["input"]))
        return LLMResponse(
            id=data.get("id", "replay"),
            content=blocks,
            stop_reason=StopReason(data.get("stop_reason", "end_turn")),
            usage=Usage(
                input_tokens=data.get("usage", {}).get("input_tokens", 0),
                output_tokens=data.get("usage", {}).get("output_tokens", 0),
            ),
            model=data.get("model", "replay"),
        )
