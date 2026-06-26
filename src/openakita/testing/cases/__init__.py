"""
测试用例集合

包含 300 个测试用例：
- qa/: 100 个问答测试
  - basic.py: 基础知识 (30个)
  - reasoning.py: 推理逻辑 (35个)
  - multiturn.py: 多轮对话 (35个)
- tools/: 100 个工具测试
  - shell_tests.py: Shell 命令 (40个)
  - file_tests.py: 文件操作 (30个)
  - api_tests.py: API 调用 (30个)
- search/: 100 个搜索测试
  - web_search.py: 网络搜索 (40个)
  - code_search.py: 代码搜索 (30个)
  - doc_search.py: 文档搜索 (30个)
"""

# 延迟导入以避免循环依赖
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
    """加载所有测试用例"""
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

    # QA 测试 (100)
    all_tests.extend(qa_basic())
    all_tests.extend(qa_reasoning())
    all_tests.extend(qa_multiturn())

    # 工具测试 (100)
    all_tests.extend(tools_shell())
    all_tests.extend(tools_file())
    all_tests.extend(tools_api())
    all_tests.extend(tools_browser())

    # 搜索测试 (100)
    all_tests.extend(search_web())
    all_tests.extend(search_code())
    all_tests.extend(search_doc())

    return all_tests


def load_tests_by_category(category: str):
    """按类别加载测试用例"""
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
    """获取测试用例总数"""
    tests = load_all_tests()
    return len(tests)


def get_category_counts():
    """获取各类别测试数量"""
    return {
        "qa": len(load_tests_by_category("qa")),
        "tools": len(load_tests_by_category("tools")),
        "search": len(load_tests_by_category("search")),
    }
