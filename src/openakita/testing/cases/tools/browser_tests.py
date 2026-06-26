"""
浏览器工具测试用例 (预留，需要 Playwright 支持)
"""

from openakita.testing.runner import TestCase

BROWSER_TESTS = [
    # 页面导航
    TestCase(
        id="tool_browser_001",
        category="tools",
        subcategory="browser",
        description="导航到页面",
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
        description="获取页面标题",
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
        description="获取页面文本",
        input={
            "action": "get_text",
            "url": "https://example.com",
        },
        expected="length>=50",
        tags=["browser", "text"],
        timeout=30,
    ),
    # 元素交互（预留）
    TestCase(
        id="tool_browser_010",
        category="tools",
        subcategory="browser",
        description="查找元素",
        input={
            "action": "find_element",
            "url": "https://example.com",
            "selector": "h1",
        },
        expected="contains:Example",
        tags=["browser", "element"],
        timeout=30,
    ),
    # 截图（预留）
    TestCase(
        id="tool_browser_020",
        category="tools",
        subcategory="browser",
        description="页面截图",
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
