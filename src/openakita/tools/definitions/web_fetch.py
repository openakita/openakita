"""
Web Fetch 工具定义

轻量 URL 内容获取，对齐 Cursor 的 WebFetch 工具。
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
            {"name": "web_search", "relation": "没有具体 URL 时用 web_search 搜索"},
            {"name": "browser_navigate", "relation": "需要与页面交互时用 browser"},
        ],
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "完整 URL（必须包含 https:// 等协议前缀）",
                },
                "max_length": {
                    "type": "integer",
                    "description": "最大返回字符数，默认 15000",
                    "default": 15000,
                },
            },
            "required": ["url"],
        },
    },
]
