"""
Message normalization pipeline

Normalizes internal message format into API-acceptable format before sending requests.
Inspired by Claude Code's normalizeMessagesForAPI (18-step normalization pipeline).

Core steps:
1. Filter internal/synthetic messages
2. Merge consecutive user messages
3. Hoist tool_result blocks (within the same user message, tool_results come first)
4. Merge assistant fragments with the same ID
5. Filter orphaned thinking-only messages
6. Sanitize error tool_result content
7. Ensure tool_result and tool_use pairing
8. Strip empty assistant messages
"""

from __future__ import annotations

import copy
import logging

logger = logging.getLogger(__name__)


def normalize_messages_for_api(
    messages: list[dict],
    tool_names: set[str] | None = None,
) -> list[dict]:
    """Normalize internal message format to API-acceptable format.

    Args:
        messages: Raw message list (Anthropic format)
        tool_names: Set of currently available tool names (for validating tool_use)

    Returns:
        Normalized message list
    """
    messages = copy.deepcopy(messages)
    messages = _filter_internal_messages(messages)
    messages = _merge_consecutive_user_messages(messages)
    messages = _hoist_tool_results_in_user(messages)
    messages = _merge_assistant_splits(messages)
    messages = _filter_orphaned_thinking(messages)
    messages = _sanitize_error_tool_results(messages)
    messages = _ensure_tool_result_pairing(messages)
    messages = _strip_empty_assistant(messages)
    messages = _ensure_alternating_roles(messages)
    return messages


def _filter_internal_messages(messages: list[dict]) -> list[dict]:
    """Filter internal/synthetic messages (marked with _internal or _synthetic)."""
    return [m for m in messages if not m.get("_internal") and not m.get("_synthetic")]


def _merge_consecutive_user_messages(messages: list[dict]) -> list[dict]:
    """Merge consecutive user messages.

    The API does not allow consecutive same-role messages.
    Merges the content of consecutive user messages.
    """
    if not messages:
        return messages

    result: list[dict] = [messages[0]]
    for msg in messages[1:]:
        prev = result[-1]
        if prev["role"] == "user" and msg["role"] == "user":
            prev_content = _ensure_content_list(prev.get("content", ""))
            msg_content = _ensure_content_list(msg.get("content", ""))
            prev["content"] = prev_content + msg_content
        else:
            result.append(msg)
    return result


def _hoist_tool_results_in_user(messages: list[dict]) -> list[dict]:
    """Within user messages, place tool_result blocks before other content.

    The Anthropic API requires tool_results to be in the user message immediately
    following the assistant's tool_use, and they should appear first in that
    user message's content.
    """
    for msg in messages:
        if msg["role"] != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        tool_results = [
            b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
        others = [
            b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_result")
        ]

        if tool_results and others:
            msg["content"] = tool_results + others
    return messages


def _merge_assistant_splits(messages: list[dict]) -> list[dict]:
    """Merge consecutive assistant messages."""
    if not messages:
        return messages

    result: list[dict] = [messages[0]]
    for msg in messages[1:]:
        prev = result[-1]
        if prev["role"] == "assistant" and msg["role"] == "assistant":
            prev_content = _ensure_content_list(prev.get("content", ""))
            msg_content = _ensure_content_list(msg.get("content", ""))
            prev["content"] = prev_content + msg_content
        else:
            result.append(msg)
    return result


def _filter_orphaned_thinking(messages: list[dict]) -> list[dict]:
    """Filter assistant messages that contain only thinking blocks.

    If an assistant message has only thinking blocks with no substantive output,
    the API may raise an error or produce confused behavior.
    """
    result = []
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content")
            if isinstance(content, list):
                has_non_thinking = any(
                    isinstance(b, dict) and b.get("type") not in ("thinking", "redacted_thinking")
                    for b in content
                )
                if not has_non_thinking and content:
                    logger.debug("Filtered orphaned thinking-only assistant message")
                    continue
        result.append(msg)
    return result


def _sanitize_error_tool_results(messages: list[dict]) -> list[dict]:
    """Sanitize tool_results with is_error=True, keeping only text content.

    The content of error tool_results may contain unstructured data like tracebacks;
    keeping only text parts avoids API parsing issues.
    """
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result" or not block.get("is_error"):
                continue

            block_content = block.get("content")
            if isinstance(block_content, list):
                text_parts = []
                for part in block_content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)
                block["content"] = "\n".join(text_parts) if text_parts else "Error"
    return messages


def _ensure_tool_result_pairing(messages: list[dict]) -> list[dict]:
    """Ensure every tool_result has a matching tool_use.

    Collects all tool_use IDs from assistant messages, then checks that
    tool_result's tool_use_id in user messages exists in that set.
    """
    tool_use_ids: set[str] = set()
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_use_ids.add(block.get("id", ""))

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        msg["content"] = [
            block
            for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id", "") not in tool_use_ids
            )
        ]
    return messages


def _strip_empty_assistant(messages: list[dict]) -> list[dict]:
    """Remove assistant messages with empty content."""
    result = []
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content")
            if content is None or content == "" or content == []:
                continue
        result.append(msg)
    return result


def _ensure_alternating_roles(messages: list[dict]) -> list[dict]:
    """Ensure alternating message roles (user/assistant), inserting placeholder messages when needed."""
    if not messages:
        return messages

    result: list[dict] = [messages[0]]
    for msg in messages[1:]:
        prev = result[-1]
        if prev["role"] == msg["role"]:
            if msg["role"] == "user":
                result.append({"role": "assistant", "content": "I understand. Continuing."})
            else:
                result.append({"role": "user", "content": "Continue."})
        result.append(msg)
    return result


def _ensure_content_list(content) -> list[dict]:
    """Normalize content to list[dict] format."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return content
    return []
