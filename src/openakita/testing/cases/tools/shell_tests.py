"""
Shell 工具测试用例 (40个)
"""

from openakita.testing.runner import TestCase

SHELL_TESTS = [
    # 基础命令
    TestCase(
        id="tool_shell_001",
        category="tools",
        subcategory="shell",
        description="echo 命令",
        input={"command": "echo hello"},
        expected="hello",
        tags=["shell", "basic"],
    ),
    TestCase(
        id="tool_shell_002",
        category="tools",
        subcategory="shell",
        description="pwd 命令",
        input={"command": "pwd"},
        expected="length>=1",
        tags=["shell", "basic"],
    ),
    TestCase(
        id="tool_shell_003",
        category="tools",
        subcategory="shell",
        description="ls 命令",
        input={"command": "ls"},
        expected="length>=0",
        tags=["shell", "basic"],
    ),
    TestCase(
        id="tool_shell_004",
        category="tools",
        subcategory="shell",
        description="date 命令",
        input={"command": "date"},
        expected="length>=10",
        tags=["shell", "basic"],
    ),
    TestCase(
        id="tool_shell_005",
        category="tools",
        subcategory="shell",
        description="whoami 命令",
        input={"command": "whoami"},
        expected="length>=1",
        tags=["shell", "basic"],
    ),
    # 文件操作命令
    TestCase(
        id="tool_shell_010",
        category="tools",
        subcategory="shell",
        description="创建临时文件",
        input={"command": "touch /tmp/test_openakita.txt && echo success"},
        expected="success",
        tags=["shell", "file"],
    ),
    TestCase(
        id="tool_shell_011",
        category="tools",
        subcategory="shell",
        description="写入文件",
        input={
            "command": "echo 'test content' > /tmp/test_openakita.txt && cat /tmp/test_openakita.txt"
        },
        expected="contains:test content",
        tags=["shell", "file"],
    ),
    TestCase(
        id="tool_shell_012",
        category="tools",
        subcategory="shell",
        description="追加文件",
        input={
            "command": "echo 'appended' >> /tmp/test_openakita.txt && tail -1 /tmp/test_openakita.txt"
        },
        expected="contains:appended",
        tags=["shell", "file"],
    ),
    # Python 命令
    TestCase(
        id="tool_shell_020",
        category="tools",
        subcategory="shell",
        description="Python 版本",
        input={"command": "python --version"},
        expected="contains:Python",
        tags=["shell", "python"],
    ),
    TestCase(
        id="tool_shell_021",
        category="tools",
        subcategory="shell",
        description="Python 计算",
        input={"command": 'python -c "print(2 + 2)"'},
        expected="4",
        tags=["shell", "python"],
    ),
    TestCase(
        id="tool_shell_022",
        category="tools",
        subcategory="shell",
        description="Python pip list",
        input={"command": "pip list | head -5"},
        expected="length>=10",
        tags=["shell", "python", "pip"],
    ),
    # Git 命令
    TestCase(
        id="tool_shell_030",
        category="tools",
        subcategory="shell",
        description="Git 版本",
        input={"command": "git --version"},
        expected="contains:git version",
        tags=["shell", "git"],
    ),
    # 网络命令
    TestCase(
        id="tool_shell_040",
        category="tools",
        subcategory="shell",
        description="curl 测试",
        input={"command": "curl -s -o /dev/null -w '%{http_code}' https://httpbin.org/status/200"},
        expected="200",
        tags=["shell", "network"],
        timeout=10,
    ),
]


def get_tests() -> list[TestCase]:
    return SHELL_TESTS
