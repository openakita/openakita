"""
Web Search MCP 服务器

基于 DuckDuckGo 的网络搜索服务，无需 API Key。

启动方式：
    python -m openakita.mcp_servers.web_search

工具：
    - web_search: 搜索网页
    - news_search: 搜索新闻
"""

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# 创建 MCP 服务器实例
mcp = FastMCP(
    name="web-search",
    instructions="""Web Search MCP Server - 基于 DuckDuckGo 的网络搜索服务。

可用工具：
- web_search: 搜索网页，返回标题、链接和摘要
- news_search: 搜索新闻，返回最新新闻文章

使用示例：
- 搜索信息：web_search(query="Python 教程", max_results=5)
- 搜索新闻：news_search(query="AI 最新进展", max_results=5)
""",
)


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


@mcp.tool()
def web_search(
    query: str, max_results: int = 5, region: str = "wt-wt", safesearch: str = "moderate"
) -> str:
    """
    Search the web using DuckDuckGo.

    Args:
        query: Search query string
        max_results: Maximum number of results (default: 5, max: 20)
        region: Region code (default: "wt-wt" for worldwide, "cn-zh" for China)
        safesearch: Safe search level ("on", "moderate", "off")

    Returns:
        Formatted search results with title, URL, and snippet
    """
    try:
        from ddgs import DDGS
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    # 限制结果数量
    max_results = min(max(1, max_results), 20)

    try:
        with DDGS() as ddgs:
            results = list(
                ddgs.text(query, max_results=max_results, region=region, safesearch=safesearch)
            )
            return _format_web_results(results)
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return f"搜索失败: {str(e)}"


@mcp.tool()
def news_search(
    query: str,
    max_results: int = 5,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timelimit: str | None = None,
) -> str:
    """
    Search news using DuckDuckGo.

    Args:
        query: Search query string
        max_results: Maximum number of results (default: 5, max: 20)
        region: Region code (default: "wt-wt" for worldwide)
        safesearch: Safe search level ("on", "moderate", "off")
        timelimit: Time limit ("d" for day, "w" for week, "m" for month)

    Returns:
        Formatted news results with title, source, date, URL, and excerpt
    """
    try:
        from ddgs import DDGS
    except ImportError:
        from openakita.tools._import_helper import import_or_hint
        return f"错误：{import_or_hint('ddgs')}"

    # 限制结果数量
    max_results = min(max(1, max_results), 20)

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
            return _format_news_results(results)
    except Exception as e:
        logger.error(f"News search failed: {e}")
        return f"新闻搜索失败: {str(e)}"


# 作为模块运行时启动服务器
if __name__ == "__main__":
    mcp.run()
