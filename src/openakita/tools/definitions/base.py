"""
Tool definition base module

Provides types, validation, and helper functions for tool definitions.
Follows the tool-definition-spec.md specification.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)


# ==================== Type Definitions ====================


class ToolExample(TypedDict, total=False):
    """Tool usage example"""

    scenario: str  # Scenario description
    params: dict[str, Any]  # Call parameters
    expected: str  # Expected result


class RelatedTool(TypedDict, total=False):
    """Related tool"""

    name: str  # Tool name
    relation: str  # Relationship description (e.g., "should check before", "commonly used after")


class Prerequisite(TypedDict, total=False):
    """Prerequisite"""

    condition: str  # Condition description
    check_tool: str  # Check tool
    action_if_not_met: str  # Action if condition is not met


class WorkflowStep(TypedDict, total=False):
    """Workflow step"""

    step: int  # Step number
    action: str  # Action description
    tool: str  # Tool to use
    tools: list[str]  # Optional multiple tools
    condition: str  # Condition


class Workflow(TypedDict, total=False):
    """Workflow definition"""

    name: str  # Workflow name
    steps: list[WorkflowStep]  # List of steps


class ToolDefinition(TypedDict, total=False):
    """Tool definition (full format)"""

    # Required fields
    name: str  # Tool name
    description: str  # Short description (Level 1)
    input_schema: dict  # Parameter schema

    # Recommended fields
    detail: str  # Detailed description (Level 2)
    triggers: list[str]  # Trigger conditions
    prerequisites: list[str | Prerequisite]  # Prerequisites
    examples: list[ToolExample]  # Usage examples

    # Optional fields
    category: str  # Tool category
    warnings: list[str]  # Important warnings
    related_tools: list[RelatedTool]  # Related tools
    workflow: Workflow  # Workflow definition


# ==================== Tool Categories ====================

ToolCategory = Literal[
    "Agent",
    "File System",
    "Browser",
    "Desktop",
    "Memory",
    "Skills",
    "Plugin",
    "Scheduled",
    "IM Channel",
    "Profile",
    "System",
    "MCP",
    "Plan",
    "Web Search",
    "Config",
]

CATEGORY_PREFIXES = {
    "Agent": (
        "delegate_to_agent",
        "spawn_agent",
        "delegate_parallel",
        "create_agent",
        "task_stop",
        "send_agent_message",
        "setup_organization",
    ),
    "Browser": "browser_",
    "Desktop": "desktop_",
    "Skills": (
        "list_skills",
        "get_skill_info",
        "run_skill_script",
        "get_skill_reference",
        "install_skill",
        "load_skill",
        "reload_skill",
        "manage_skill_enabled",
        "execute_skill",
        "uninstall_skill",
        "find_skills",
        "install_store_skill",
        "search_store_skills",
        "submit_skill_repo",
    ),
    "Memory": (
        "add_memory",
        "search_memory",
        "get_memory_stats",
        "search_relational_memory",
        "list_recent_tasks",
        "search_conversation_traces",
        "trace_memory",
        "consolidate_memories",
    ),
    "Scheduled": (
        "schedule_task",
        "list_scheduled_tasks",
        "cancel_scheduled_task",
        "update_scheduled_task",
        "trigger_scheduled_task",
    ),
    "IM Channel": (
        "deliver_artifacts",
        "get_voice_file",
        "get_image_file",
        "get_chat_history",
        "send_sticker",
    ),
    "Profile": (
        "update_user_profile",
        "skip_profile_question",
        "get_user_profile",
        "switch_persona",
        "toggle_proactive",
    ),
    "System": (
        "enable_thinking",
        "get_session_logs",
        "get_tool_info",
        "generate_image",
        "set_task_timeout",
        "get_workspace_map",
        "get_session_context",
    ),
    "MCP": (
        "call_mcp_tool",
        "list_mcp_servers",
        "get_mcp_instructions",
        "add_mcp_server",
        "remove_mcp_server",
        "connect_mcp_server",
        "disconnect_mcp_server",
        "reload_mcp_servers",
    ),
    "File System": (
        "run_shell",
        "write_file",
        "read_file",
        "edit_file",
        "list_directory",
        "glob",
        "grep",
        "delete_file",
    ),
    "Text Search": ("semantic_search", "read_lints"),
    "Todo": ("create_todo", "update_todo_step", "get_todo_status", "complete_todo"),
    "Plan": ("create_plan_file", "exit_plan_mode"),
    "Web Search": ("web_search", "news_search", "web_fetch"),
    "Config": ("system_config",),
    "Plugin": ("list_plugins", "get_plugin_info"),
    "Advanced": (
        "run_powershell",
        "lsp",
        "sleep",
        "structured_output",
        "edit_notebook",
        "switch_mode",
        "tool_search",
        "enter_worktree",
        "exit_worktree",
        "view_image",
    ),
    "OpenCLI": ("opencli_list", "opencli_run", "opencli_doctor"),
    "Agent Package": (
        "export_agent",
        "import_agent",
        "inspect_agent_package",
        "publish_agent",
        "search_hub_agents",
        "install_hub_agent",
        "list_exportable_agents",
        "generate_agents_md",
    ),
    "Platform": ("platform_guide", "opencli", "cli_anything", "tool_routing"),
}


# ==================== Helper Functions ====================


def validate_tool_name(name: str) -> tuple[bool, str]:
    """
    Validate tool name

    Args:
        name: Tool name

    Returns:
        (is_valid, error_message)
    """
    if not name:
        return False, "Name cannot be empty"

    if len(name) > 64:
        return False, f"Name too long: {len(name)} > 64"

    if not re.match(r"^[a-z][a-z0-9_]*$", name):
        return False, "Name must be snake_case (lowercase letters, numbers, underscores)"

    return True, ""


def validate_description(description: str) -> tuple[bool, str]:
    """
    Validate tool description

    Args:
        description: Description text

    Returns:
        (is_valid, error_message)
    """
    if not description:
        return False, "Description cannot be empty"

    if len(description) > 500:
        return False, f"Description too long: {len(description)} > 500"

    # Check if usage scenarios are included
    if "When you need to" not in description and "When" not in description:
        logger.warning("Description may lack usage scenarios")

    return True, ""


def validate_tool_definition(tool: dict) -> tuple[bool, list[str]]:
    """
    Validate complete tool definition

    Args:
        tool: Tool definition dict

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    # Required fields
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

    # Validate examples (if present)
    if "examples" in tool:
        schema_props = tool.get("input_schema", {}).get("properties", {})
        for i, example in enumerate(tool["examples"]):
            if "params" in example:
                for param_name in example["params"]:
                    if param_name not in schema_props:
                        errors.append(f"Example {i}: unknown param '{param_name}'")

    return len(errors) == 0, errors


