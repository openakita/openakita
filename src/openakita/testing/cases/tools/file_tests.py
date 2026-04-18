"""
File tool test cases (30)
"""

from openakita.testing.runner import TestCase

FILE_TESTS = [
    # Read/write tests
    TestCase(
        id="tool_file_001",
        category="tools",
        subcategory="file",
        description="Write and read a file",
        input={
            "action": "write_read",
            "path": "/tmp/openakita_test_001.txt",
            "content": "Hello, OpenAkita!",
        },
        expected="Hello, OpenAkita!",
        tags=["file", "read", "write"],
    ),
    TestCase(
        id="tool_file_002",
        category="tools",
        subcategory="file",
        description="Append content",
        input={
            "action": "append_read",
            "path": "/tmp/openakita_test_002.txt",
            "initial": "Line 1\n",
            "append": "Line 2\n",
        },
        expected="contains:Line 2",
        tags=["file", "append"],
    ),
    TestCase(
        id="tool_file_003",
        category="tools",
        subcategory="file",
        description="Check file existence",
        input={
            "action": "exists",
            "path": "/tmp/openakita_test_001.txt",
        },
        expected=True,
        tags=["file", "exists"],
    ),
    TestCase(
        id="tool_file_004",
        category="tools",
        subcategory="file",
        description="List directory",
        input={
            "action": "list_dir",
            "path": "/tmp",
        },
        expected="length>=1",
        tags=["file", "list"],
    ),
    TestCase(
        id="tool_file_005",
        category="tools",
        subcategory="file",
        description="Create directory",
        input={
            "action": "mkdir",
            "path": "/tmp/openakita_test_dir",
        },
        expected=True,
        tags=["file", "mkdir"],
    ),
    TestCase(
        id="tool_file_006",
        category="tools",
        subcategory="file",
        description="Copy file",
        input={
            "action": "copy",
            "src": "/tmp/openakita_test_001.txt",
            "dst": "/tmp/openakita_test_001_copy.txt",
        },
        expected=True,
        tags=["file", "copy"],
    ),
    TestCase(
        id="tool_file_007",
        category="tools",
        subcategory="file",
        description="Search files",
        input={
            "action": "search",
            "path": "/tmp",
            "pattern": "openakita_test*.txt",
        },
        expected="length>=1",
        tags=["file", "search"],
    ),
    TestCase(
        id="tool_file_008",
        category="tools",
        subcategory="file",
        description="Read partial content of a large file",
        input={
            "action": "read_lines",
            "path": "/tmp/openakita_test_001.txt",
            "start": 0,
            "end": 10,
        },
        expected="length>=1",
        tags=["file", "read", "partial"],
    ),
    TestCase(
        id="tool_file_009",
        category="tools",
        subcategory="file",
        description="Get file info",
        input={
            "action": "stat",
            "path": "/tmp/openakita_test_001.txt",
        },
        expected="contains:size",
        tags=["file", "stat"],
    ),
    TestCase(
        id="tool_file_010",
        category="tools",
        subcategory="file",
        description="Delete file",
        input={
            "action": "delete",
            "path": "/tmp/openakita_test_delete.txt",
        },
        expected=True,
        tags=["file", "delete"],
    ),
]


def get_tests() -> list[TestCase]:
    return FILE_TESTS
