"""
系统工具目录 (Tool Catalog)

遵循渐进式披露原则（与 Agent Skills 规范对齐）:
- Level 1: 工具清单 (name + description) - 在系统提示中提供
- Level 2: 详细说明 (detail + examples + triggers + prerequisites) - 通过 get_tool_info 获取 / 传给 LLM API
- Level 3: 直接执行工具

工具定义格式（遵循 tool-definition-spec.md）：
{
    # 必填字段
    "name": "tool_name",
    "description": "清单披露的简短描述（Level 1）",
    "input_schema": {...},
    
    # 推荐字段
    "detail": "详细使用说明（Level 2）",
    "triggers": ["触发条件1", "触发条件2"],
    "prerequisites": ["前置条件1", "前置条件2"],
    "examples": [{"scenario": "...", "params": {...}, "expected": "..."}],
    
    # 可选字段
    "category": "工具分类",
    "warnings": ["重要警告"],
    "related_tools": [{"name": "...", "relation": "..."}],
}

如果没有 detail 字段，则 fallback 到 description。
"""

import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)


class ToolCatalog:
    """
    系统工具目录
    
    管理工具清单的生成和格式化，用于系统提示注入。
    支持渐进式披露：
    - Level 1: 工具清单 (name + short_description)
    - Level 2: 完整定义 (description + input_schema)
    """
    
    # 工具清单模板
    CATALOG_TEMPLATE = """
## Available System Tools

The following system tools are available. Use `get_tool_info(tool_name)` to get full parameters before calling.

{tool_list}

### How to Use System Tools

1. **Identify tool** from the list above based on your task
2. **Get details**: Use `get_tool_info(tool_name)` for full description and parameters
3. **Execute**: Call the tool directly with the required parameters
"""

    # 工具分类
    TOOL_CATEGORIES = {
        "File System": ["run_shell", "write_file", "read_file", "list_directory"],
        "Skills Management": ["list_skills", "get_skill_info", "run_skill_script", "get_skill_reference", "install_skill", "generate_skill", "improve_skill"],
        "Memory": ["add_memory", "search_memory", "get_memory_stats"],
        "Browser": ["browser_open", "browser_status", "browser_list_tabs", "browser_navigate", "browser_new_tab", "browser_switch_tab", "browser_click", "browser_type", "browser_get_content", "browser_screenshot"],
        "Desktop (Windows)": ["desktop_screenshot", "desktop_find_element", "desktop_click", "desktop_type", "desktop_hotkey", "desktop_scroll", "desktop_window", "desktop_wait", "desktop_inspect"],
        "Scheduled Tasks": ["schedule_task", "list_scheduled_tasks", "cancel_scheduled_task", "trigger_scheduled_task"],
        "IM Channel": ["send_to_chat", "get_voice_file", "get_image_file", "get_chat_history"],
        "User Profile": ["update_user_profile", "skip_profile_question", "get_user_profile"],
        "System": ["enable_thinking", "get_session_logs", "get_tool_info"],
        "MCP": ["call_mcp_tool", "list_mcp_servers", "get_mcp_instructions"],
    }
    
    TOOL_ENTRY_TEMPLATE = "- **{name}**: {description}"
    CATEGORY_TEMPLATE = "\n### {category}\n{tools}"
    
    def __init__(self, tools: list[dict]):
        """
        初始化工具目录
        
        Args:
            tools: 工具定义列表，每个工具包含 name, short_description, description, input_schema
        """
        self._tools = {t["name"]: t for t in tools}
        self._cached_catalog: Optional[str] = None
    
    def generate_catalog(self) -> str:
        """
        生成工具清单（Level 1）
        
        只包含 name + short_description，用于系统提示
        
        Returns:
            格式化的工具清单字符串
        """
        if not self._tools:
            return "\n## Available System Tools\n\nNo system tools available.\n"
        
        category_sections = []
        categorized_tools = set()
        
        # 按类别生成
        for category, tool_names in self.TOOL_CATEGORIES.items():
            tools_in_category = []
            for name in tool_names:
                if name in self._tools:
                    tool = self._tools[name]
                    # 优先使用 short_description，否则截取 description
                    desc = tool.get("short_description") or self._get_short_description(tool.get("description", ""))
                    entry = self.TOOL_ENTRY_TEMPLATE.format(name=name, description=desc)
                    tools_in_category.append(entry)
                    categorized_tools.add(name)
            
            if tools_in_category:
                section = self.CATEGORY_TEMPLATE.format(
                    category=category,
                    tools="\n".join(tools_in_category)
                )
                category_sections.append(section)
        
        # 未分类的工具
        uncategorized = []
        for name, tool in self._tools.items():
            if name not in categorized_tools:
                desc = tool.get("short_description") or self._get_short_description(tool.get("description", ""))
                entry = self.TOOL_ENTRY_TEMPLATE.format(name=name, description=desc)
                uncategorized.append(entry)
        
        if uncategorized:
            section = self.CATEGORY_TEMPLATE.format(
                category="Other",
                tools="\n".join(uncategorized)
            )
            category_sections.append(section)
        
        tool_list = "\n".join(category_sections)
        catalog = self.CATALOG_TEMPLATE.format(tool_list=tool_list)
        self._cached_catalog = catalog
        
        logger.info(f"Generated tool catalog with {len(self._tools)} tools")
        return catalog
    
    def _get_short_description(self, description: str) -> str:
        """
        从完整描述中提取简短描述
        
        Args:
            description: 完整描述
        
        Returns:
            简短描述（第一行，不截断以保留完整警告信息）
        """
        if not description:
            return ""
        
        # 取第一行，不再截断
        # 原因：完整工具定义已通过 tools 参数传给 LLM API，
        # 清单中截断会丢失重要警告（如 ⚠️ 必须先检查状态），
        # 导致 LLM 行为异常（如不调用工具就说"完成"）
        first_line = description.split('\n')[0].strip()
        
        return first_line
    
    def get_catalog(self, refresh: bool = False) -> str:
        """
        获取工具清单
        
        Args:
            refresh: 是否强制刷新
        
        Returns:
            工具清单字符串
        """
        if refresh or self._cached_catalog is None:
            return self.generate_catalog()
        return self._cached_catalog
    
    def get_tool_info(self, tool_name: str) -> Optional[dict]:
        """
        获取工具的完整定义（Level 2）
        
        Args:
            tool_name: 工具名称
        
        Returns:
            工具完整定义，包含 description 和 input_schema
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return None
        
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("input_schema", {}),
        }
    
    def get_tool_info_formatted(self, tool_name: str) -> str:
        """
        获取工具的格式化完整信息（Level 2 详细说明）
        
        支持新规范字段：triggers, prerequisites, examples, warnings, related_tools
        
        Args:
            tool_name: 工具名称
        
        Returns:
            格式化的工具信息字符串
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return f"❌ Tool not found: {tool_name}"
        
        output = f"# Tool: {tool['name']}\n\n"
        
        # 分类
        category = tool.get('category')
        if category:
            output += f"**Category**: {category}\n\n"
        
        # 详细说明（优先使用 detail，否则 fallback 到 description）
        detail = tool.get('detail') or tool.get('description', 'No description')
        output += f"{detail}\n\n"
        
        # 警告信息
        warnings = tool.get("warnings", [])
        if warnings:
            output += "## ⚠️ Warnings\n\n"
            for warning in warnings:
                output += f"- {warning}\n"
            output += "\n"
        
        # 触发条件
        triggers = tool.get("triggers", [])
        if triggers:
            output += "## When to Use\n\n"
            for trigger in triggers:
                output += f"- {trigger}\n"
            output += "\n"
        
        # 前置条件
        prerequisites = tool.get("prerequisites", [])
        if prerequisites:
            output += "## Prerequisites\n\n"
            for prereq in prerequisites:
                if isinstance(prereq, dict):
                    output += f"- {prereq.get('condition', prereq)}\n"
                else:
                    output += f"- {prereq}\n"
            output += "\n"
        
        # 参数说明
        schema = tool.get("input_schema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])
        
        if props:
            output += "## Parameters\n\n"
            for param_name, param_def in props.items():
                req_mark = " **(required)**" if param_name in required else ""
                param_type = param_def.get("type", "any")
                param_desc = param_def.get("description", "")
                default = param_def.get("default")
                enum_vals = param_def.get("enum")
                
                output += f"- `{param_name}` ({param_type}){req_mark}: {param_desc}"
                if default is not None:
                    output += f" (default: {default})"
                if enum_vals:
                    output += f" [options: {', '.join(str(v) for v in enum_vals)}]"
                output += "\n"
            output += "\n"
        else:
            output += "## Parameters\n\nNo parameters required.\n\n"
        
        # 使用示例
        examples = tool.get("examples", [])
        if examples:
            output += "## Examples\n\n"
            for i, example in enumerate(examples, 1):
                scenario = example.get("scenario", f"Example {i}")
                params = example.get("params", {})
                expected = example.get("expected", "")
                
                output += f"**{scenario}**\n"
                output += f"```json\n{self._format_params(params)}\n```\n"
                if expected:
                    output += f"→ {expected}\n"
                output += "\n"
        
        # 相关工具
        related_tools = tool.get("related_tools", [])
        if related_tools:
            output += "## Related Tools\n\n"
            for related in related_tools:
                name = related.get("name", "")
                relation = related.get("relation", "")
                output += f"- `{name}`: {relation}\n"
            output += "\n"
        
        return output
    
    def _format_params(self, params: dict) -> str:
        """格式化参数为 JSON 字符串"""
        import json
        if not params:
            return "{}"
        return json.dumps(params, ensure_ascii=False, indent=2)
    
    def list_tools(self) -> list[str]:
        """列出所有工具名称"""
        return list(self._tools.keys())
    
    def has_tool(self, tool_name: str) -> bool:
        """检查工具是否存在"""
        return tool_name in self._tools
    
    def update_tools(self, tools: list[dict]) -> None:
        """
        更新工具列表
        
        Args:
            tools: 新的工具定义列表
        """
        self._tools = {t["name"]: t for t in tools}
        self._cached_catalog = None
    
    def add_tool(self, tool: dict) -> None:
        """
        添加单个工具
        
        Args:
            tool: 工具定义
        """
        self._tools[tool["name"]] = tool
        self._cached_catalog = None
    
    def remove_tool(self, tool_name: str) -> bool:
        """
        移除工具
        
        Args:
            tool_name: 工具名称
        
        Returns:
            是否成功移除
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            self._cached_catalog = None
            return True
        return False
    
    def invalidate_cache(self) -> None:
        """使缓存失效"""
        self._cached_catalog = None
    
    @property
    def tool_count(self) -> int:
        """工具数量"""
        return len(self._tools)


def create_tool_catalog(tools: list[dict]) -> ToolCatalog:
    """便捷函数：创建工具目录"""
    return ToolCatalog(tools)
