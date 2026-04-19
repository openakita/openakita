"""
Message format converter

Converts messages between internal format (Anthropic-like) and OpenAI format.
"""

from ..types import (
    AudioBlock,
    AudioContent,
    ContentBlock,
    DocumentBlock,
    DocumentContent,
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    VideoBlock,
    VideoContent,
)
from .multimodal import convert_content_blocks_to_openai

# Set of providers requiring assistant messages to include reasoning_content when thinking is enabled.
# These providers' reasoning models return reasoning_content in the response.
# Missing this field in multi-turn conversations may result in HTTP 400.
#
# Note: OpenRouter uses the reasoning field (not reasoning_content),
# handled separately in _convert_single_message_to_openai
_REASONING_CONTENT_PROVIDERS = frozenset(
    {
        "moonshot",  # legacy Kimi
        "kimi-cn",  # Kimi China region
        "kimi-int",  # Kimi International region
        "deepseek",  # DeepSeek Reasoner
        "dashscope",  # Alibaba DashScope Qwen3 / QwQ
        "siliconflow",  # Silicon Flow (hosting DeepSeek-R1 / QwQ / Qwen3 etc.)
        "siliconflow-intl",  # Silicon Flow International region
        "volcengine",  # Volcano Engine (hosting DeepSeek-R1 / doubao-seed etc.)
        "zhipu",  # Zhipu GLM-5 / GLM-4.7
    }
)


def _needs_reasoning_content(provider: str) -> bool:
    """Check if provider requires assistant messages to include reasoning_content in thinking mode."""
    return provider in _REASONING_CONTENT_PROVIDERS or provider.startswith("kimi")


def convert_messages_to_openai(
    messages: list[Message],
    system: str = "",
    provider: str = "openai",
    enable_thinking: bool = False,
) -> list[dict]:
    """
    Convert internal message format to OpenAI format.

    Key differences:
    - Internal format has system as separate parameter; OpenAI requires it as first message
    - Internal format has content as ContentBlock list; OpenAI uses string or list
    - Internal format has tool_result as part of user message; OpenAI uses separate tool role message

    Args:
        messages: List of internal format messages
        system: System prompt
        provider: Provider identifier (for multimodal handling, e.g. moonshot supports video)
        enable_thinking: Whether to enable thinking mode
    """
    result = []

    # Add system message
    if system:
        result.append(
            {
                "role": "system",
                "content": system,
            }
        )

    for msg in messages:
        converted = _convert_single_message_to_openai(
            msg,
            provider=provider,
            enable_thinking=enable_thinking,
        )
        if converted:
            if isinstance(converted, list):
                result.extend(converted)
            else:
                result.append(converted)

    return result


