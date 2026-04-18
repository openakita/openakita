"""
Code search test cases (30)
"""

from openakita.testing.runner import TestCase

CODE_SEARCH_TESTS = [
    # Local code search
    TestCase(
        id="search_code_001",
        category="search",
        subcategory="code",
        description="Search for function definitions",
        input={
            "action": "search_code",
            "pattern": "def execute",
            "path": "src/openakita",
        },
        expected="length>=1",
        tags=["code", "search", "function"],
    ),
    TestCase(
        id="search_code_002",
        category="search",
        subcategory="code",
        description="Search for class definitions",
        input={
            "action": "search_code",
            "pattern": "class.*Skill",
            "path": "src/openakita",
        },
        expected="length>=1",
        tags=["code", "search", "class"],
    ),
    TestCase(
        id="search_code_003",
        category="search",
        subcategory="code",
        description="Search for import statements",
        input={
            "action": "search_code",
            "pattern": "^import|^from.*import",
            "path": "src/openakita",
        },
        expected="length>=5",
        tags=["code", "search", "import"],
    ),
    TestCase(
        id="search_code_004",
        category="search",
        subcategory="code",
        description="Search for TODO comments",
        input={
            "action": "search_code",
            "pattern": "TODO|FIXME",
            "path": "src/openakita",
        },
        expected="length>=0",
        tags=["code", "search", "todo"],
    ),
    TestCase(
        id="search_code_005",
        category="search",
        subcategory="code",
        description="Search by file type",
        input={
            "action": "search_code",
            "pattern": "async def",
            "path": "src/openakita",
            "file_pattern": "*.py",
        },
        expected="length>=5",
        tags=["code", "search", "async"],
    ),
    # File search
    TestCase(
        id="search_file_001",
        category="search",
        subcategory="file",
        description="Search for files by name",
        input={
            "action": "search_files",
            "pattern": "*.py",
            "path": "src/openakita",
        },
        expected="length>=10",
        tags=["file", "search", "glob"],
    ),
    TestCase(
        id="search_file_002",
        category="search",
        subcategory="file",
        description="Search for configuration files",
        input={
            "action": "search_files",
            "pattern": "*.toml",
            "path": ".",
        },
        expected="length>=1",
        tags=["file", "search", "config"],
    ),
    TestCase(
        id="search_file_003",
        category="search",
        subcategory="file",
        description="Search for Markdown files",
        input={
            "action": "search_files",
            "pattern": "*.md",
            "path": ".",
        },
        expected="length>=4",
        tags=["file", "search", "markdown"],
    ),
    # Semantic search (reserved)
    TestCase(
        id="search_semantic_001",
        category="search",
        subcategory="semantic",
        description="Semantic search for functions",
        input={
            "action": "semantic_search",
            "query": "function that executes shell commands",
            "path": "src/openakita",
        },
        expected="contains:shell",
        tags=["semantic", "search"],
    ),
    TestCase(
        id="search_semantic_002",
        category="search",
        subcategory="semantic",
        description="Semantic search for classes",
        input={
            "action": "semantic_search",
            "query": "class that manages skill registration",
            "path": "src/openakita",
        },
        expected="contains:Registry",
        tags=["semantic", "search"],
    ),
]


def get_tests() -> list[TestCase]:
    return CODE_SEARCH_TESTS
