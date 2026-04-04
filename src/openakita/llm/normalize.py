"""
消息规范化管线

在发送 API 请求前，将内部消息格式规范化为 API 可接受的格式。
参考 Claude Code 的 normalizeMessagesForAPI（18 步规范化流水线）。

核心步骤:
1. 过滤内部/虚拟消息
2. 合并连续 user 消息
3. tool_result 上提（同一 user 消息中 tool_result 排在前面）
4. 合并同 ID assistant 分片
5. 过滤 orphaned thinking-only 消息
6. error tool_result 内容清洗
7. 确保 tool_result 与 tool_use 配对
8. 过滤空 assistant 消息
"""

from __future__ import annotations

import copy
import logging

logger = logging.getLogger(__name__)


def normalize_messages_for_api(
    messages: list[dict],
    tool_names: set[str] | None = None,
) -> list[dict]:
    """将内部消息格式规范化为 API 可接受的格式。

    Args:
        messages: 原始消息列表 (Anthropic 格式)
        tool_names: 当前可用工具名称集合 (用于验证 tool_use)

    Returns:
        规范化后的消息列表
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
    """过滤内部/虚拟消息（带 _internal 或 _synthetic 标记）。"""
    return [
        m for m in messages
        if not m.get("_internal") and not m.get("_synthetic")
    ]


def _merge_consecutive_user_messages(messages: list[dict]) -> list[dict]:
    """合并连续的 user 消息。

    API 不允许连续的同角色消息。将连续 user 消息的 content 合并。
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
    """在 user 消息中，tool_result 块排在其他内容前面。

    Anthropic API 要求 tool_result 必须紧跟在 assistant 的 tool_use 后面的
    user 消息中，且应排在该 user 消息内容的最前面。
    """
    for msg in messages:
        if msg["role"] != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        others = [b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_result")]

        if tool_results and others:
            msg["content"] = tool_results + others
    return messages


def _merge_assistant_splits(messages: list[dict]) -> list[dict]:
    """合并连续的 assistant 消息。"""
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
    """过滤只包含 thinking 块的 assistant 消息。

    如果 assistant 消息只有 thinking 块没有实质输出，
    API 可能报错或产生混乱。
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
    """清洗 is_error=True 的 tool_result，只保留 text 内容。

    错误 tool_result 的 content 可能包含 traceback 等非结构化内容，
    只保留 text 类型避免 API 解析问题。
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
    """确保每个 tool_result 都有对应的 tool_use。

    收集 assistant 消息中所有 tool_use 的 id，
    然后检查 user 消息中 tool_result 的 tool_use_id 是否存在。
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
            block for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id", "") not in tool_use_ids
            )
        ]
    return messages


def _strip_empty_assistant(messages: list[dict]) -> list[dict]:
    """移除空内容的 assistant 消息。"""
    result = []
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content")
            if content is None or content == "" or content == []:
                continue
        result.append(msg)
    return result


def _ensure_alternating_roles(messages: list[dict]) -> list[dict]:
    """确保消息角色交替（user/assistant），必要时插入占位消息。"""
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
    """将 content 统一为 list[dict] 格式。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return content
    return []
