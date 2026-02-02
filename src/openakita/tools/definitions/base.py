"""
工具定义基础模块

提供工具定义的类型、验证和辅助函数。
遵循 tool-definition-spec.md 规范。
"""

from typing import TypedDict, Optional, Any, Literal
from dataclasses import dataclass, field
import re
import logging

logger = logging.getLogger(__name__)


# ==================== 类型定义 ====================

class ToolExample(TypedDict, total=False):
    """工具使用示例"""
    scenario: str           # 场景描述
    params: dict[str, Any]  # 调用参数
    expected: str           # 预期结果


class RelatedTool(TypedDict, total=False):
    """相关工具"""
    name: str               # 工具名称
    relation: str           # 关系说明（如 "should check before", "commonly used after"）


class Prerequisite(TypedDict, total=False):
    """前置条件"""
    condition: str          # 条件描述
    check_tool: str         # 检查工具
    action_if_not_met: str  # 不满足时的操作


class WorkflowStep(TypedDict, total=False):
    """工作流步骤"""
    step: int               # 步骤编号
    action: str             # 操作描述
    tool: str               # 使用的工具
    tools: list[str]        # 可选的多个工具
    condition: str          # 条件


class Workflow(TypedDict, total=False):
    """工作流定义"""
    name: str               # 工作流名称
    steps: list[WorkflowStep]  # 步骤列表


class ToolDefinition(TypedDict, total=False):
    """工具定义（完整格式）"""
    # 必填字段
    name: str               # 工具名称
    description: str        # 简短描述（Level 1）
    input_schema: dict      # 参数 Schema
    
    # 推荐字段
    detail: str             # 详细说明（Level 2）
    triggers: list[str]     # 触发条件
    prerequisites: list[str | Prerequisite]  # 前置条件
    examples: list[ToolExample]  # 使用示例
    
    # 可选字段
    category: str           # 工具分类
    warnings: list[str]     # 重要警告
    related_tools: list[RelatedTool]  # 相关工具
    workflow: Workflow      # 工作流定义


# ==================== 工具分类 ====================

ToolCategory = Literal[
    "File System",
    "Browser",
    "Desktop",
    "Memory",
    "Skills",
    "Scheduled",
    "IM Channel",
    "Profile",
    "System",
    "MCP",
]

CATEGORY_PREFIXES = {
    "Browser": "browser_",
    "Desktop": "desktop_",
    "Skills": ("list_skills", "get_skill_info", "run_skill_script", "get_skill_reference", "install_skill", "generate_skill", "improve_skill"),
    "Memory": ("add_memory", "search_memory", "get_memory_stats"),
    "Scheduled": ("schedule_task", "list_scheduled_tasks", "cancel_scheduled_task", "update_scheduled_task", "trigger_scheduled_task"),
    "IM Channel": ("send_to_chat", "get_voice_file", "get_image_file", "get_chat_history"),
    "Profile": ("update_user_profile", "skip_profile_question", "get_user_profile"),
    "System": ("enable_thinking", "get_session_logs", "get_tool_info"),
    "MCP": ("call_mcp_tool", "list_mcp_servers", "get_mcp_instructions"),
    "File System": ("run_shell", "write_file", "read_file", "list_directory"),
}


# ==================== 辅助函数 ====================

def validate_tool_name(name: str) -> tuple[bool, str]:
    """
    验证工具名称
    
    Args:
        name: 工具名称
        
    Returns:
        (是否有效, 错误信息)
    """
    if not name:
        return False, "Name cannot be empty"
    
    if len(name) > 64:
        return False, f"Name too long: {len(name)} > 64"
    
    if not re.match(r'^[a-z][a-z0-9_]*$', name):
        return False, "Name must be snake_case (lowercase letters, numbers, underscores)"
    
    return True, ""


def validate_description(description: str) -> tuple[bool, str]:
    """
    验证工具描述
    
    Args:
        description: 描述文本
        
    Returns:
        (是否有效, 错误信息)
    """
    if not description:
        return False, "Description cannot be empty"
    
    if len(description) > 500:
        return False, f"Description too long: {len(description)} > 500"
    
    # 检查是否包含使用场景
    if "When you need to" not in description and "When" not in description:
        logger.warning(f"Description may lack usage scenarios")
    
    return True, ""


