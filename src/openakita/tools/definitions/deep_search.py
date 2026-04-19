"""
Deep Search tool definitions.

Includes:
- deep_search: Multi-provider deep research (Tavily + Exa) returning hundreds of sources
"""

DEEP_SEARCH_TOOLS = [
    {
        "name": "deep_search",
        "category": "Web Search",
        "description": (
            "Deep research search that returns hundreds of unique sources using "
            "Tavily and Exa APIs in parallel. Generates diverse sub-queries, "
            "fans out across providers, deduplicates by URL, and ranks by relevance.\n\n"
            "Use when you need:\n"
            "- Comprehensive research on a topic (100-500+ sources)\n"
            "- Deep market analysis with many references\n"
            "- Academic-style literature surveys\n"
            "- Competitive intelligence gathering\n\n"
            "This is significantly slower than web_search but returns far more results. "
            "Use web_search for quick lookups (5-20 results), deep_search for thorough research."
        ),
        "related_tools": [
            {
                "name": "web_search",
                "relation": "Use web_search for quick lookups (5-20 results); deep_search for hundreds",
            },
        ],
        "detail": """Deep research search via Tavily + Exa with parallel multi-query fan-out.

**Use cases**:
- Comprehensive topic research needing 100-500+ sources
- Deep market/competitive analysis
- Academic literature surveys
- Any task requiring exhaustive source coverage

**Parameters**:
- query: Research topic or question
- max_sources: Target unique sources (50-500, default 100)
- providers: List of providers to use (default: ["tavily", "exa"])
- include_content: Fetch full content snippets (slower, default false)
- max_display: Max sources to show in output (0=all, default 50)

**Examples**:
- Basic: deep_search(query="transformer architecture NLP", max_sources=100)
- Thorough: deep_search(query="quantum computing patents 2026", max_sources=400, include_content=true)
- Single provider: deep_search(query="AI safety research", providers=["exa"])""",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Research topic or question to deeply investigate",
                },
                "max_sources": {
                    "type": "integer",
                    "description": "Target number of unique sources to return (50-500, default 100)",
                    "default": 100,
                },
                "providers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Providers to use (default: ["tavily", "exa"]). Options: tavily, exa',
                },
                "include_content": {
                    "type": "boolean",
                    "description": "Fetch full content for each source (slower, default false)",
                    "default": False,
                },
                "max_display": {
                    "type": "integer",
                    "description": "Max sources to show in formatted output (0=all, default 50)",
                    "default": 50,
                },
            },
            "required": ["query"],
        },
    },
]
