"""
系统工具定义模块

将工具定义从 agent.py 抽离出来，按类别组织。
每个文件定义一类工具，最后统一导出。

遵循 tool-definition-spec.md 规范。

结构：
- base.py         # 基础类型、验证器、构建器
- browser.py      # Browser 工具（11 个）
- filesystem.py   # File System 工具（4 个）
- skills.py       # Skills 工具（7 个）
- memory.py       # Memory 工具（3 个）
- scheduled.py    # Scheduled Tasks 工具（5 个）
- im_channel.py   # IM Channel 工具（4 个）
- profile.py      # User Profile 工具（3 个）
- system.py       # System 工具（5 个）
- mcp.py          # MCP 工具（3 个）
- plan.py         # Plan 模式工具（4 个）
- web_search.py   # Web Search 工具（2 个）
"""

# 基础模块
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

# 工具定义
from .browser import BROWSER_TOOLS
from .filesystem import FILESYSTEM_TOOLS
from .im_channel import IM_CHANNEL_TOOLS
from .mcp import MCP_TOOLS
from .memory import MEMORY_TOOLS
from .persona import PERSONA_TOOLS
from .plan import PLAN_TOOLS
from .profile import PROFILE_TOOLS
from .scheduled import SCHEDULED_TOOLS
from .skills import SKILLS_TOOLS
from .sticker import STICKER_TOOLS
from .system import SYSTEM_TOOLS
from .web_search import WEB_SEARCH_TOOLS

# 合并所有工具定义
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
    + PERSONA_TOOLS
    + STICKER_TOOLS
)

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
    "BROWSER_TOOLS",
    "FILESYSTEM_TOOLS",
    "SKILLS_TOOLS",
    "MEMORY_TOOLS",
    "SCHEDULED_TOOLS",
    "IM_CHANNEL_TOOLS",
    "PROFILE_TOOLS",
    "SYSTEM_TOOLS",
    "MCP_TOOLS",
    "PLAN_TOOLS",
    "WEB_SEARCH_TOOLS",
    "PERSONA_TOOLS",
    "STICKER_TOOLS",
]
