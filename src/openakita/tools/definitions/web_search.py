"""
Web Search tool definitions

Includes web-search-related tools:
- web_search: Search the web (supports DuckDuckGo / Brave / Tavily / Exa)
- news_search: Search news

Search provider is configured via SEARCH_PROVIDER, defaulting to auto (automatically selected).
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
                "relation": "Switch to browser_navigate when you need to open a page for full content or screenshots",
            },
            {"name": "news_search", "relation": "Switch to news_search when searching specifically for news"},
        ],
        "detail": """Search the web (via the configured search provider: DuckDuckGo / Brave / Tavily / Exa).

**Use cases**:
- Find up-to-date information
- Verify facts
- Look up documentation
- Answer questions that require recent knowledge

**Parameters**:
- query: Search keywords
- max_results: Maximum number of results (1-20, default 5)
- region: Region code (default wt-wt for global, cn-zh for China)
- safesearch: Safe search level (on/moderate/off)

**Examples**:
- Search for information: web_search(query="Python asyncio tutorial", max_results=5)
- Search Chinese content: web_search(query="weather forecast", region="cn-zh")""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (1-20, default 5)",
                    "default": 5,
                },
                "region": {
                    "type": "string",
                    "description": "Region code (default wt-wt for global, cn-zh for China)",
                    "default": "wt-wt",
                },
                "safesearch": {
                    "type": "string",
                    "description": "Safe search level (on/moderate/off)",
                    "default": "moderate",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "news_search",
        "category": "Web Search",
        "description": "Search news using the configured search provider (DuckDuckGo by default; or Brave / Tavily / Exa when API keys are set). Use when you need to find recent news articles, current events, or breaking news. Returns titles, sources, dates, URLs, and excerpts.",
        "detail": """Search news (via the configured search provider).

**Use cases**:
- Find the latest news
- Stay updated on current events
- Get industry updates

**Parameters**:
- query: Search keywords
- max_results: Maximum number of results (1-20, default 5)
- region: Region code
- safesearch: Safe search level
- timelimit: Time range (d=one day, w=one week, m=one month)

**Examples**:
- Search news: news_search(query="AI latest developments", max_results=5)
- Search today's news: news_search(query="technology", timelimit="d")""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (1-20, default 5)",
                    "default": 5,
                },
                "region": {
                    "type": "string",
                    "description": "Region code (default wt-wt for global)",
                    "default": "wt-wt",
                },
                "safesearch": {
                    "type": "string",
                    "description": "Safe search level (on/moderate/off)",
                    "default": "moderate",
                },
                "timelimit": {
                    "type": "string",
                    "description": "Time range (d=one day, w=one week, m=one month, default unlimited)",
                },
            },
            "required": ["query"],
        },
    },
]
