"""
Browser tool test cases (placeholder, requires Playwright support)
"""

from openakita.testing.runner import TestCase

BROWSER_TESTS = [
    # Page navigation
    TestCase(
        id="tool_browser_001",
        category="tools",
        subcategory="browser",
        description="Navigate to page",
        input={
            "action": "navigate",
            "url": "https://example.com",
        },
        expected="contains:Example Domain",
        tags=["browser", "navigate"],
        timeout=30,
    ),
    TestCase(
        id="tool_browser_002",
        category="tools",
        subcategory="browser",
        description="Get page title",
        input={
            "action": "get_title",
            "url": "https://example.com",
        },
        expected="contains:Example",
        tags=["browser", "title"],
        timeout=30,
    ),
    TestCase(
        id="tool_browser_003",
        category="tools",
        subcategory="browser",
        description="Get page text",
        input={
            "action": "get_text",
            "url": "https://example.com",
        },
        expected="length>=50",
        tags=["browser", "text"],
        timeout=30,
    ),
    # Element interaction (placeholder)
    TestCase(
        id="tool_browser_010",
        category="tools",
        subcategory="browser",
        description="Find element",
        input={
            "action": "find_element",
            "url": "https://example.com",
            "selector": "h1",
        },
        expected="contains:Example",
        tags=["browser", "element"],
        timeout=30,
    ),
    # Screenshot (placeholder)
    TestCase(
        id="tool_browser_020",
        category="tools",
        subcategory="browser",
        description="Take page screenshot",
        input={
            "action": "screenshot",
            "url": "https://example.com",
            "path": "/tmp/screenshot.png",
        },
        expected=True,
        tags=["browser", "screenshot"],
        timeout=30,
    ),
]


def get_tests() -> list[TestCase]:
    return BROWSER_TESTS
