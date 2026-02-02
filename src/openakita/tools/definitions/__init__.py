"""
系统工具定义模块

将工具定义从 agent.py 抽离出来，按类别组织。
每个文件定义一类工具，最后统一导出。

结构：
- browser.py      # Browser 工具（10 个）
- filesystem.py   # File System 工具（4 个）
- skills.py       # Skills 工具（7 个）
- memory.py       # Memory 工具（3 个）
- scheduled.py    # Scheduled Tasks 工具（5 个）
- im_channel.py   # IM Channel 工具（4 个）
- profile.py      # User Profile 工具（3 个）
- system.py       # System 工具（3 个）
- mcp.py          # MCP 工具（3 个）
"""

from .browser import BROWSER_TOOLS
from .filesystem import FILESYSTEM_TOOLS
from .skills import SKILLS_TOOLS
from .memory import MEMORY_TOOLS
from .scheduled import SCHEDULED_TOOLS
from .im_channel import IM_CHANNEL_TOOLS
from .profile import PROFILE_TOOLS
from .system import SYSTEM_TOOLS
from .mcp import MCP_TOOLS

# 合并所有工具定义
BASE_TOOLS = (
    FILESYSTEM_TOOLS +
    SKILLS_TOOLS +
    MEMORY_TOOLS +
    BROWSER_TOOLS +
    SCHEDULED_TOOLS +
    IM_CHANNEL_TOOLS +
    SYSTEM_TOOLS +
    PROFILE_TOOLS +
    MCP_TOOLS
)

__all__ = [
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
]
