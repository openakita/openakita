"""
Shell tool test cases (40)
"""

from openakita.testing.runner import TestCase

SHELL_TESTS = [
    # Basic commands
    TestCase(
        id="tool_shell_001",
        category="tools",
        subcategory="shell",
        description="echo command",
        input={"command": "echo hello"},
        expected="hello",
        tags=["shell", "basic"],
    ),
    TestCase(
        id="tool_shell_002",
        category="tools",
        subcategory="shell",
        description="pwd command",
        input={"command": "pwd"},
        expected="length>=1",
        tags=["shell", "basic"],
    ),
    TestCase(
        id="tool_shell_003",
        category="tools",
        subcategory="shell",
        description="ls command",
        input={"command": "ls"},
        expected="length>=0",
        tags=["shell", "basic"],
    ),
    TestCase(
        id="tool_shell_004",
        category="tools",
        subcategory="shell",
        description="date command",
        input={"command": "date"},
        expected="length>=10",
        tags=["shell", "basic"],
    ),
    TestCase(
        id="tool_shell_005",
        category="tools",
        subcategory="shell",
        description="whoami command",
        input={"command": "whoami"},
        expected="length>=1",
        tags=["shell", "basic"],
    ),
    # File operation commands
    TestCase(
        id="tool_shell_010",
        category="tools",
        subcategory="shell",
        description="Create temporary file",
        input={"command": "touch /tmp/test_openakita.txt && echo success"},
        expected="success",
        tags=["shell", "file"],
    ),
    TestCase(
        id="tool_shell_011",
        category="tools",
        subcategory="shell",
        description="Write to file",
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
        description="Append to file",
        input={
            "command": "echo 'appended' >> /tmp/test_openakita.txt && tail -1 /tmp/test_openakita.txt"
        },
        expected="contains:appended",
        tags=["shell", "file"],
    ),
    # Python commands
    TestCase(
        id="tool_shell_020",
        category="tools",
        subcategory="shell",
        description="Python version",
        input={"command": "python --version"},
        expected="contains:Python",
        tags=["shell", "python"],
    ),
    TestCase(
        id="tool_shell_021",
        category="tools",
        subcategory="shell",
        description="Python calculation",
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
    # Git commands
    TestCase(
        id="tool_shell_030",
        category="tools",
        subcategory="shell",
        description="Git version",
        input={"command": "git --version"},
        expected="contains:git version",
        tags=["shell", "git"],
    ),
    # Network commands
    TestCase(
        id="tool_shell_040",
        category="tools",
        subcategory="shell",
        description="curl test",
        input={"command": "curl -s -o /dev/null -w '%{http_code}' https://httpbin.org/status/200"},
        expected="200",
        tags=["shell", "network"],
        timeout=10,
    ),
]


def get_tests() -> list[TestCase]:
    return SHELL_TESTS