def infer_category(tool_name: str) -> str | None:
    """
    Infer category from tool name

    Args:
        tool_name: Tool name

    Returns:
        Category name, or None if unable to infer
    """
    for category, pattern in CATEGORY_PREFIXES.items():
        if isinstance(pattern, str):
            if tool_name.startswith(pattern):
                return category
        elif isinstance(pattern, tuple) and tool_name in pattern:
            return category
    return None


def build_description(
    what: str,
    triggers: list[str],
    warnings: list[str] = None,
    prerequisites: list[str] = None,
) -> str:
    """
    Build a standard-format tool description

    Args:
        what: Tool functionality description
        triggers: List of trigger conditions
        warnings: Warning messages
        prerequisites: Prerequisites

    Returns:
        Formatted description string
    """
    parts = [what]

    # Add trigger conditions
    if triggers:
        trigger_str = " When you need to: " + ", ".join(
            f"({i + 1}) {t}" for i, t in enumerate(triggers[:3])
        )
        parts.append(trigger_str.rstrip(".") + ".")

    # Add prerequisites
    if prerequisites:
        parts.append(f" PREREQUISITE: {prerequisites[0]}")

    # Add warnings
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
    Build a standard-format detailed description

    Args:
        summary: Brief functionality summary
        scenarios: Applicable scenarios
        params_desc: Parameter descriptions
        notes: Notes and caveats
        workflow_steps: Workflow steps

    Returns:
        Formatted detailed description (Markdown)
    """
    lines = [summary, ""]

    if scenarios:
        lines.append("**Applicable Scenarios**:")
        for s in scenarios:
            lines.append(f"- {s}")
        lines.append("")

    if params_desc:
        lines.append("**Parameters**:")
        for param, desc in params_desc.items():
            lines.append(f"- {param}: {desc}")
        lines.append("")

    if workflow_steps:
        lines.append("**Workflow**:")
        for i, step in enumerate(workflow_steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    if notes:
        lines.append("**Notes**:")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines).strip()


# ==================== Tool Definition Builder ====================


@dataclass
class ToolBuilder:
    """
    Tool definition builder

    Build tool definitions using chainable calls:

    >>> tool = (ToolBuilder("browser_navigate")
    ...     .what("Navigate browser to specified URL")
    ...     .triggers(["Open a webpage", "Start web interaction"])
    ...     .param("url", "string", "URL to visit", required=True)
    ...     .example("Open Google", {"url": "https://google.com"})
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
        """Set functionality description"""
        self._description = description
        return self

    def triggers(self, triggers: list[str]) -> "ToolBuilder":
        """Set trigger conditions"""
        self._triggers = triggers
        return self

    def prerequisites(self, prereqs: list[str]) -> "ToolBuilder":
        """Set prerequisites"""
        self._prerequisites = prereqs
        return self

    def warnings(self, warnings: list[str]) -> "ToolBuilder":
        """Set warning messages"""
        self._warnings = warnings
        return self

    def detail(self, detail: str) -> "ToolBuilder":
        """Set detailed description"""
        self._detail = detail
        return self

    def category(self, category: str) -> "ToolBuilder":
        """Set tool category"""
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
        """Add parameter definition"""
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
        """Add usage example"""
        example = {"scenario": scenario, "params": params}
        if expected:
            example["expected"] = expected
        self._examples.append(example)
        return self

    def related(self, name: str, relation: str) -> "ToolBuilder":
        """Add related tool"""
        self._related_tools.append({"name": name, "relation": relation})
        return self

    def build(self) -> dict:
        """Build tool definition"""
        # Build description
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

        # Optional fields
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
            # Auto-infer category
            inferred = infer_category(self.name)
            if inferred:
                tool["category"] = inferred

        # Validate
        valid, errors = validate_tool_definition(tool)
        if not valid:
            logger.warning(f"Tool {self.name} validation warnings: {errors}")

        return tool


# ==================== Tool List Merging ====================


def merge_tool_lists(*tool_lists: list[dict]) -> list[dict]:
    """
    Merge multiple tool lists

    Args:
        tool_lists: Multiple tool definition lists

    Returns:
        Merged tool list (deduplicated)
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
    Filter tools by category

    Args:
        tools: Tool list
        categories: Categories to keep

    Returns:
        Filtered tool list
    """
    result = []
    for tool in tools:
        category = tool.get("category") or infer_category(tool.get("name", ""))
        if category in categories:
            result.append(tool)
    return result
