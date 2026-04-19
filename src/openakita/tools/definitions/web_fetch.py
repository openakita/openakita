"""
Web Fetch tool definition

Lightweight URL content fetching, aligned with Cursor's WebFetch tool.
"""

WEB_FETCH_TOOLS = [
    {
        "name": "web_fetch",
        "category": "Web",
        "description": (
            "Fetch content from a URL and return it in readable markdown format. "
            "Use when you need to read a webpage, API doc, blog post, or any public URL "
            "content WITHOUT launching a browser.\n\n"
            "IMPORTANT:\n"
            "- Much faster and cheaper than browser_open → browser_navigate → browser_get_content\n"
            "- Use this for reading content; use browser tools only when you need to INTERACT "
            "with a page (click, fill forms, take screenshots)\n"
            "- Does not support authentication, binary content (media/PDFs), or localhost URLs\n"
            "- Returns markdown-formatted text extracted from the page\n\n"
            "When to use web_fetch vs browser vs web_search:\n"
            "- web_fetch: Read a specific URL's content (documentation, articles, API responses)\n"
            "- web_search: Find information when you don't have a specific URL\n"
            "- browser: Interactive tasks (login, form filling, clicking, screenshots)"
        ),
        "related_tools": [
            {"name": "web_search", "relation": "Use web_search when you don't have a specific URL"},
            {"name": "browser_navigate", "relation": "Use browser when you need to interact with a page"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full URL (must include a protocol prefix such as https://)",
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum number of characters to return, default 15000",
                    "default": 15000,
                },
            },
            "required": ["url"],
        },
    },
]