def _convert_single_message_to_openai(
    msg: Message,
    provider: str = "openai",
    enable_thinking: bool = False,
) -> dict | list[dict] | None:
    """Convert a single message."""
    if isinstance(msg.content, str):
        # Simple text message
        converted = {"role": msg.role, "content": msg.content}
        if msg.role == "assistant" and _needs_reasoning_content(provider):
            if msg.reasoning_content:
                converted["reasoning_content"] = msg.reasoning_content
                _, clean = _extract_thinking_content(converted["content"])
                converted["content"] = clean
            else:
                extracted, clean = _extract_thinking_content(converted["content"])
                if extracted:
                    converted["reasoning_content"] = extracted
                    converted["content"] = clean
                else:
                    converted["reasoning_content"] = "..."
        elif msg.role == "assistant" and provider == "openrouter" and enable_thinking:
            # OpenRouter uses reasoning field (not reasoning_content)
            _rc = msg.reasoning_content
            if not _rc:
                _rc, clean = _extract_thinking_content(converted["content"])
                if _rc:
                    converted["content"] = clean
            if _rc:
                converted["reasoning"] = _rc
        elif msg.reasoning_content:
            converted["reasoning_content"] = msg.reasoning_content
        return converted

    # Complex content blocks
    content_blocks = msg.content

    # Check for tool_result (needs special handling)
    tool_results = [b for b in content_blocks if isinstance(b, ToolResultBlock)]
    other_blocks = [b for b in content_blocks if not isinstance(b, ToolResultBlock)]

    result = []

    # Handle tool_result (OpenAI uses separate tool role message)
    for tr in tool_results:
        tool_msg: dict = {
            "role": "tool",
            "tool_call_id": tr.tool_use_id,
        }
        if isinstance(tr.content, list):
            # Multimodal tool result (text + images etc.), pass through directly
            tool_msg["content"] = tr.content
        else:
            tool_msg["content"] = tr.content
        result.append(tool_msg)

    # Handle other content blocks
    if other_blocks:
        if msg.role == "assistant":
            # assistant message may contain tool_calls
            tool_uses = [b for b in other_blocks if isinstance(b, ToolUseBlock)]
            text_blocks = [b for b in other_blocks if isinstance(b, TextBlock)]

            assistant_msg = {"role": "assistant"}

            # Text content
            text_content = ""
            if text_blocks:
                if len(text_blocks) == 1:
                    text_content = text_blocks[0].text
                else:
                    text_content = "".join(b.text for b in text_blocks)

            # Providers requiring reasoning_content (DeepSeek Reasoner / Kimi etc.)
            # Always inject if provider requires it, regardless of enable_thinking,
            # to avoid 400 error when thinking degrades and falls back to thinking-only model.
            # Excess reasoning_content is harmless for models that don't need it (API ignores it).
            reasoning_content = None
            if _needs_reasoning_content(provider):
                if msg.reasoning_content:
                    reasoning_content = msg.reasoning_content
                    # reasoning_content provided directly, but text may still have <thinking> tags
                    # (brain.py wraps reasoning_content as <thinking> embedded in TextBlock),
                    # need to clean to prevent tags leaking to content field
                    if text_content:
                        _, text_content = _extract_thinking_content(text_content)
                elif text_content:
                    reasoning_content, text_content = _extract_thinking_content(text_content)

                # Inject placeholder if missing to avoid API 400
                # DeepSeek/DashScope require all assistant messages to include reasoning_content,
                # empty string may be rejected when enable_thinking=true, use "..." placeholder uniformly
                if not reasoning_content:
                    reasoning_content = "..."

                assistant_msg["reasoning_content"] = reasoning_content
            elif provider == "openrouter" and enable_thinking:
                _rc = msg.reasoning_content
                if not _rc and text_content:
                    _rc, text_content = _extract_thinking_content(text_content)
                if _rc:
                    assistant_msg["reasoning"] = _rc
            elif msg.reasoning_content:
                assistant_msg["reasoning_content"] = msg.reasoning_content

            assistant_msg["content"] = text_content if text_content else ""

            # Tool calls
            if tool_uses:
                tc_list = []
                for tu in tool_uses:
                    tc: dict = {
                        "id": tu.id,
                        "type": "function",
                        "function": {
                            "name": tu.name,
                            "arguments": _dict_to_json_string(tu.input),
                        },
                    }
                    if tu.provider_extra:
                        tc["extra_content"] = tu.provider_extra
                    tc_list.append(tc)
                assistant_msg["tool_calls"] = tc_list

            result.append(assistant_msg)
        else:
            # user message, convert content blocks (pass provider for proper video handling)
            openai_content = convert_content_blocks_to_openai(other_blocks, provider=provider)
            result.append(
                {
                    "role": msg.role,
                    "content": openai_content,
                }
            )

    return result if result else None


def _extract_thinking_content(text: str) -> tuple[str | None, str]:
    """Extract <thinking> tag content from text.

    Returns:
        (reasoning_content, clean_text): Reasoning content and cleaned text
    """
    import re

    # Match <thinking>...</thinking> tags
    pattern = r"<thinking>\s*(.*?)\s*</thinking>\s*"
    match = re.search(pattern, text, re.DOTALL)

    if match:
        reasoning_content = match.group(1).strip()
        clean_text = re.sub(pattern, "", text, flags=re.DOTALL).strip()
        return reasoning_content, clean_text

    return None, text


def convert_messages_from_openai(messages: list[dict]) -> tuple[list[Message], str]:
    """
    Convert OpenAI format messages to internal format.

    Returns:
        (messages, system): Message list and system prompt
    """
    result = []
    system = ""

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            system = content
            continue

        if role == "tool":
            # Convert OpenAI tool message to tool_result
            tool_result = ToolResultBlock(
                tool_use_id=msg.get("tool_call_id", ""),
                content=content,
            )
            result.append(Message(role="user", content=[tool_result]))
            continue

        if role == "assistant":
            content_blocks = []

            # Text content
            if content:
                if isinstance(content, str):
                    content_blocks.append(TextBlock(text=content))
                elif isinstance(content, list):
                    for item in content:
                        if item.get("type") == "text":
                            content_blocks.append(TextBlock(text=item.get("text", "")))

            # Tool calls
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                func = tc.get("function", {})
                extra = tc.get("extra_content") or None
                content_blocks.append(
                    ToolUseBlock(
                        id=tc.get("id", ""),
                        name=func.get("name", ""),
                        input=_json_string_to_dict(func.get("arguments", "{}")),
                        provider_extra=extra,
                    )
                )

            if content_blocks:
                result.append(Message(role="assistant", content=content_blocks))
            continue

        # user message
        if isinstance(content, str):
            result.append(Message(role=role, content=content))
        elif isinstance(content, list):
            content_blocks = _convert_openai_content_to_blocks(content)
            result.append(Message(role=role, content=content_blocks))

    return result, system


