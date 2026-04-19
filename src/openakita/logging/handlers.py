"""
Custom log handlers

Features:
- ErrorOnlyHandler: Only logs ERROR/CRITICAL level messages
- ColoredConsoleHandler: Colored console output
- SessionLogHandler: Per-session log buffering for AI queries
"""

import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import TextIO

from .session_buffer import get_session_log_buffer


class ErrorOnlyHandler(TimedRotatingFileHandler):
    """
    Log handler that only records ERROR and CRITICAL level messages.

    Inherits TimedRotatingFileHandler for daily log rotation.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Only process ERROR and above."""
        if record.levelno >= logging.ERROR:
            super().emit(record)


class ColoredConsoleHandler(logging.StreamHandler):
    """
    Colored console log handler.

    Different log levels use different colors:
    - DEBUG: gray
    - INFO: default
    - WARNING: yellow
    - ERROR: red
    - CRITICAL: bold red

    Windows special handling:
    - Forces UTF-8 encoding on output to prevent GBK encoding from causing
      UnicodeEncodeError with emoji and other Unicode characters, which would
      break SSE streaming and cause blank pages on the frontend.
    """

    # ANSI color codes
    COLORS = {
        logging.DEBUG: "\033[90m",  # gray
        logging.INFO: "\033[0m",  # default
        logging.WARNING: "\033[93m",  # yellow
        logging.ERROR: "\033[91m",  # red
        logging.CRITICAL: "\033[91;1m",  # bold red
    }
    RESET = "\033[0m"

    def __init__(self, stream: TextIO = None):
        output_stream = stream or sys.stdout
        # Belt-and-suspenders: even if _ensure_utf8 has already globally reconfigured stdout,
        # wrap the handler's own stream with UTF-8 to guard against edge cases where logging
        # is initialized before _ensure_utf8 is imported.
        if sys.platform == "win32" and hasattr(output_stream, "buffer"):
            import io

            output_stream = io.TextIOWrapper(
                output_stream.buffer, encoding="utf-8", errors="replace", line_buffering=True
            )
        super().__init__(output_stream)
        # Detect color support (Windows requires special handling)
        self._supports_color = self._check_color_support()

    def _check_color_support(self) -> bool:
        """Detect whether the terminal supports color."""
        # On Windows, try to enable ANSI support
        if sys.platform == "win32":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                # Enable virtual terminal processing
                kernel32.SetConsoleMode(
                    kernel32.GetStdHandle(-11),  # STD_OUTPUT_HANDLE
                    7,  # ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                )
                return True
            except Exception:
                return False

        # Unix/Linux/Mac: supported by default
        return hasattr(self.stream, "isatty") and self.stream.isatty()

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record, ensuring Unicode characters do not cause exceptions."""
        try:
            super().emit(record)
        except UnicodeEncodeError:
            # Last resort: if encoding errors still occur, retry with replace strategy
            try:
                msg = self.format(record)
                safe_msg = msg.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
                self.stream.write(safe_msg + self.terminator)
                self.stream.flush()
            except Exception:
                pass

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record, adding color."""
        message = super().format(record)

        if self._supports_color:
            color = self.COLORS.get(record.levelno, self.RESET)
            return f"{color}{message}{self.RESET}"

        return message


class SessionLogHandler(logging.Handler):
    """
    Session log handler.

    Buffers log records in memory, grouped by session_id, so the AI can query logs
    for the current session.

    Usage:
    1. Pass session_id via extra when logging:
       logger.info("message", extra={"session_id": "telegram_123_..."})

    2. Or pre-set the current session:
       get_session_log_buffer().set_current_session(session_id)
       logger.info("message")  # automatically associated with current session
    """

    def __init__(self, level: int = logging.DEBUG):
        """
        Initialize the session log handler.

        Args:
            level: Minimum log level (default DEBUG, records all levels)
        """
        super().__init__(level)
        self._buffer = get_session_log_buffer()

    def emit(self, record: logging.LogRecord) -> None:
        """
        Process a log record.

        Args:
            record: Log record object
        """
        try:
            # Try to get session_id from extra
            session_id = getattr(record, "session_id", None)

            # Format the message
            message = self.format(record) if self.formatter else record.getMessage()

            # Add to buffer
            self._buffer.add_log(
                level=record.levelname,
                module=record.name,
                message=message,
                session_id=session_id,
                timestamp=datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[
                    :-3
                ],
            )
        except Exception:
            # Log handlers should never raise exceptions
            self.handleError(record)
