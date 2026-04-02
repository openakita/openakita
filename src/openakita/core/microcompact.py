"""
Microcompact — 请求前轻量上下文清理

零 LLM 调用成本的上下文瘦身策略，在发送 API 请求前执行:
1. 过期工具结果清空（按时间阈值）
2. 大工具结果替换为摘要预览
3. 旧 thinking 块移除
4. 旧 tool_use 参数裁剪

参考 Claude Code 的 microcompact 策略。
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

TOOL_RESULT_EXPIRY_SECONDS = 600  # 10 分钟
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
    """对消息列表执行轻量清理。

    注意：这是浅拷贝操作，会修改传入的消息列表。
    调用方应在需要时提前深拷贝。

    Args:
        messages: 消息列表
        tool_result_expiry_s: 工具结果过期秒数
        large_result_threshold: 大结果阈值字符数
        preview_chars: 预览保留字符数
        current_time: 当前时间（测试用）

    Returns:
        清理后的消息列表（原地修改）
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
    """直接丢弃最早的 N 组对话段（History Snip）。

    零 LLM 调用成本，适用于超长对话的快速上下文释放。
    通过 user/assistant 消息对分组，移除最早的 N 组。

    Args:
        messages: 消息列表
        max_groups: 当组数超过此值时触发裁剪
        snip_count: 每次裁剪的组数

    Returns:
        (裁剪后的消息列表, 被移除的消息数量)
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
        snipped_msgs, to_snip, len(remaining),
    )
    return remaining, snipped_msgs


def _group_messages(messages: list[dict]) -> list[list[dict]]:
    """将消息按 user→assistant 对话轮次分组。

    每组以 user 消息开始，包含紧随的 assistant 消息和相关 tool_result。
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
