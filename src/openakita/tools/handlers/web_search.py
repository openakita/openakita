"""
Web Search 处理器

直接使用 ddgs 库执行网络搜索，无需通过 MCP。
"""

import logging
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

    async def _web_search(self, params: dict[str, Any]) -> str:
        """搜索网页"""
        query = params.get("query", "")
        if not query:
            return "错误：query 参数不能为空"

        max_results = min(max(1, params.get("max_results", 5)), 20)
        region = params.get("region", "wt-wt")
        safesearch = params.get("safesearch", "moderate")

        try:
            from ddgs import DDGS
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            return f"错误：{import_or_hint('ddgs')}"

        try:
            with DDGS() as ddgs:
                results = list(
                    ddgs.text(
                        query,
                        max_results=max_results,
                        region=region,
                        safesearch=safesearch,
                    )
                )
                return self._format_web_results(results)
        except Exception as e:
            logger.error(f"Web search failed: {e}")
            return f"搜索失败: {str(e)}"

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
            from ddgs import DDGS
        except ImportError:
            from openakita.tools._import_helper import import_or_hint
            return f"错误：{import_or_hint('ddgs')}"

        try:
            with DDGS() as ddgs:
                results = list(
                    ddgs.news(
                        query,
                        max_results=max_results,
                        region=region,
                        safesearch=safesearch,
                        timelimit=timelimit,
                    )
                )
                return self._format_news_results(results)
        except Exception as e:
            logger.error(f"News search failed: {e}")
            return f"新闻搜索失败: {str(e)}"

    @staticmethod
    def _format_web_results(results: list) -> str:
        """格式化网页搜索结果"""
        if not results:
            return "未找到相关结果"

        output = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            url = r.get("href", r.get("link", ""))
            body = r.get("body", r.get("snippet", ""))
            output.append(f"**{i}. {title}**\n{url}\n{body}\n")

        return "\n".join(output)

    @staticmethod
    def _format_news_results(results: list) -> str:
        """格式化新闻搜索结果"""
        if not results:
            return "未找到相关新闻"

        output = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            url = r.get("url", r.get("link", ""))
            body = r.get("body", r.get("excerpt", ""))
            date = r.get("date", "")
            source = r.get("source", "")

            header = f"**{i}. {title}**"
            if source or date:
                header += f" ({source} {date})"

            output.append(f"{header}\n{url}\n{body}\n")

        return "\n".join(output)


def create_handler(agent: Any = None):
    """创建 WebSearchHandler 实例并返回 handle 方法"""
    handler = WebSearchHandler(agent)
    return handler.handle
