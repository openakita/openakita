"""
ToolSearch 工具处理器

参考 CC ToolSearchTool：接受自然语言查询，在所有工具（含延迟加载的）中
搜索匹配项，返回完整 schema，并将发现的工具注册到 discovered 集合，
使其在后续请求中自动以完整 schema 加载。
"""

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase tokens for matching.

    Supports both Latin (a-z0-9_) and CJK characters.
    CJK characters are treated as individual tokens (bigrams for short queries).
    """
    latin = set(re.findall(r"[a-z0-9_]+", text.lower()))
    # Extract CJK characters as individual tokens
    cjk = set(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]+", text))
    # Split CJK runs into individual characters for matching
    cjk_chars = set()
    for run in cjk:
        for ch in run:
            cjk_chars.add(ch)
    return latin | cjk_chars


def _score_tool(query_tokens: set[str], hint: str) -> float:
    """Score a tool against query tokens using token overlap."""
    hint_tokens = _tokenize(hint)
    if not hint_tokens:
        return 0.0
    overlap = query_tokens & hint_tokens
    return len(overlap) / max(len(query_tokens), 1)


class ToolSearchHandler:
    """ToolSearch 工具处理器"""

    TOOLS = ["tool_search"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "tool_search":
            return await self._search(params)
        return f"Unknown tool_search tool: {tool_name}"

    async def _search(self, params: dict[str, Any]) -> str:
        query = params.get("query", "").strip()
        if not query:
            return "tool_search requires a 'query' parameter."

        from ..defer_config import build_search_hint

        query_tokens = _tokenize(query)
        if not query_tokens:
            return "Query too short or contains no searchable terms."

        all_tools = getattr(self.agent, "_tools", [])
        if not all_tools:
            return "No tools available."

        scored: list[tuple[float, dict]] = []
        for tool in all_tools:
            name = tool.get("name", "")
            if not name:
                continue
            hint = build_search_hint(tool)
            score = _score_tool(query_tokens, hint)
            if score > 0.1:
                scored.append((score, tool))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:MAX_RESULTS]

        if not top:
            return f"No tools found matching '{query}'. Try different keywords."

        # Register discovered tools for full-schema loading in future turns
        discovered_names = []
        for _, tool in top:
            name = tool.get("name", "")
            if hasattr(self.agent, "_discovered_tools"):
                self.agent._discovered_tools.add(name)
            discovered_names.append(name)

        logger.info(
            "[ToolSearch] query=%r → discovered %d tools: %s",
            query, len(discovered_names), discovered_names,
        )

        results = []
        for _score, tool in top:
            entry = {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "input_schema": tool.get("input_schema", {}),
                "category": tool.get("category", ""),
            }
            results.append(entry)

        header = (
            f"Found {len(results)} tool(s) matching '{query}'.\n"
            "These tools are now available with full parameters:\n\n"
        )
        body = json.dumps(results, ensure_ascii=False, indent=2)
        return header + body


def create_handler(agent: "Agent"):
    handler = ToolSearchHandler(agent)
    return handler.handle
