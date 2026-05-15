"""
OpenAkita MCP 服务器模块

历史上曾内置 web_search MCP server（基于 DuckDuckGo），但与
``src/openakita/tools/handlers/web_search.py`` handler 路径冗余，导致同一能力
以两份不同实现暴露给 Agent。已下线该 MCP 镜像，统一走内置 handler →
``src/openakita/tools/web_search/`` Provider 注册表（支持博查/Tavily/SearXNG/
Jina/DuckDuckGo 多 provider 切换 + auto-detect fallback）。

本目录保留为占位包以便未来扩展其他内置 MCP 服务器。
"""

__all__: list[str] = []
