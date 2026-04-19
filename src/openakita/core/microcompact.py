"""
Microcompact — lightweight context cleanup before requests

A zero LLM-cost context trimming strategy executed before sending API requests:
1. Clear expired tool results (by time threshold)
2. Replace large tool results with summary previews
3. Remove old thinking blocks
4. Trim old tool_use parameters

Modeled after Claude Code's microcompact strategy.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

TOOL_RESULT_EXPIRY_SECONDS = 600  # 10 minutes
LARGE_RESULT_PREVIEW_CHARS = 500
LARGE_RESULT_THRESHOLD_CHARS = 8000


def microcompact(
    messages: list[dict],
    *,
    tool_result_expiry_s: float = TOOL_RESULT_EXPIRY_SECONDS,
    large_result_threshold: int = LARGE_RESULT_THRESHOLD_CHARS,
    preview_chars: int = LARGE_RESULT_PREVIEW_CHARS,
    current_time: float | None = None,
) -> list[dict]:
    """Perform lightweight cleanup on the message list.

    Note: This is a shallow-copy operation that mutates the passed-in list.
    The caller should deep-copy beforehand if needed.

    Args:
        messages: message list
        tool_result_expiry_s: tool result expiry in seconds
        large_result_threshold: character threshold for large results
        preview_chars: number of characters to keep as preview
        current_time: current time (for testing)

    Returns:
        cleaned message list (mutated in place)
    """
    now = current_time or time.time()
    cleaned = 0
    total_messages = len(messages)

    for i, msg in enumerate(messages):
        # Only process messages not in the last 3 (keep recent context intact)
        is_recent = i >= total_messages - 3

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type", "")

            # 1. Clear expired tool results (except recent ones)
            if block_type == "tool_result" and not is_recent:
                ts = block.get("_timestamp", 0)
                if ts > 0 and (now - ts) > tool_result_expiry_s:
                    original_content = block.get("content", "")
                    if isinstance(original_content, str) and len(original_content) > 100:
                        block["content"] = "[expired tool result]"
                        cleaned += 1

            # 2. Truncate large tool results to preview
            if block_type == "tool_result" and not is_recent:
                result_content = block.get("content", "")
                if isinstance(result_content, str) and len(result_content) > large_result_threshold:
                    preview = result_content[:preview_chars]
                    total = len(result_content)
                    block["content"] = (
                        f"{preview}\n\n... [{total} chars total, truncated by microcompact]"
                    )
                    cleaned += 1

            # 3. Remove old thinking blocks (except last 2 messages)
            if block_type in ("thinking", "redacted_thinking") and not is_recent:
                if len(block.get("thinking", "")) > 200:
                    block["thinking"] = "[thinking removed by microcompact]"
                    cleaned += 1

    if cleaned > 0:
        logger.debug("microcompact: cleaned %d blocks in %d messages", cleaned, total_messages)

    return messages


def snip_old_segments(
    messages: list[dict],
    *,
    max_groups: int = 50,
    snip_count: int = 5,
) -> tuple[list[dict], int]:
    """Drop the earliest N conversation groups (History Snip).

    Zero LLM cost, suitable for fast context release in very long conversations.
    Groups messages by user/assistant pairs and removes the earliest N groups.

    Args:
        messages: message list
        max_groups: trigger pruning when the group count exceeds this value
        snip_count: number of groups to prune each time

    Returns:
        (pruned message list, number of messages removed)
    """
    groups = _group_messages(messages)
    if len(groups) <= max_groups:
        return messages, 0

    to_snip = min(snip_count, len(groups) - 1)  # Keep at least 1 group
    snipped_msgs = 0
    for i in range(to_snip):
        snipped_msgs += len(groups[i])

    boundary_marker = {
        "role": "user",
        "content": f"[HISTORY_SNIP: removed {snipped_msgs} messages from {to_snip} conversation turns]",
        "_internal": False,
    }

    remaining = [boundary_marker]
    for group in groups[to_snip:]:
        remaining.extend(group)

    logger.info(
        "history_snip: removed %d messages (%d groups), %d remaining",
        snipped_msgs,
        to_snip,
        len(remaining),
    )
    return remaining, snipped_msgs


def _group_messages(messages: list[dict]) -> list[list[dict]]:
    """Group messages by user-assistant conversation turns.

    Each group starts with a user message and includes the following
    assistant message and related tool_result blocks.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []

    for msg in messages:
        role = msg.get("role", "")
        if role == "user" and current:
            groups.append(current)
            current = []
        current.append(msg)

    if current:
        groups.append(current)

    return groups
