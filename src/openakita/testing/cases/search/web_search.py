"""
网络搜索测试用例 (40个)
"""

from openakita.testing.runner import TestCase

WEB_SEARCH_TESTS = [
    # HTTP 请求测试
    TestCase(
        id="search_http_001",
        category="search",
        subcategory="http",
        description="GET 请求测试",
        input={
            "action": "get",
            "url": "https://httpbin.org/get",
        },
        expected="contains:origin",
        tags=["http", "get"],
        timeout=15,
    ),
    TestCase(
        id="search_http_002",
        category="search",
        subcategory="http",
        description="POST 请求测试",
        input={
            "action": "post",
            "url": "https://httpbin.org/post",
            "data": {"test": "value"},
        },
        expected="contains:test",
        tags=["http", "post"],
        timeout=15,
    ),
    TestCase(
        id="search_http_003",
        category="search",
        subcategory="http",
        description="HTTP 头测试",
        input={
            "action": "get",
            "url": "https://httpbin.org/headers",
            "headers": {"X-Test": "openakita"},
        },
        expected="contains:X-Test",
        tags=["http", "headers"],
        timeout=15,
    ),
    TestCase(
        id="search_http_004",
        category="search",
        subcategory="http",
        description="JSON 响应解析",
        input={
            "action": "get",
            "url": "https://httpbin.org/json",
        },
        expected="contains:slideshow",
        tags=["http", "json"],
        timeout=15,
    ),
    # GitHub 搜索测试
    TestCase(
        id="search_github_001",
        category="search",
        subcategory="github",
        description="搜索 Python 仓库",
        input={
            "action": "github_search",
            "query": "python web framework",
            "limit": 5,
        },
        expected="length>=1",
        tags=["github", "search"],
        timeout=30,
    ),
    TestCase(
        id="search_github_002",
        category="search",
        subcategory="github",
        description="搜索 AI 工具",
        input={
            "action": "github_search",
            "query": "AI agent",
            "limit": 5,
        },
        expected="length>=1",
        tags=["github", "search"],
        timeout=30,
    ),
    # 下载测试
    TestCase(
        id="search_download_001",
        category="search",
        subcategory="download",
        description="下载小文件",
        input={
            "action": "download",
            "url": "https://httpbin.org/bytes/100",
            "path": "/tmp/openakita_download_test.bin",
        },
        expected=True,
        tags=["download", "file"],
        timeout=30,
    ),
]


def get_tests() -> list[TestCase]:
    return WEB_SEARCH_TESTS
