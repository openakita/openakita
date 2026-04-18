"""
Web Search Handler

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
    """Web Search handler"""

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
        """Search the web"""
        query = params.get("query", "")
        if not query:
            return "Error: query parameter must not be empty"

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
                "Search is temporarily unavailable (all search providers are unreachable). "
                'Please inform the user that "web search is currently unavailable" '
                "and suggest retrying later or using a different tool. "
                "Do not retry repeatedly or fabricate search results."
            )

    async def _news_search(self, params: dict[str, Any]) -> str:
        """Search news"""
        query = params.get("query", "")
        if not query:
            return "Error: query parameter must not be empty"

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
                "News search is temporarily unavailable (all search providers are unreachable). "
                'Please inform the user that "web search is currently unavailable" '
                "and suggest retrying later or using a different tool. "
                "Do not retry repeatedly or fabricate search results."
            )

    @staticmethod
    def _format_web_results(results: list) -> str:
        """Format search results (shared by web + news)"""
        if not results:
            return "No relevant results found"

        output = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "No title")
            url = r.get("url", r.get("href", r.get("link", "")))
            body = r.get("body", r.get("snippet", r.get("content", "")))
            output.append(f"**{i}. {title}**\n{url}\n{body}\n")

        return "\n".join(output)


def create_handler(agent: Any = None):
    """Create a WebSearchHandler instance and return its handle method"""
    handler = WebSearchHandler(agent)
    return handler.handle
