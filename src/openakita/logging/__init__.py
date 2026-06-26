"""
OpenAkita 日志系统

功能:
- 日志文件输出（按天轮转 + 按大小轮转）
- 分离 error.log（只记录 ERROR/CRITICAL）
- 自动清理过期日志
- 支持控制台彩色输出
- 会话级日志缓存（供 AI 查询）
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
