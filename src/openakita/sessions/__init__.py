"""
会话管理模块

提供统一的会话管理能力:
- Session: 会话对象，包含上下文和配置
- SessionManager: 会话生命周期管理
- UserManager: 跨平台用户管理
"""

from .manager import SessionManager
from .session import Session, SessionConfig, SessionContext, SessionState, TaskCheckpoint
from .user import User, UserManager

__all__ = [
    "Session",
    "SessionState",
    "SessionContext",
    "SessionConfig",
    "SessionManager",
    "TaskCheckpoint",
    "User",
    "UserManager",
]

