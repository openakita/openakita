"""
Prompt Cache 支持

实现 Anthropic API 的 prompt caching 策略:
- 系统提示分段缓存 (静态部分 + 动态部分)
- 工具 Schema 缓存标记
- 消息缓存断点 (最后 1-2 条消息)
- 工具 Schema LRU 缓存 (按 name + schema hash)

参考 Claude Code 的 getCacheControl / addCacheBreakpoints。
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "<!-- DYNAMIC -->"


def build_cached_system_blocks(system_prompt: str) -> list[dict]:
    """将系统提示拆分为静态/动态部分并添加 cache_control。

    如果系统提示包含 DYNAMIC_BOUNDARY 标记，则标记之前的部分为静态缓存。
    否则整个提示都标记为缓存。

    Returns:
        Anthropic 格式的 system blocks 列表
    """
    if not system_prompt:
        return []

    if SYSTEM_PROMPT_DYNAMIC_BOUNDARY in system_prompt:
        parts = system_prompt.split(SYSTEM_PROMPT_DYNAMIC_BOUNDARY, 1)
        static_part = parts[0].strip()
        dynamic_part = parts[1].strip() if len(parts) > 1 else ""

        blocks = []
        if static_part:
            blocks.append({
                "type": "text",
                "text": static_part,
                "cache_control": {"type": "ephemeral"},
            })
        if dynamic_part:
            blocks.append({
                "type": "text",
                "text": dynamic_part,
            })
        return blocks

    return [{
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]


def add_tools_cache_control(tools: list[dict]) -> list[dict]:
    """为工具列表添加缓存标记。

    最后一个工具添加 cache_control，使整个工具列表可被缓存。
    工具列表应预先排序以保证缓存稳定性。
    """
    if not tools:
        return tools

    result = [dict(t) for t in tools]
    result[-1] = dict(result[-1])
    result[-1]["cache_control"] = {"type": "ephemeral"}
    return result


def add_message_cache_breakpoints(
    messages: list[dict],
    max_breakpoints: int = 2,
) -> list[dict]:
    """在消息列表的末尾添加缓存断点。

    在最后 N 条消息的最后一个 content block 上添加 cache_control。
    这使得对话历史中靠后的消息可以被缓存复用。

    Args:
        messages: 消息列表
        max_breakpoints: 最多添加的断点数量 (默认 2)
    """
    if not messages:
        return messages

    result = [dict(m) for m in messages]
    count = 0

    for i in range(len(result) - 1, -1, -1):
        if count >= max_breakpoints:
            break

        msg = result[i]
        content = msg.get("content")
        if isinstance(content, list) and content:
            result[i] = dict(msg)
            new_content = list(content)
            last_block = dict(new_content[-1])
            last_block["cache_control"] = {"type": "ephemeral"}
            new_content[-1] = last_block
            result[i]["content"] = new_content
            count += 1
        elif isinstance(content, str) and content:
            result[i] = dict(msg)
            result[i]["content"] = [{
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }]
            count += 1

    return result


def _schema_hash(schema: dict) -> str:
    """计算 JSON Schema 的稳定哈希。"""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(canonical.encode()).hexdigest()


@lru_cache(maxsize=512)
def _cached_tool_schema_json(name: str, schema_hash: str, raw_json: str) -> dict:
    """缓存工具 Schema 的序列化结果。"""
    return json.loads(raw_json)


def get_cached_tool_schema(tool: dict) -> dict:
    """获取缓存的工具 Schema，避免每次请求重新序列化。

    Args:
        tool: 工具定义 dict (含 name, description, input_schema)

    Returns:
        缓存后的工具 Schema dict
    """
    name = tool.get("name", "")
    schema = tool.get("input_schema", {})
    h = _schema_hash(schema)
    raw = json.dumps(tool, sort_keys=True, separators=(",", ":"))
    return _cached_tool_schema_json(name, h, raw)


def sort_tools_for_cache_stability(tools: list[dict]) -> list[dict]:
    """按名称排序工具列表，保证 prompt cache 稳定性。"""
    return sorted(tools, key=lambda t: t.get("name", ""))
