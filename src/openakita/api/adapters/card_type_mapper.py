"""CardTypeMapper: tool_name → card_type 映射。

根据工具名自动推断步骤卡片的展示类型（图标 / 颜色），无需 LLM。
"""

from __future__ import annotations

from fnmatch import fnmatch


class CardTypeMapper:
    """根据 tool_name 自动推断 card_type，支持通配符匹配。"""

    MAPPING: dict[str, str] = {
        # search
        "web_search": "search",
        "search_*": "search",
        # code
        "code_execute": "code",
        "python_execute": "code",
        "shell_execute": "code",
        # file
        "generate_report": "file",
        "create_document": "file",
        "export_*": "file",
        # analysis
        "analyze_data": "analysis",
        "chart_*": "analysis",
        # browser
        "browser_*": "browser",
        "navigate_*": "browser",
    }

    def get_type(self, tool_name: str) -> str:
        """返回 card_type，未匹配返回 ``'default'``。"""
        # 精确匹配优先
        if tool_name in self.MAPPING:
            return self.MAPPING[tool_name]
        # 通配符匹配
        for pattern, card_type in self.MAPPING.items():
            if "*" in pattern and fnmatch(tool_name, pattern):
                return card_type
        return "default"
