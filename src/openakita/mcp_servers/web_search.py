"""
Web Search MCP Server

DuckDuckGo-based web search service. No API key required.

Usage:
    python -m openakita.mcp_servers.web_search

Tools:
    - web_search: Search the web
    - news_search: Search news
"""

import logging
import traceback

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Create MCP server instance
mcp = FastMCP(
    name="web-search",
    instructions="""Web Search MCP Server - DuckDuckGo-based web search service.

Available tools:
- web_search: Search the web, returns title, link, and snippet
- news_search: Search news, returns latest news articles

Usage examples:
- Search for information: web_search(query="Python tutorial", max_results=5)
- Search for news: news_search(query="AI latest developments", max_results=5)
""",
)


def _format_web_results(results: list) -> str:
    """Format web search results"""
    if not results:
        return "No relevant results found"

    output = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url = r.get("href", r.get("link", ""))
        body = r.get("body", r.get("snippet", ""))
        output.append(f"**{i}. {title}**\n{url}\n{body}\n")

    return "\n".join(output)


def _format_news_results(results: list) -> str:
    """Format news search results"""
    if not results:
        return "No relevant news found"

    output = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
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

        return f"Error: {import_or_hint('ddgs')}"

    # Limit result count
    max_results = min(max(1, max_results), 20)

    try:
        with DDGS() as ddgs:
            results = list(
                ddgs.text(query, max_results=max_results, region=region, safesearch=safesearch)
            )
            return _format_web_results(results)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Web search failed: {type(e).__name__}: {e}\n{tb}")
        return f"Search failed: {type(e).__name__}: {e}"


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

        return f"Error: {import_or_hint('ddgs')}"

    # Limit result count
    max_results = min(max(1, max_results), 20)

    try:
        with DDGS() as ddgs:
            results = ddgs.news(
                query,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
            )
            return _format_news_results(results)
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"News search failed: {type(e).__name__}: {e}\n{tb}")
        return f"News search failed: {type(e).__name__}: {e}"


# Start the server when run as a module
if __name__ == "__main__":
    mcp.run()