def validate_tool_definition(tool: dict) -> tuple[bool, list[str]]:
    """
    验证完整工具定义
    
    Args:
        tool: 工具定义字典
        
    Returns:
        (是否有效, 错误列表)
    """
    errors = []
    
    # 必填字段
    if "name" not in tool:
        errors.append("Missing required field: name")
    else:
        valid, error = validate_tool_name(tool["name"])
        if not valid:
            errors.append(f"Invalid name: {error}")
    
    if "description" not in tool:
        errors.append("Missing required field: description")
    else:
        valid, error = validate_description(tool["description"])
        if not valid:
            errors.append(f"Invalid description: {error}")
    
    if "input_schema" not in tool:
        errors.append("Missing required field: input_schema")
    elif not isinstance(tool["input_schema"], dict):
        errors.append("input_schema must be a dict")
    elif tool["input_schema"].get("type") != "object":
        errors.append("input_schema.type must be 'object'")
    
    # 验证示例（如果有）
    if "examples" in tool:
        schema_props = tool.get("input_schema", {}).get("properties", {})
        for i, example in enumerate(tool["examples"]):
            if "params" in example:
                for param_name in example["params"]:
                    if param_name not in schema_props:
                        errors.append(f"Example {i}: unknown param '{param_name}'")
    
    return len(errors) == 0, errors


def infer_category(tool_name: str) -> Optional[str]:
    """
    根据工具名称推断分类
    
    Args:
        tool_name: 工具名称
        
    Returns:
        分类名称，无法推断时返回 None
    """
    for category, pattern in CATEGORY_PREFIXES.items():
        if isinstance(pattern, str):
            if tool_name.startswith(pattern):
                return category
        elif isinstance(pattern, tuple):
            if tool_name in pattern:
                return category
    return None


def build_description(
    what: str,
    triggers: list[str],
    warnings: list[str] = None,
    prerequisites: list[str] = None,
) -> str:
    """
    构建标准格式的工具描述
    
    Args:
        what: 工具功能描述
        triggers: 触发条件列表
        warnings: 警告信息
        prerequisites: 前置条件
        
    Returns:
        格式化的描述字符串
    """
    parts = [what]
    
    # 添加触发条件
    if triggers:
        trigger_str = " When you need to: " + ", ".join(
            f"({i+1}) {t}" for i, t in enumerate(triggers[:3])
        )
        parts.append(trigger_str.rstrip(".") + ".")
    
    # 添加前置条件
    if prerequisites:
        parts.append(f" PREREQUISITE: {prerequisites[0]}")
    
    # 添加警告
    if warnings:
        parts.append(f" IMPORTANT: {warnings[0]}")
    
    return "".join(parts)


