"""
系统工具定义模块

将工具定义从 agent.py 抽离出来，按类别组织。
每个文件定义一类工具，最后统一导出。

遵循 tool-definition-spec.md 规范。

结构：
- base.py         # 基础类型、验证器、构建器
- browser.py      # Browser 工具（11 个）
- filesystem.py   # File System 工具（8 个）
- skills.py       # Skills 工具（7 个）
- memory.py       # Memory 工具（3 个）
- scheduled.py    # Scheduled Tasks 工具（5 个）
- im_channel.py   # IM Channel 工具（4 个）
- profile.py      # User Profile 工具（3 个）
- system.py       # System 工具（7 个）
- mcp.py          # MCP 工具（8 个）
- plan.py         # Todo & Plan 工具（6 个）
- web_search.py   # Web Search 工具（2 个）
- web_fetch.py    # Web Fetch 工具（1 个）
- code_quality.py # Code Quality 工具（1 个）
- search.py       # Search 工具（1 个）
- mode.py         # Mode 工具（1 个）
- notebook.py     # Notebook 工具（1 个）
- config.py       # Config 工具（1 个，统一配置管理）
"""

# 基础模块
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

# 工具定义
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

# 合并所有工具定义（不含平台连接类工具，后者由 agent 根据 hub_enabled 动态加载）
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

# 平台连接工具（Agent Hub + Skill Store），仅在 hub_enabled=True 时注册
HUB_TOOLS = AGENT_HUB_TOOLS + SKILL_STORE_TOOLS

_ALL_TOOLS = list(BASE_TOOLS) + list(HUB_TOOLS)
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
    # 基础类型和工具
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
    # 工具列表
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