def _convert_openai_content_to_blocks(content: list[dict]) -> list[ContentBlock]:
    """Convert OpenAI content list to content blocks.

    Supported types:
    - text: Text
    - image_url: Image (OpenAI standard)
    - video_url: Video (Kimi/DashScope extension)
    - input_audio: Audio (OpenAI gpt-4o-audio format)
    - document: Document/PDF (Anthropic format)
    """
    from .multimodal import convert_openai_image_to_internal

    blocks = []
    for item in content:
        item_type = item.get("type", "")

        if item_type == "text":
            blocks.append(TextBlock(text=item.get("text", "")))
        elif item_type == "image_url":
            image = convert_openai_image_to_internal(item)
            if image:
                blocks.append(ImageBlock(image=image))
        elif item_type == "video_url":
            video_url = item.get("video_url", {})
            url = video_url.get("url", "")
            if url:
                import re

                match = re.match(r"data:([^;]+);base64,(.+)", url)
                if match:
                    media_type = match.group(1)
                    data = match.group(2)
                    blocks.append(VideoBlock(video=VideoContent(media_type=media_type, data=data)))
        elif item_type == "input_audio":
            audio_data = item.get("input_audio", {})
            data = audio_data.get("data", "")
            fmt = audio_data.get("format", "wav")
            if data:
                mime_map = {"wav": "audio/wav", "mp3": "audio/mpeg", "pcm16": "audio/pcm"}
                media_type = mime_map.get(fmt, f"audio/{fmt}")
                blocks.append(
                    AudioBlock(audio=AudioContent(media_type=media_type, data=data, format=fmt))
                )
        elif item_type == "document":
            source = item.get("source", {})
            if source.get("type") == "base64":
                blocks.append(
                    DocumentBlock(
                        document=DocumentContent(
                            media_type=source.get("media_type", "application/pdf"),
                            data=source.get("data", ""),
                            filename=item.get("filename", ""),
                        )
                    )
                )

    return blocks


def convert_messages_to_responses(
    messages: list[Message],
    system: str = "",
    provider: str = "openai",
    enable_thinking: bool = False,
) -> tuple[list[dict], str]:
    """Convert internal messages to Responses API input items + instructions.

    Differences from Chat Completions:
    - system prompt not embedded in messages, passed separately as instructions
    - tool_result uses function_call_output item instead of role:"tool" message
    - tool_call uses function_call item instead of assistant.tool_calls

    Returns:
        (input_items, instructions): input array and instructions string
    """
    input_items: list[dict] = []

    for msg in messages:
        converted = _convert_single_message_to_responses(
            msg,
            provider=provider,
            enable_thinking=enable_thinking,
        )
        if converted:
            if isinstance(converted, list):
                input_items.extend(converted)
            else:
                input_items.append(converted)

    return input_items, system


def _convert_single_message_to_responses(
    msg: Message,
    provider: str = "openai",
    enable_thinking: bool = False,
) -> dict | list[dict] | None:
    """Convert single internal message to Responses API input item(s)."""
    from .tools import convert_tool_result_to_responses

    if isinstance(msg.content, str):
        return {"role": msg.role, "content": msg.content}

    content_blocks = msg.content
    tool_results = [b for b in content_blocks if isinstance(b, ToolResultBlock)]
    other_blocks = [b for b in content_blocks if not isinstance(b, ToolResultBlock)]

    result = []

    # tool_result to function_call_output items
    for tr in tool_results:
        content = tr.content if isinstance(tr.content, str) else str(tr.content)
        result.append(convert_tool_result_to_responses(tr.tool_use_id, content))

    if other_blocks:
        if msg.role == "assistant":
            tool_uses = [b for b in other_blocks if isinstance(b, ToolUseBlock)]
            text_blocks = [b for b in other_blocks if isinstance(b, TextBlock)]

            text_content = "".join(b.text for b in text_blocks) if text_blocks else ""

            # Responses API: assistant text output is a message item
            if text_content:
                result.append({"role": "assistant", "content": text_content})

            # tool_use -> function_call items
            import json

            for tu in tool_uses:
                result.append(
                    {
                        "type": "function_call",
                        "call_id": tu.id,
                        "name": tu.name,
                        "arguments": json.dumps(tu.input, ensure_ascii=False),
                        "status": "completed",
                    }
                )
        else:
            # user message
            openai_content = convert_content_blocks_to_openai(other_blocks, provider=provider)
            result.append({"role": msg.role, "content": openai_content})

    return result if result else None


def convert_system_to_openai(system: str) -> dict:
    """Convert system prompt to OpenAI format message."""
    return {"role": "system", "content": system}


def _dict_to_json_string(d: dict) -> str:
    """Convert a dict to a JSON string."""
    import json

    return json.dumps(d, ensure_ascii=False)


def _json_string_to_dict(s: str) -> dict:
    """Convert a JSON string to a dict."""
    import json

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {}
