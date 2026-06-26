"""
代码搜索测试用例 (30个)
"""

from openakita.testing.runner import TestCase

CODE_SEARCH_TESTS = [
    # 本地代码搜索
    TestCase(
        id="search_code_001",
        category="search",
        subcategory="code",
        description="搜索函数定义",
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
        description="搜索类定义",
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
        description="搜索导入语句",
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
        description="搜索 TODO 注释",
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
        description="按文件类型搜索",
        input={
            "action": "search_code",
            "pattern": "async def",
            "path": "src/openakita",
            "file_pattern": "*.py",
        },
        expected="length>=5",
        tags=["code", "search", "async"],
    ),
    # 文件搜索
    TestCase(
        id="search_file_001",
        category="search",
        subcategory="file",
        description="按名称搜索文件",
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
        description="搜索配置文件",
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
        description="搜索 Markdown 文件",
        input={
            "action": "search_files",
            "pattern": "*.md",
            "path": ".",
        },
        expected="length>=4",
        tags=["file", "search", "markdown"],
    ),
    # 语义搜索（预留）
    TestCase(
        id="search_semantic_001",
        category="search",
        subcategory="semantic",
        description="语义搜索函数",
        input={
            "action": "semantic_search",
            "query": "执行shell命令的函数",
            "path": "src/openakita",
        },
        expected="contains:shell",
        tags=["semantic", "search"],
    ),
    TestCase(
        id="search_semantic_002",
        category="search",
        subcategory="semantic",
        description="语义搜索类",
        input={
            "action": "semantic_search",
            "query": "管理技能注册的类",
            "path": "src/openakita",
        },
        expected="contains:Registry",
        tags=["semantic", "search"],
    ),
]


def get_tests() -> list[TestCase]:
    return CODE_SEARCH_TESTS
