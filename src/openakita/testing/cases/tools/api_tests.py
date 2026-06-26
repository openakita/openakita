"""
API 操作测试用例 (30个)
"""

from openakita.testing.runner import TestCase

API_TESTS = [
    # REST API 测试
    TestCase(
        id="tool_api_001",
        category="tools",
        subcategory="api",
        description="GET 请求解析 JSON",
        input={
            "action": "api_call",
            "method": "GET",
            "url": "https://jsonplaceholder.typicode.com/posts/1",
        },
        expected="contains:userId",
        tags=["api", "rest", "get"],
        timeout=15,
    ),
    TestCase(
        id="tool_api_002",
        category="tools",
        subcategory="api",
        description="POST 请求创建资源",
        input={
            "action": "api_call",
            "method": "POST",
            "url": "https://jsonplaceholder.typicode.com/posts",
            "json": {"title": "test", "body": "content", "userId": 1},
        },
        expected="contains:id",
        tags=["api", "rest", "post"],
        timeout=15,
    ),
    TestCase(
        id="tool_api_003",
        category="tools",
        subcategory="api",
        description="获取用户列表",
        input={
            "action": "api_call",
            "method": "GET",
            "url": "https://jsonplaceholder.typicode.com/users",
        },
        expected="length>=100",
        tags=["api", "rest", "list"],
        timeout=15,
    ),
    TestCase(
        id="tool_api_004",
        category="tools",
        subcategory="api",
        description="带参数的 GET 请求",
        input={
            "action": "api_call",
            "method": "GET",
            "url": "https://jsonplaceholder.typicode.com/comments",
            "params": {"postId": 1},
        },
        expected="contains:email",
        tags=["api", "rest", "params"],
        timeout=15,
    ),
    TestCase(
        id="tool_api_005",
        category="tools",
        subcategory="api",
        description="PUT 请求更新资源",
        input={
            "action": "api_call",
            "method": "PUT",
            "url": "https://jsonplaceholder.typicode.com/posts/1",
            "json": {"id": 1, "title": "updated", "body": "new content", "userId": 1},
        },
        expected="contains:updated",
        tags=["api", "rest", "put"],
        timeout=15,
    ),
    # 状态码测试
    TestCase(
        id="tool_api_010",
        category="tools",
        subcategory="api",
        description="200 OK 响应",
        input={
            "action": "check_status",
            "url": "https://httpbin.org/status/200",
        },
        expected=200,
        tags=["api", "status"],
        timeout=10,
    ),
    TestCase(
        id="tool_api_011",
        category="tools",
        subcategory="api",
        description="404 Not Found",
        input={
            "action": "check_status",
            "url": "https://httpbin.org/status/404",
        },
        expected=404,
        tags=["api", "status", "error"],
        timeout=10,
    ),
    TestCase(
        id="tool_api_012",
        category="tools",
        subcategory="api",
        description="重定向处理",
        input={
            "action": "check_redirect",
            "url": "https://httpbin.org/redirect/1",
        },
        expected=True,
        tags=["api", "redirect"],
        timeout=10,
    ),
    # 请求头测试
    TestCase(
        id="tool_api_020",
        category="tools",
        subcategory="api",
        description="自定义 User-Agent",
        input={
            "action": "api_call",
            "method": "GET",
            "url": "https://httpbin.org/user-agent",
            "headers": {"User-Agent": "OpenAkita/1.0"},
        },
        expected="contains:OpenAkita",
        tags=["api", "headers"],
        timeout=10,
    ),
    TestCase(
        id="tool_api_021",
        category="tools",
        subcategory="api",
        description="Accept 头",
        input={
            "action": "api_call",
            "method": "GET",
            "url": "https://httpbin.org/headers",
            "headers": {"Accept": "application/json"},
        },
        expected="contains:Accept",
        tags=["api", "headers"],
        timeout=10,
    ),
]


def get_tests() -> list[TestCase]:
    return API_TESTS
