"""
Test case collection

Contains 300 test cases:
- qa/: 100 QA tests
  - basic.py: basic knowledge (30)
  - reasoning.py: reasoning logic (35)
  - multiturn.py: multi-turn conversation (35)
- tools/: 100 tool tests
  - shell_tests.py: shell commands (40)
  - file_tests.py: file operations (30)
  - api_tests.py: API calls (30)
- search/: 100 search tests
  - web_search.py: web search (40)
  - code_search.py: code search (30)
  - doc_search.py: document search (30)
"""

# Lazy imports to avoid circular dependencies
_test_modules = {
    "qa.basic": "qa/basic.py",
    "qa.reasoning": "qa/reasoning.py",
    "qa.multiturn": "qa/multiturn.py",
    "tools.shell": "tools/shell_tests.py",
    "tools.file": "tools/file_tests.py",
    "tools.api": "tools/api_tests.py",
    "tools.browser": "tools/browser_tests.py",
    "search.web": "search/web_search.py",
    "search.code": "search/code_search.py",
    "search.doc": "search/doc_search.py",
}


def load_all_tests():
    """Load all test cases."""
    from .qa.basic import get_tests as qa_basic
    from .qa.multiturn import get_tests as qa_multiturn
    from .qa.reasoning import get_tests as qa_reasoning
    from .search.code_search import get_tests as search_code
    from .search.doc_search import get_tests as search_doc
    from .search.web_search import get_tests as search_web
    from .tools.api_tests import get_tests as tools_api
    from .tools.browser_tests import get_tests as tools_browser
    from .tools.file_tests import get_tests as tools_file
    from .tools.shell_tests import get_tests as tools_shell

    all_tests = []

    # QA tests (100)
    all_tests.extend(qa_basic())
    all_tests.extend(qa_reasoning())
    all_tests.extend(qa_multiturn())

    # Tool tests (100)
    all_tests.extend(tools_shell())
    all_tests.extend(tools_file())
    all_tests.extend(tools_api())
    all_tests.extend(tools_browser())

    # Search tests (100)
    all_tests.extend(search_web())
    all_tests.extend(search_code())
    all_tests.extend(search_doc())

    return all_tests


def load_tests_by_category(category: str):
    """Load test cases by category."""
    if category == "qa":
        from .qa.basic import get_tests as qa_basic
        from .qa.multiturn import get_tests as qa_multiturn
        from .qa.reasoning import get_tests as qa_reasoning

        return qa_basic() + qa_reasoning() + qa_multiturn()

    elif category == "tools":
        from .tools.api_tests import get_tests as tools_api
        from .tools.browser_tests import get_tests as tools_browser
        from .tools.file_tests import get_tests as tools_file
        from .tools.shell_tests import get_tests as tools_shell

        return tools_shell() + tools_file() + tools_api() + tools_browser()

    elif category == "search":
        from .search.code_search import get_tests as search_code
        from .search.doc_search import get_tests as search_doc
        from .search.web_search import get_tests as search_web

        return search_web() + search_code() + search_doc()

    return []


def get_test_count():
    """Get total test case count."""
    tests = load_all_tests()
    return len(tests)


def get_category_counts():
    """Get test counts per category."""
    return {
        "qa": len(load_tests_by_category("qa")),
        "tools": len(load_tests_by_category("tools")),
        "search": len(load_tests_by_category("search")),
    }
