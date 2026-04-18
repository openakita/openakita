"""
LLM Adapter Layer

Provides a backward-compatible interface for the existing Brain class
while using the new LLMClient internally.
"""

import logging
from dataclasses import dataclass, field

from .client import LLMClient, get_default_client
from .types import (
    ImageBlock,
    ImageContent,
    LLMResponse,
    Message,
    TextBlock,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)


@dataclass
class LegacyResponse:
    """Response format compatible with the legacy Brain.Response"""

    content: str
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class LegacyContext:
    """Context format compatible with the legacy Brain.Context"""

    messages: list[dict] = field(default_factory=list)
    system: str = ""
    tools: list[dict] = field(default_factory=list)


class LLMAdapter:
    """
    LLM Adapter

    Provides an interface compatible with the legacy Brain class,
    using LLMClient internally.

    Example:
        adapter = LLMAdapter()
        response = await adapter.think(
            prompt="Hello",
            system="You are helpful",
            tools=[{"name": "search", ...}]
        )
    """

    def __init__(self, client: LLMClient | None = None):
        """
        Initialize the adapter.

        Args:
            client: LLMClient instance; defaults to the global singleton
        """
        self._client = client or get_default_client()

    async def think(
        self,
        prompt: str,
        context: LegacyContext | None = None,
        system: str | None = None,
        tools: list[dict] | None = None,
        enable_thinking: bool = False,
        max_tokens: int = 4096,
    ) -> LegacyResponse:
        """
        Interface compatible with the legacy Brain.think.

        Args:
            prompt: User input
            context: Conversation context (legacy format)
            system: System prompt
            tools: Available tools list (legacy format)
            enable_thinking: Whether to enable thinking mode
            max_tokens: Maximum output tokens

        Returns:
            A response object in the legacy format
        """
        # Convert message format
        messages = self._convert_legacy_messages(context)
        messages.append(Message(role="user", content=prompt))

        # Determine system prompt
        sys_prompt = system or (context.system if context else "")

        # Convert tool format
        converted_tools = None
        if tools:
            converted_tools = self._convert_legacy_tools(tools)
        elif context and context.tools:
            converted_tools = self._convert_legacy_tools(context.tools)

        # Call the new LLMClient
        try:
            response = await self._client.chat(
                messages=messages,
                system=sys_prompt,
                tools=converted_tools,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            )

            return self._convert_to_legacy_response(response)

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

    def _convert_legacy_messages(self, context: LegacyContext | None) -> list[Message]:
        """Convert legacy-format messages to the new format"""
        if not context or not context.messages:
            return []

        messages = []
        for msg in context.messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if isinstance(content, str):
                messages.append(Message(role=role, content=content))
            elif isinstance(content, list):
                # Handle multimodal content
                blocks = []
                for part in content:
                    if isinstance(part, dict):
                        part_type = part.get("type", "")
                        if part_type == "text":
                            blocks.append(TextBlock(text=part.get("text", "")))
                        elif part_type == "image":
                            source = part.get("source", {})
                            if source.get("type") == "base64":
                                blocks.append(
                                    ImageBlock(
                                        image=ImageContent(
                                            media_type=source.get("media_type", "image/jpeg"),
                                            data=source.get("data", ""),
                                        )
                                    )
                                )
                        elif part_type == "tool_use":
                            blocks.append(
                                ToolUseBlock(
                                    id=part.get("id", ""),
                                    name=part.get("name", ""),
                                    input=part.get("input", {}),
                                )
                            )
                        elif part_type == "tool_result":
                            blocks.append(
                                ToolResultBlock(
                                    tool_use_id=part.get("tool_use_id", ""),
                                    content=part.get("content", ""),
                                    is_error=part.get("is_error", False),
                                )
                            )
                    elif isinstance(part, str):
                        blocks.append(TextBlock(text=part))

                if blocks:
                    messages.append(Message(role=role, content=blocks))

        return messages

    def _convert_legacy_tools(self, tools: list[dict]) -> list[Tool]:
        """Convert legacy-format tools to the new format"""
        converted = []
        for tool in tools:
            converted.append(
                Tool(
                    name=tool.get("name", ""),
                    description=tool.get("description", ""),
                    input_schema=tool.get("input_schema", {}),
                )
            )
        return converted

    def _convert_to_legacy_response(self, response: LLMResponse) -> LegacyResponse:
        """Convert new-format response to legacy format"""
        # Extract text content
        content = response.text

        # Extract tool calls
        tool_calls = []
        for tc in response.tool_calls:
            tool_calls.append(
                {
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                }
            )

        return LegacyResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=response.stop_reason.value,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

    @property
    def client(self) -> LLMClient:
        """Return the underlying LLMClient"""
        return self._client


# Convenience function
async def think(
    prompt: str,
    context: LegacyContext | None = None,
    system: str | None = None,
    tools: list[dict] | None = None,
    **kwargs,
) -> LegacyResponse:
    """
    Convenience function: think using the default adapter.

    This is a direct replacement for the legacy Brain.think.
    """
    adapter = LLMAdapter()
    return await adapter.think(prompt, context, system, tools, **kwargs)
