"""
Prompt Cache support

Implements Anthropic API prompt caching strategy:
- System prompt segmented caching (static part + dynamic part)
- Tool schema cache markers
- Message cache breakpoints (last 1-2 messages)
- Tool schema LRU cache (by name + schema hash)

Reference: Claude Code's getCacheControl / addCacheBreakpoints.
"""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_DYNAMIC_BOUNDARY = "<!-- DYNAMIC_BOUNDARY -->"


def build_cached_system_blocks(system_prompt: str) -> list[dict]:
    """Split the system prompt into static/dynamic parts and add cache_control.

    If the system prompt contains a DYNAMIC_BOUNDARY marker, the part before
    the marker is cached statically. Otherwise, the entire prompt is marked
    for caching.

    Returns:
        List of system blocks in Anthropic format
    """
    if not system_prompt:
        return []

    if SYSTEM_PROMPT_DYNAMIC_BOUNDARY in system_prompt:
        parts = system_prompt.split(SYSTEM_PROMPT_DYNAMIC_BOUNDARY, 1)
        static_part = parts[0].strip()
        dynamic_part = parts[1].strip() if len(parts) > 1 else ""

        blocks = []
        if static_part:
            blocks.append(
                {
                    "type": "text",
                    "text": static_part,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if dynamic_part:
            blocks.append(
                {
                    "type": "text",
                    "text": dynamic_part,
                }
            )
        return blocks

    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def add_tools_cache_control(tools: list[dict]) -> list[dict]:
    """Add cache markers to the tool list.

    Appends cache_control to the last tool so the entire tool list can be
    cached. The tool list should be pre-sorted for cache stability.
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
    """Add cache breakpoints at the end of the message list.

    Adds cache_control to the last content block of the last N messages.
    This allows messages toward the end of the conversation history to be
    cached and reused.

    Args:
        messages: List of messages
        max_breakpoints: Maximum number of breakpoints to add (default 2)
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
            result[i]["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            count += 1

    return result


def _schema_hash(schema: dict) -> str:
    """Compute a stable hash for a JSON Schema."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(canonical.encode()).hexdigest()


@lru_cache(maxsize=512)
def _cached_tool_schema_json(name: str, schema_hash: str, raw_json: str) -> dict:
    """Cache the serialized result of a tool schema."""
    return json.loads(raw_json)


def get_cached_tool_schema(tool: dict) -> dict:
    """Retrieve a cached tool schema, avoiding re-serialization on every request.

    Args:
        tool: Tool definition dict (containing name, description, input_schema)

    Returns:
        Cached tool schema dict
    """
    name = tool.get("name", "")
    schema = tool.get("input_schema", {})
    h = _schema_hash(schema)
    raw = json.dumps(tool, sort_keys=True, separators=(",", ":"))
    return _cached_tool_schema_json(name, h, raw)


def sort_tools_for_cache_stability(tools: list[dict]) -> list[dict]:
    """Sort the tool list by name to ensure prompt cache stability."""
    return sorted(tools, key=lambda t: t.get("name", ""))
