"""
Web Search 工具定义

包含网络搜索相关的工具：
- web_search: 搜索网页
- news_search: 搜索新闻
"""

WEB_SEARCH_TOOLS = [
    {
        "name": "web_search",
        "category": "Web Search",
        "description": (
            "Search the web for real-time information. Returns titles, URLs, and snippets.\n\n"
            "Use when you need:\n"
            "- Up-to-date information not in your training data\n"
            "- Current documentation for libraries/frameworks\n"
            "- News, events, or technology updates\n"
            "- Verification of facts\n\n"
            "IMPORTANT — Use the correct year in search queries:\n"
            "- You MUST use the current year when searching for recent information, "
            "e.g., 'React documentation 2026' not 'React documentation 2025'\n\n"
            "When to use web_search vs web_fetch vs browser:\n"
            "- web_search: Find information when you don't have a specific URL\n"
            "- web_fetch: Read content from a known URL (docs, articles)\n"
            "- browser: Interactive web tasks (login, form filling, screenshots)"
        ),
        "related_tools": [
            {"name": "browser_navigate", "relation": "需要打开网页查看完整内容或截图时改用 browser_navigate"},
            {"name": "news_search", "relation": "专门搜索新闻时改用 news_search"},
        ],
        "detail": """使用 DuckDuckGo 搜索网页。

**适用场景**：
- 查找最新信息
- 验证事实
- 查阅文档
- 回答需要最新知识的问题

**参数说明**：
- query: 搜索关键词
- max_results: 最大结果数（1-20，默认 5）
- region: 地区代码（默认 wt-wt 全球，cn-zh 中国）
- safesearch: 安全搜索级别（on/moderate/off）

**示例**：
- 搜索信息：web_search(query="Python asyncio 教程", max_results=5)
- 搜索中文内容：web_search(query="天气预报", region="cn-zh")""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数（1-20，默认 5）",
                    "default": 5,
                },
                "region": {
                    "type": "string",
                    "description": "地区代码（默认 wt-wt 全球，cn-zh 中国）",
                    "default": "wt-wt",
                },
                "safesearch": {
                    "type": "string",
                    "description": "安全搜索级别（on/moderate/off）",
                    "default": "moderate",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "news_search",
        "category": "Web Search",
        "description": "Search news using DuckDuckGo. Use when you need to find recent news articles, current events, or breaking news. Returns titles, sources, dates, URLs, and excerpts.",
        "detail": """使用 DuckDuckGo 搜索新闻。

**适用场景**：
- 查找最新新闻
- 了解时事动态
- 获取行业资讯

**参数说明**：
- query: 搜索关键词
- max_results: 最大结果数（1-20，默认 5）
- region: 地区代码
- safesearch: 安全搜索级别
- timelimit: 时间范围（d=一天, w=一周, m=一月）

**示例**：
- 搜索新闻：news_search(query="AI 最新进展", max_results=5)
- 搜索今日新闻：news_search(query="科技", timelimit="d")""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数（1-20，默认 5）",
                    "default": 5,
                },
                "region": {
                    "type": "string",
                    "description": "地区代码（默认 wt-wt 全球）",
                    "default": "wt-wt",
                },
                "safesearch": {
                    "type": "string",
                    "description": "安全搜索级别（on/moderate/off）",
                    "default": "moderate",
                },
                "timelimit": {
                    "type": "string",
                    "description": "时间范围（d=一天, w=一周, m=一月，默认不限）",
                },
            },
            "required": ["query"],
        },
    },
]
