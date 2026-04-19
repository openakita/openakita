"""
Session management module

Provides unified session management:
- Session: Session object containing context and configuration
- SessionManager: Session lifecycle management
- UserManager: Cross-platform user management
"""

from .manager import SessionManager
from .session import Session, SessionConfig, SessionContext, SessionState
from .user import User, UserManager

__all__ = [
    "Session",
    "SessionState",
    "SessionContext",
    "SessionConfig",
    "SessionManager",
    "User",
    "UserManager",
]
