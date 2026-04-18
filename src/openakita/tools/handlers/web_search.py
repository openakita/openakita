"""
Web Search 处理器

Delegates to SearchProviderRouter which supports multiple backends:
  DuckDuckGo (default, no key) | Brave | Tavily | Exa

Configure via .env:
  SEARCH_PROVIDER=auto          # auto|ddgs|brave|tavily|exa
  BRAVE_API_KEY=...
  TAVILY_API_KEY=...
  EXA_API_KEY=...
  SEARCH_FALLBACK_ENABLED=true
"""

import logging
import traceback
from typing import Any

logger = logging.getLogger(__name__)


class WebSearchHandler:
    """Web Search 处理器"""

    TOOLS = ["web_search", "news_search"]

    def __init__(self, agent: Any = None):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name == "web_search":
            return await self._web_search(params)
        elif tool_name == "news_search":
            return await self._news_search(params)
        else:
            return f"Unknown web search tool: {tool_name}"

    def _get_router(self):
        """Lazy-load the router (respects config reloads)."""
        from openakita.tools.handlers.search_providers import get_router

        return get_router()

    async def _web_search(self, params: dict[str, Any]) -> str:
        """搜索网页"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")

        try:
            router = self._get_router()
            results = await router.get_web_results(
                query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
            )
            provider = router.active_provider_name
            logger.debug("[WebSearch] web_search via '%s', got %d results", provider, len(results))
            return self._format_web_results(results)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("Web search failed: %s\n%s", e, tb)
            return (
                "搜索暂时不可用（所有搜索提供商均无法访问）。"
                "请直接告知用户\"当前无法联网搜索\"，建议稍后重试或改用其他工具，"
                "不要反复重试，也不要伪造搜索结果。"
            )

    async def _news_search(self, params: dict[str, Any]) -> str:
        """搜索新闻"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")
        timelimit = params.get("timelimit")

        try:
            router = self._get_router()
            results = await router.get_news_results(
                query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
            )
            provider = router.active_provider_name
            logger.debug("[WebSearch] news_search via '%s', got %d results", provider, len(results))
            return self._format_web_results(results)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("News search failed: %s\n%s", e, tb)
            return (
                "新闻搜索暂时不可用（所有搜索提供商均无法访问）。"
                "请直接告知用户\"当前无法联网搜索\"，建议稍后重试或改用其他工具，"
                "不要反复重试，也不要伪造搜索结果。"
            )

    @staticmethod
    def _format_web_results(results: list) -> str:
        """格式化搜索结果（web + news 共用）"""
        if not results:
            return "未找到相关结果"

        output = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            url = r.get("url", r.get("href", r.get("link", "")))
            body = r.get("body", r.get("snippet", r.get("content", "")))
            output.append(f"**{i}. {title}**\n{url}\n{body}\n")

        return "\n".join(output)


def create_handler(agent: Any = None):
    """创建 WebSearchHandler 实例并返回 handle 方法"""
    handler = WebSearchHandler(agent)
    return handler.handle
