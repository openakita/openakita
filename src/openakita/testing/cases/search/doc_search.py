"""
文档搜索测试用例 (30个)
"""

from openakita.testing.runner import TestCase

DOC_SEARCH_TESTS = [
    # 项目文档搜索
    TestCase(
        id="search_doc_001",
        category="search",
        subcategory="doc",
        description="搜索 README",
        input={
            "action": "search_doc",
            "query": "OpenAkita",
            "file": "README.md",
        },
        expected="contains:自进化",
        tags=["doc", "readme"],
    ),
    TestCase(
        id="search_doc_002",
        category="search",
        subcategory="doc",
        description="搜索 AGENT.md",
        input={
            "action": "search_doc",
            "query": "Ralph",
            "file": "identity/AGENT.md",
        },
        expected="contains:Wiggum",
        tags=["doc", "agent"],
    ),
    TestCase(
        id="search_doc_003",
        category="search",
        subcategory="doc",
        description="搜索 SOUL.md",
        input={
            "action": "search_doc",
            "query": "诚实",
            "file": "identity/SOUL.md",
        },
        expected="length>=10",
        tags=["doc", "soul"],
    ),
    
    # 规格文档搜索
    TestCase(
        id="search_spec_001",
        category="search",
        subcategory="spec",
        description="搜索技能规格",
        input={
            "action": "search_doc",
            "query": "BaseSkill",
            "path": "specs/",
        },
        expected="length>=1",
        tags=["spec", "skill"],
    ),
    TestCase(
        id="search_spec_002",
        category="search",
        subcategory="spec",
        description="搜索工具规格",
        input={
            "action": "search_doc",
            "query": "ShellTool",
            "path": "specs/",
        },
        expected="length>=1",
        tags=["spec", "tool"],
    ),
    
    # 代码注释搜索
    TestCase(
        id="search_docstring_001",
        category="search",
        subcategory="docstring",
        description="搜索函数文档",
        input={
            "action": "search_docstring",
            "query": "执行",
            "path": "src/openakita",
        },
        expected="length>=1",
        tags=["docstring", "function"],
    ),
    TestCase(
        id="search_docstring_002",
        category="search",
        subcategory="docstring",
        description="搜索类文档",
        input={
            "action": "search_docstring",
            "query": "Agent",
            "path": "src/openakita",
        },
        expected="length>=1",
        tags=["docstring", "class"],
    ),
]

def get_tests() -> list[TestCase]:
    return DOC_SEARCH_TESTS