def build_detail(
    summary: str,
    scenarios: list[str] = None,
    params_desc: dict[str, str] = None,
    notes: list[str] = None,
    workflow_steps: list[str] = None,
) -> str:
    """
    构建标准格式的详细说明
    
    Args:
        summary: 功能简述
        scenarios: 适用场景
        params_desc: 参数说明
        notes: 注意事项
        workflow_steps: 工作流步骤
        
    Returns:
        格式化的详细说明（Markdown）
    """
    lines = [summary, ""]
    
    if scenarios:
        lines.append("**适用场景**：")
        for s in scenarios:
            lines.append(f"- {s}")
        lines.append("")
    
    if params_desc:
        lines.append("**参数说明**：")
        for param, desc in params_desc.items():
            lines.append(f"- {param}: {desc}")
        lines.append("")
    
    if workflow_steps:
        lines.append("**使用流程**：")
        for i, step in enumerate(workflow_steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")
    
    if notes:
        lines.append("**注意事项**：")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")
    
    return "\n".join(lines).strip()


# ==================== 工具定义构建器 ====================

@dataclass
class ToolBuilder:
    """
    工具定义构建器
    
    使用链式调用构建工具定义：
    
    >>> tool = (ToolBuilder("browser_navigate")
    ...     .what("Navigate browser to specified URL")
    ...     .triggers(["Open a webpage", "Start web interaction"])
    ...     .param("url", "string", "要访问的 URL", required=True)
    ...     .example("打开 Google", {"url": "https://google.com"})
    ...     .build())
    """
    
    name: str
    _description: str = ""
    _detail: str = ""
    _triggers: list[str] = field(default_factory=list)
    _prerequisites: list[str] = field(default_factory=list)
    _warnings: list[str] = field(default_factory=list)
    _examples: list[dict] = field(default_factory=list)
    _related_tools: list[dict] = field(default_factory=list)
    _category: str = ""
    _params: dict = field(default_factory=dict)
    _required_params: list[str] = field(default_factory=list)
    
    def what(self, description: str) -> "ToolBuilder":
        """设置功能描述"""
        self._description = description
        return self
    
    def triggers(self, triggers: list[str]) -> "ToolBuilder":
        """设置触发条件"""
        self._triggers = triggers
        return self
    
    def prerequisites(self, prereqs: list[str]) -> "ToolBuilder":
        """设置前置条件"""
        self._prerequisites = prereqs
        return self
    
    def warnings(self, warnings: list[str]) -> "ToolBuilder":
        """设置警告信息"""
        self._warnings = warnings
        return self
    
    def detail(self, detail: str) -> "ToolBuilder":
        """设置详细说明"""
        self._detail = detail
        return self
    
    def category(self, category: str) -> "ToolBuilder":
        """设置工具分类"""
        self._category = category
        return self
    
    def param(
        self,
        name: str,
        type_: str,
        description: str,
        required: bool = False,
        default: Any = None,
        enum: list = None,
    ) -> "ToolBuilder":
        """添加参数定义"""
        param_def = {
            "type": type_,
            "description": description,
        }
        if default is not None:
            param_def["default"] = default
        if enum:
            param_def["enum"] = enum
        
        self._params[name] = param_def
        if required:
            self._required_params.append(name)
        return self
    
    def example(
        self,
        scenario: str,
        params: dict,
        expected: str = None,
    ) -> "ToolBuilder":
        """添加使用示例"""
        example = {"scenario": scenario, "params": params}
        if expected:
            example["expected"] = expected
        self._examples.append(example)
        return self
    
    def related(self, name: str, relation: str) -> "ToolBuilder":
        """添加相关工具"""
        self._related_tools.append({"name": name, "relation": relation})
        return self
    
    def build(self) -> dict:
        """构建工具定义"""
        # 构建 description
        description = build_description(
            what=self._description,
            triggers=self._triggers,
            warnings=self._warnings,
            prerequisites=self._prerequisites,
        )
        
        tool = {
            "name": self.name,
            "description": description,
            "input_schema": {
                "type": "object",
                "properties": self._params,
                "required": self._required_params,
            },
        }
        
        # 可选字段
        if self._detail:
            tool["detail"] = self._detail
        if self._triggers:
            tool["triggers"] = self._triggers
        if self._prerequisites:
            tool["prerequisites"] = self._prerequisites
        if self._warnings:
            tool["warnings"] = self._warnings
        if self._examples:
            tool["examples"] = self._examples
        if self._related_tools:
            tool["related_tools"] = self._related_tools
        if self._category:
            tool["category"] = self._category
        else:
            # 自动推断分类
            inferred = infer_category(self.name)
            if inferred:
                tool["category"] = inferred
        
        # 验证
        valid, errors = validate_tool_definition(tool)
        if not valid:
            logger.warning(f"Tool {self.name} validation warnings: {errors}")
        
        return tool


# ==================== 工具列表合并 ====================

def merge_tool_lists(*tool_lists: list[dict]) -> list[dict]:
    """
    合并多个工具列表
    
    Args:
        tool_lists: 多个工具定义列表
        
    Returns:
        合并后的工具列表（去重）
    """
    seen = set()
    result = []
    
    for tools in tool_lists:
        for tool in tools:
            name = tool.get("name")
            if name and name not in seen:
                seen.add(name)
                result.append(tool)
    
    return result


def filter_tools_by_category(
    tools: list[dict],
    categories: list[str],
) -> list[dict]:
    """
    按分类筛选工具
    
    Args:
        tools: 工具列表
        categories: 要保留的分类
        
    Returns:
        筛选后的工具列表
    """
    result = []
    for tool in tools:
        category = tool.get("category") or infer_category(tool.get("name", ""))
        if category in categories:
            result.append(tool)
    return result
