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
            {
                "name": "browser_navigate",
                "relation": "需要打开网页查看完整内容或截图时改用 browser_navigate",
            },
            {"name": "news_search", "relation": "专门搜索新闻时改用 news_search"},
        ],
        "detail": """通过当前激活的搜索源进行网页搜索。

**搜索源（Provider）**：
- 用户在「配置 → 工具与技能 → 搜索源」面板配置激活源（博查/Tavily/SearXNG/Jina/DuckDuckGo）
- 留空时按优先级自动检测可用源（bocha → tavily → searxng → jina → duckduckgo）
- DuckDuckGo 在国内常无法访问，请用户配置博查/Tavily 等替代源

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
- provider: 显式指定搜索源 ID（可选；不传则按用户配置/auto-detect）
- timeout_seconds: 单次搜索等待上限，0 表示不限；超时只跳过本次源，不代表任务失败

**失败时**：如果工具返回 "[搜索源未配置] ..." 或 "[搜索 API Key 无效] ..." 等
[xxx] 开头的提示，请直接告诉用户去 OpenAkita 桌面端「配置 → 工具与技能 → 搜索源」
配置/更新 Key，并停止重试同一查询。""",
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
                "provider": {
                    "type": "string",
                    "description": "可选：显式指定搜索源 ID（bocha/tavily/searxng/jina/duckduckgo）；不传则按配置/auto-detect",
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "单次外部搜索等待上限（秒），0=不限；超时后请换源或基于已获取信息继续",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "news_search",
        "category": "Web Search",
        "description": (
            "Search news through the active web_search provider. Use when you need to find "
            "recent news articles, current events, or breaking news. Returns titles, sources, "
            "dates, URLs, and excerpts. Note: not all providers expose a dedicated news endpoint; "
            "providers that don't will be skipped during auto-detect."
        ),
        "detail": """通过当前激活的搜索源搜索新闻。

**搜索源**：
- 不是所有 provider 都支持独立的新闻接口；不支持的会被自动跳过
- 当前内置源中：DuckDuckGo 支持 news；博查/Tavily/SearXNG/Jina 当前不暴露独立 news 端点
- 国内场景下若 DuckDuckGo 不可达，news_search 可能没有可用源；建议改用 web_search + 关键词限定

**适用场景**：
- 查找最新新闻
- 了解时事动态
- 获取行业资讯

**参数说明**：
- query: 搜索关键词
- max_results: 最大结果数（1-20，默认 5）
- region: 地区代码
- safesearch: 安全搜索级别
- provider: 显式指定搜索源 ID（可选）
- timelimit: 时间范围（d=一天, w=一周, m=一月）
- timeout_seconds: 单次外部搜索等待上限，0 表示不限；超时只跳过本次搜索源，不代表任务失败

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
                "provider": {
                    "type": "string",
                    "description": "可选：显式指定搜索源 ID；不传则按配置/auto-detect",
                },
                "timelimit": {
                    "type": "string",
                    "description": "时间范围（d=一天, w=一周, m=一月，默认不限）",
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "单次外部搜索等待上限（秒），0=不限；超时后请换源或基于已获取信息继续",
                },
            },
            "required": ["query"],
        },
    },
]
