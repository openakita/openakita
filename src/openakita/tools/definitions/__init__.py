"""
System tool definitions module

Extracts tool definitions from agent.py, organized by category.
Each file defines a category of tools, unified export at the end.

Follows the tool-definition-spec.md specification.

Structure:
- base.py         # Base types, validators, builders
- browser.py      # Browser tools (11)
- filesystem.py   # File System tools (8)
- skills.py       # Skills tools (7)
- memory.py       # Memory tools (3)
- scheduled.py    # Scheduled Tasks tools (5)
- im_channel.py   # IM Channel tools (4)
- profile.py      # User Profile tools (3)
- system.py       # System tools (7)
- mcp.py          # MCP tools (8)
- plan.py         # Todo & Plan tools (6)
- web_search.py   # Web Search tools (2)
- web_fetch.py    # Web Fetch tools (1)
- code_quality.py # Code Quality tools (1)
- search.py       # Search tools (1)
- mode.py         # Mode tools (1)
- notebook.py     # Notebook tools (1)
- config.py       # Config tools (1, unified configuration management)
"""

# Base modules
from .agent import AGENT_TOOLS
from .agent_hub import AGENT_HUB_TOOLS
from .agent_package import AGENT_PACKAGE_TOOLS
from .base import (
    Prerequisite,
    RelatedTool,
    ToolBuilder,
    ToolDefinition,
    ToolExample,
    build_description,
    build_detail,
    filter_tools_by_category,
    infer_category,
    merge_tool_lists,
    validate_description,
    validate_tool_definition,
    validate_tool_name,
)
from .browser import BROWSER_TOOLS
from .cli_anything import CLI_ANYTHING_TOOLS
from .code_quality import CODE_QUALITY_TOOLS

# Tool definitions
from .config import CONFIG_TOOLS
from .filesystem import FILESYSTEM_TOOLS
from .im_channel import IM_CHANNEL_TOOLS
from .lsp import LSP_TOOLS
from .mcp import MCP_TOOLS
from .memory import MEMORY_TOOLS
from .mode import MODE_TOOLS
from .notebook import NOTEBOOK_TOOLS
from .opencli import OPENCLI_TOOLS
from .org_setup import ORG_SETUP_TOOLS
from .persona import PERSONA_TOOLS
from .plan import PLAN_TOOLS
from .plugins import PLUGIN_TOOLS
from .powershell import POWERSHELL_TOOLS
from .profile import PROFILE_TOOLS
from .scheduled import SCHEDULED_TOOLS
from .search import SEARCH_TOOLS
from .skill_store import SKILL_STORE_TOOLS
from .skills import SKILLS_TOOLS
from .sleep import SLEEP_TOOLS
from .sticker import STICKER_TOOLS
from .structured_output import STRUCTURED_OUTPUT_TOOLS
from .system import SYSTEM_TOOLS
from .tool_search import TOOL_SEARCH_TOOLS
from .web_fetch import WEB_FETCH_TOOLS
from .web_search import WEB_SEARCH_TOOLS
from .worktree import WORKTREE_TOOLS

# Merge all tool definitions (excluding platform connection tools, which are dynamically loaded by the agent based on hub_enabled)
BASE_TOOLS = (
    FILESYSTEM_TOOLS
    + SKILLS_TOOLS
    + MEMORY_TOOLS
    + BROWSER_TOOLS
    + SCHEDULED_TOOLS
    + IM_CHANNEL_TOOLS
    + SYSTEM_TOOLS
    + PROFILE_TOOLS
    + MCP_TOOLS
    + PLAN_TOOLS
    + WEB_SEARCH_TOOLS
    + WEB_FETCH_TOOLS
    + CODE_QUALITY_TOOLS
    + SEARCH_TOOLS
    + MODE_TOOLS
    + NOTEBOOK_TOOLS
    + PERSONA_TOOLS
    + STICKER_TOOLS
    + CONFIG_TOOLS
    + AGENT_PACKAGE_TOOLS
    + PLUGIN_TOOLS
    + POWERSHELL_TOOLS
    + TOOL_SEARCH_TOOLS
    + LSP_TOOLS
    + SLEEP_TOOLS
    + STRUCTURED_OUTPUT_TOOLS
    + WORKTREE_TOOLS
)

# Platform connection tools (Agent Hub + Skill Store), registered only when hub_enabled=True
HUB_TOOLS = AGENT_HUB_TOOLS + SKILL_STORE_TOOLS

_ALL_TOOLS = list(BASE_TOOLS) + list(HUB_TOOLS) + list(AGENT_TOOLS)
_TOOL_DEFINITIONS_BY_NAME = {tool["name"]: tool for tool in _ALL_TOOLS}


def get_tool_definition(tool_name: str) -> dict | None:
    """Return the static tool definition for a tool name, if known."""
    return _TOOL_DEFINITIONS_BY_NAME.get(tool_name)


def get_tool_input_schema(tool_name: str) -> dict:
    """Return a tool's input schema or an empty dict when unavailable."""
    tool = get_tool_definition(tool_name)
    schema = tool.get("input_schema") if tool else None
    return schema if isinstance(schema, dict) else {}


__all__ = [
    # Base types and tools
    "ToolDefinition",
    "ToolExample",
    "RelatedTool",
    "Prerequisite",
    "ToolBuilder",
    "validate_tool_definition",
    "validate_tool_name",
    "validate_description",
    "build_description",
    "build_detail",
    "infer_category",
    "merge_tool_lists",
    "filter_tools_by_category",
    # Tool lists
    "BASE_TOOLS",
    "HUB_TOOLS",
    "AGENT_TOOLS",
    "ORG_SETUP_TOOLS",
    "AGENT_HUB_TOOLS",
    "AGENT_PACKAGE_TOOLS",
    "SKILL_STORE_TOOLS",
    "BROWSER_TOOLS",
    "CODE_QUALITY_TOOLS",
    "FILESYSTEM_TOOLS",
    "MODE_TOOLS",
    "NOTEBOOK_TOOLS",
    "SKILLS_TOOLS",
    "MEMORY_TOOLS",
    "SCHEDULED_TOOLS",
    "SEARCH_TOOLS",
    "IM_CHANNEL_TOOLS",
    "PROFILE_TOOLS",
    "SYSTEM_TOOLS",
    "MCP_TOOLS",
    "PLAN_TOOLS",
    "WEB_FETCH_TOOLS",
    "WEB_SEARCH_TOOLS",
    "PERSONA_TOOLS",
    "STICKER_TOOLS",
    "CONFIG_TOOLS",
    "OPENCLI_TOOLS",
    "CLI_ANYTHING_TOOLS",
    "PLUGIN_TOOLS",
    "POWERSHELL_TOOLS",
    "TOOL_SEARCH_TOOLS",
    "LSP_TOOLS",
    "SLEEP_TOOLS",
    "STRUCTURED_OUTPUT_TOOLS",
    "WORKTREE_TOOLS",
    "get_tool_definition",
    "get_tool_input_schema",
]
