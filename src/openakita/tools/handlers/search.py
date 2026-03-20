"""
Semantic Search 处理器

基于向量相似度的语义搜索 — 按含义搜索文件内容。
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class SearchHandler:
    TOOLS = ["semantic_search"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "semantic_search":
            return await self._semantic_search(params)
        return f"❌ Unknown search tool: {tool_name}"

    async def _semantic_search(self, params: dict) -> str:
        query = params.get("query", "").strip()
        if not query:
            return "❌ semantic_search 缺少必要参数 'query'。"

        search_path = params.get("path", "")
        max_results = params.get("max_results", 10)
        max_results = max(1, min(15, max_results))

        try:
            memory_manager = getattr(self.agent, "memory_manager", None)
            if memory_manager and hasattr(memory_manager, "vector_store"):
                vector_store = memory_manager.vector_store
                if vector_store and hasattr(vector_store, "search"):
                    results = await vector_store.search(
                        query, top_k=max_results, filter_path=search_path or None
                    )
                    if results:
                        return self._format_results(query, results)
        except Exception as e:
            logger.debug(f"Vector search unavailable: {e}")

        return await self._fallback_keyword_search(query, search_path, max_results)

    async def _fallback_keyword_search(
        self, query: str, search_path: str, max_results: int
    ) -> str:
        """Fallback: use grep-based keyword search when vector store is unavailable."""
        import re
        keywords = re.findall(r"\b[a-zA-Z_]\w{2,}\b", query)
        if not keywords:
            keywords = query.split()[:3]

        if not keywords:
            return f"❌ 无法从查询中提取有效关键词: '{query}'"

        all_results = []
        for keyword in keywords[:3]:
            try:
                results = await self.agent.file_tool.grep(
                    keyword,
                    search_path or ".",
                    max_results=max_results,
                    case_insensitive=True,
                )
                for r in results:
                    r["_keyword"] = keyword
                all_results.extend(results)
            except Exception:
                continue

        if not all_results:
            scope = search_path or "workspace"
            return f"No results found for '{query}' in {scope}."

        seen = set()
        unique = []
        for r in all_results:
            key = (r.get("file", ""), r.get("line", 0))
            if key not in seen:
                seen.add(key)
                unique.append(r)

        unique = unique[:max_results]

        lines = [f"Semantic search results for: '{query}'", ""]
        lines.append(
            "(Note: Using keyword fallback — vector search not available. "
            "Results may be less accurate.)"
        )
        lines.append("")
        for r in unique:
            lines.append(f"  {r.get('file', '?')}:{r.get('line', '?')}: {r.get('text', '').strip()}")

        return "\n".join(lines)

    @staticmethod
    def _format_results(query: str, results: list) -> str:
        lines = [f"Semantic search results for: '{query}'", ""]
        for i, r in enumerate(results, 1):
            score = r.get("score", 0)
            file_path = r.get("file", r.get("path", "?"))
            text = r.get("text", r.get("content", "")).strip()
            line_num = r.get("line", "")
            loc = f":{line_num}" if line_num else ""
            lines.append(f"  [{i}] {file_path}{loc} (score: {score:.3f})")
            preview = text[:200] + "..." if len(text) > 200 else text
            for preview_line in preview.split("\n")[:5]:
                lines.append(f"      {preview_line}")
            lines.append("")
        return "\n".join(lines)


def create_handler(agent: "Agent"):
    handler = SearchHandler(agent)
    return handler.handle
