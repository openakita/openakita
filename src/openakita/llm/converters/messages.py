"""
消息格式转换器

负责在内部格式（Anthropic-like）和 OpenAI 格式之间转换消息。
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

# 启用 thinking 时，要求 assistant 消息携带 reasoning_content 的服务商集合
# 这些服务商的思考模型在响应中返回 reasoning_content，
# 多轮对话时缺少此字段会返回 400
_REASONING_CONTENT_PROVIDERS = frozenset({
    "moonshot",         # legacy Kimi
    "kimi-cn",          # Kimi 中国区
    "kimi-int",         # Kimi 国际区
    "deepseek",         # DeepSeek Reasoner
    "dashscope",        # 通义千问 Qwen3 / QwQ
    "siliconflow",      # 硅基流动（托管 DeepSeek-R1 / QwQ / Qwen3 等）
    "siliconflow-intl", # 硅基流动国际区
    "volcengine",       # 火山引擎（托管 DeepSeek-R1 / doubao-seed 等）
    "zhipu",            # 智谱 GLM-5 / GLM-4.7
})


def _needs_reasoning_content(provider: str) -> bool:
    """服务商在 thinking 模式下是否要求 assistant 消息包含 reasoning_content"""
    return provider in _REASONING_CONTENT_PROVIDERS or provider.startswith("kimi")


def convert_messages_to_openai(
    messages: list[Message],
    system: str = "",
    provider: str = "openai",
    enable_thinking: bool = False,
) -> list[dict]:
    """
    将内部消息格式转换为 OpenAI 格式

    主要差异：
    - 内部格式的 system 是独立参数，OpenAI 需要作为第一条消息
    - 内部格式的 content 是 ContentBlock 列表，OpenAI 可以是字符串或列表
    - 内部格式的 tool_result 是 user 消息的一部分，OpenAI 是独立的 tool 角色消息

    Args:
        messages: 内部格式消息列表
        system: 系统提示
        provider: 服务商标识（用于多媒体处理，如 moonshot 支持视频）
        enable_thinking: 是否启用思考模式
    """
    result = []

    # 添加 system 消息
    if system:
        result.append(
            {
                "role": "system",
                "content": system,
            }
        )

    for msg in messages:
        converted = _convert_single_message_to_openai(
            msg, provider=provider, enable_thinking=enable_thinking,
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
    """转换单条消息"""
    if isinstance(msg.content, str):
        # 简单文本消息
        converted = {"role": msg.role, "content": msg.content}
        if msg.role == "assistant" and enable_thinking and _needs_reasoning_content(provider):
            converted["reasoning_content"] = msg.reasoning_content or ""
        elif msg.reasoning_content:
            converted["reasoning_content"] = msg.reasoning_content
        return converted

    # 复杂内容块
    content_blocks = msg.content

    # 检查是否有 tool_result（需要特殊处理）
    tool_results = [b for b in content_blocks if isinstance(b, ToolResultBlock)]
    other_blocks = [b for b in content_blocks if not isinstance(b, ToolResultBlock)]

    result = []

    # 处理 tool_result（OpenAI 使用独立的 tool 角色消息）
    for tr in tool_results:
        result.append(
            {
                "role": "tool",
                "tool_call_id": tr.tool_use_id,
                "content": tr.content,
            }
        )

    # 处理其他内容块
    if other_blocks:
        if msg.role == "assistant":
            # assistant 消息可能包含 tool_calls
            tool_uses = [b for b in other_blocks if isinstance(b, ToolUseBlock)]
            text_blocks = [b for b in other_blocks if isinstance(b, TextBlock)]

            assistant_msg = {"role": "assistant"}

            # 文本内容
            text_content = ""
            if text_blocks:
                if len(text_blocks) == 1:
                    text_content = text_blocks[0].text
                else:
                    text_content = "".join(b.text for b in text_blocks)

            # 需要 reasoning_content 的服务商（DeepSeek Reasoner / Kimi 等）
            reasoning_content = None
            if enable_thinking and _needs_reasoning_content(provider):
                if msg.reasoning_content:
                    reasoning_content = msg.reasoning_content
                elif text_content:
                    reasoning_content, text_content = _extract_thinking_content(text_content)

                # 缺失时注入占位符，避免 API 400
                # DeepSeek 要求所有 assistant 消息都携带，Kimi 至少在有 tool_calls 时需要
                if reasoning_content is None:
                    reasoning_content = "..." if tool_uses else ""

                assistant_msg["reasoning_content"] = reasoning_content
            elif msg.reasoning_content:
                assistant_msg["reasoning_content"] = msg.reasoning_content

            assistant_msg["content"] = text_content if text_content else None

            # 工具调用
            if tool_uses:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tu.id,
                        "type": "function",
                        "function": {
                            "name": tu.name,
                            "arguments": _dict_to_json_string(tu.input),
                        },
                    }
                    for tu in tool_uses
                ]

            result.append(assistant_msg)
        else:
            # user 消息，转换内容块（传递 provider 以正确处理视频）
            openai_content = convert_content_blocks_to_openai(other_blocks, provider=provider)
            result.append(
                {
                    "role": msg.role,
                    "content": openai_content,
                }
            )

    return result if result else None


def _extract_thinking_content(text: str) -> tuple[str | None, str]:
    """从文本中提取 <thinking> 标签内容

    Returns:
        (reasoning_content, clean_text): 思考内容和清理后的文本
    """
    import re

    # 匹配 <thinking>...</thinking> 标签
    pattern = r"<thinking>\s*(.*?)\s*</thinking>\s*"
    match = re.search(pattern, text, re.DOTALL)

    if match:
        reasoning_content = match.group(1).strip()
        clean_text = re.sub(pattern, "", text, flags=re.DOTALL).strip()
        return reasoning_content, clean_text

    return None, text


def convert_messages_from_openai(messages: list[dict]) -> tuple[list[Message], str]:
    """
    将 OpenAI 格式消息转换为内部格式

    Returns:
        (messages, system): 消息列表和系统提示
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
            # OpenAI 的 tool 消息转换为 tool_result
            tool_result = ToolResultBlock(
                tool_use_id=msg.get("tool_call_id", ""),
                content=content,
            )
            result.append(Message(role="user", content=[tool_result]))
            continue

        if role == "assistant":
            content_blocks = []

            # 文本内容
            if content:
                if isinstance(content, str):
                    content_blocks.append(TextBlock(text=content))
                elif isinstance(content, list):
                    for item in content:
                        if item.get("type") == "text":
                            content_blocks.append(TextBlock(text=item.get("text", "")))

            # 工具调用
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                func = tc.get("function", {})
                content_blocks.append(
                    ToolUseBlock(
                        id=tc.get("id", ""),
                        name=func.get("name", ""),
                        input=_json_string_to_dict(func.get("arguments", "{}")),
                    )
                )

            if content_blocks:
                result.append(Message(role="assistant", content=content_blocks))
            continue

        # user 消息
        if isinstance(content, str):
            result.append(Message(role=role, content=content))
        elif isinstance(content, list):
            content_blocks = _convert_openai_content_to_blocks(content)
            result.append(Message(role=role, content=content_blocks))

    return result, system


def _convert_openai_content_to_blocks(content: list[dict]) -> list[ContentBlock]:
    """将 OpenAI 内容列表转换为内容块

    支持的类型:
    - text: 文本
    - image_url: 图片（OpenAI 标准）
    - video_url: 视频（Kimi/DashScope 扩展）
    - input_audio: 音频（OpenAI gpt-4o-audio 格式）
    - document: 文档/PDF（Anthropic 格式）
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
                blocks.append(AudioBlock(audio=AudioContent(media_type=media_type, data=data, format=fmt)))
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


def convert_system_to_openai(system: str) -> dict:
    """将系统提示转换为 OpenAI 格式消息"""
    return {"role": "system", "content": system}


def _dict_to_json_string(d: dict) -> str:
    """将字典转换为 JSON 字符串"""
    import json

    return json.dumps(d, ensure_ascii=False)


def _json_string_to_dict(s: str) -> dict:
    """将 JSON 字符串转换为字典"""
    import json

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {}
