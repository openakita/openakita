"""ToolFilter: 配置化的工具过滤器。

决定哪些工具调用应展示为 step_card（用户可见的语义步骤），
哪些应隐藏（系统级 / 框架级内部操作）。
"""

from __future__ import annotations

from fnmatch import fnmatch


class ToolFilter:
    """工具展示/隐藏过滤器，支持 fnmatch 通配符。"""

    # 用户可理解的语义动作 → 展示
    SHOW_PATTERNS: list[str] = [
        "web_search",
        "browser_*",
        "generate_report",
        "analyze_data",
        "translate_text",
        "summarize_*",
        "create_*",
        "extract_*",
        "code_execute",
        "python_execute",
        "shell_execute",
        "send_email",
        "send_message",
        "navigate_*",
        "chart_*",
        "export_*",
        "search_*",
    ]

    # 系统级 / 框架级工具 → 隐藏
    HIDE_PATTERNS: list[str] = [
        "read_file",
        "write_file",
        "list_files",
        "memory_*",
        "core_memory_*",
        "prompt_*",
        "context_*",
        "skill_*",
        "route_*",
        "system_config",
        "get_capabilities",
        "plan_task",
        "update_plan",
    ]

    def should_show(self, tool_name: str) -> bool:
        """判断工具是否应展示为 step_card。

        优先级: HIDE 匹配 → 隐藏; SHOW 匹配 → 展示; 默认 → 展示。
        """
        # 黑名单优先
        for pattern in self.HIDE_PATTERNS:
            if tool_name == pattern or ("*" in pattern and fnmatch(tool_name, pattern)):
                return False
        # 白名单匹配
        for pattern in self.SHOW_PATTERNS:
            if tool_name == pattern or ("*" in pattern and fnmatch(tool_name, pattern)):
                return True
        # 未命中任何规则 → 默认展示（宁多勿漏）
        return True
