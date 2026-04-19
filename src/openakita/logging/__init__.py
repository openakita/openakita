"""
OpenAkita logging system

Features:
- Log file output (daily rotation + size-based rotation)
- Separate error.log (ERROR/CRITICAL only)
- Automatic cleanup of expired logs
- Console color output support
- Session-level log buffer (for AI queries)
"""

from .cleaner import LogCleaner
from .config import get_logger, setup_logging
from .session_buffer import SessionLogBuffer, get_session_log_buffer

__all__ = [
    "setup_logging",
    "get_logger",
    "LogCleaner",
    "SessionLogBuffer",
    "get_session_log_buffer",
]
