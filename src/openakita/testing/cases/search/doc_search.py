"""
Document search test cases
"""

from openakita.testing.runner import TestCase

DOC_SEARCH_TESTS = [
    # Project document search
    TestCase(
        id="search_doc_001",
        category="search",
        subcategory="doc",
        description="Search README",
        input={
            "action": "search_doc",
            "query": "OpenAkita",
            "file": "README.md",
        },
        expected="contains:self-evolving",
        tags=["doc", "readme"],
    ),
    TestCase(
        id="search_doc_002",
        category="search",
        subcategory="doc",
        description="Search AGENT.md",
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
        description="Search SOUL.md",
        input={
            "action": "search_doc",
            "query": "honesty",
            "file": "identity/SOUL.md",
        },
        expected="length>=10",
        tags=["doc", "soul"],
    ),
    # Specification document search
    TestCase(
        id="search_spec_001",
        category="search",
        subcategory="spec",
        description="Search skill specifications",
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
        description="Search tool specifications",
        input={
            "action": "search_doc",
            "query": "ShellTool",
            "path": "specs/",
        },
        expected="length>=1",
        tags=["spec", "tool"],
    ),
    # Docstring search
    TestCase(
        id="search_docstring_001",
        category="search",
        subcategory="docstring",
        description="Search function docstrings",
        input={
            "action": "search_docstring",
            "query": "execute",
            "path": "src/openakita",
        },
        expected="length>=1",
        tags=["docstring", "function"],
    ),
    TestCase(
        id="search_docstring_002",
        category="search",
        subcategory="docstring",
        description="Search class docstrings",
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
