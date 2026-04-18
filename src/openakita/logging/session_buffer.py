"""
Session Log Buffer

Stores logs grouped by session_id for AI to query execution logs of the current session.

Features:
- In-memory storage grouped by session_id
- Each session retains the most recent N log entries (default 500)
- Global singleton access
- Thread-safe
"""

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class LogEntry:
    """A single log record."""

    timestamp: str
    level: str
    module: str
    message: str
    session_id: str = "_global"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "module": self.module,
            "message": self.message,
            "session_id": self.session_id,
        }

    def __str__(self) -> str:
        return f"[{self.timestamp}] [{self.level}] {self.module}: {self.message}"


class SessionLogBuffer:
    """
    Session log buffer.

    Stores logs grouped by session_id, using a deque per session to cap the maximum entry count.
    """

    _instance: Optional["SessionLogBuffer"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_entries_per_session: int = 500, max_sessions: int = 50):
        """
        Initialize the session log buffer.

        Args:
            max_entries_per_session: Maximum log entries retained per session
            max_sessions: Maximum number of session buffers to retain
        """
        if self._initialized:
            return

        self._max_entries = max_entries_per_session
        self._max_sessions = max_sessions
        self._buffers: dict[str, deque[LogEntry]] = {}
        self._buffer_lock = threading.Lock()
        self._current_session_id: str | None = None
        self._initialized = True

    def set_current_session(self, session_id: str) -> None:
        """
        Set the currently active session_id.

        Args:
            session_id: Session ID
        """
        self._current_session_id = session_id

    def get_current_session(self) -> str | None:
        """Get the currently active session_id."""
        return self._current_session_id

    def add_log(
        self,
        level: str,
        module: str,
        message: str,
        session_id: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        """
        Add a log entry.

        Args:
            level: Log level (DEBUG/INFO/WARNING/ERROR/CRITICAL)
            module: Module name
            message: Log message
            session_id: Session ID (defaults to current session or _global if None)
            timestamp: Timestamp (defaults to current time if None)
        """
        # Determine session_id
        sid = session_id or self._current_session_id or "_global"

        # Create log entry
        entry = LogEntry(
            timestamp=timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            level=level,
            module=module,
            message=message,
            session_id=sid,
        )

        with self._buffer_lock:
            # Ensure the session buffer exists
            if sid not in self._buffers:
                # When session count exceeds the limit, evict the oldest non-current session
                if len(self._buffers) >= self._max_sessions:
                    self._evict_oldest_session(sid)
                self._buffers[sid] = deque(maxlen=self._max_entries)

            self._buffers[sid].append(entry)

    def get_logs(
        self,
        session_id: str | None = None,
        count: int = 20,
        level_filter: str | None = None,
        include_global: bool = True,
    ) -> list[dict]:
        """
        Get logs for a specified session.

        Args:
            session_id: Session ID (defaults to current session if None)
            count: Number of log entries to return (default 20, max 500)
            level_filter: Filter by log level (optional)
            include_global: Whether to include global logs (default True)

        Returns:
            List of log entries (most recent last)
        """
        sid = session_id or self._current_session_id or "_global"
        count = min(count, self._max_entries)

        logs = []

        with self._buffer_lock:
            # Retrieve session logs
            if sid in self._buffers:
                for entry in self._buffers[sid]:
                    if level_filter and entry.level != level_filter:
                        continue
                    logs.append((entry.timestamp, entry))

            # Include global logs if requested
            if include_global and sid != "_global" and "_global" in self._buffers:
                for entry in self._buffers["_global"]:
                    if level_filter and entry.level != level_filter:
                        continue
                    logs.append((entry.timestamp, entry))

        # Sort by timestamp and take the last count entries
        logs.sort(key=lambda x: x[0])
        result = [entry.to_dict() for _, entry in logs[-count:]]

        return result

    def get_logs_formatted(
        self,
        session_id: str | None = None,
        count: int = 20,
        level_filter: str | None = None,
    ) -> str:
        """
        Get formatted log text.

        Args:
            session_id: Session ID
            count: Number of log entries to return
            level_filter: Filter by log level

        Returns:
            Formatted log text
        """
        logs = self.get_logs(session_id, count, level_filter)

        if not logs:
            return "No log entries available"

        lines = []
        for log in logs:
            lines.append(
                f"[{log['timestamp']}] [{log['level']:7}] {log['module']}: {log['message']}"
            )

        return "\n".join(lines)

    def _evict_oldest_session(self, keep_sid: str) -> None:
        """Evict the oldest session buffer (called within _buffer_lock).

        Preserves _global and keep_sid; evicts the least recently active session.
        """
        protected = {"_global", keep_sid, self._current_session_id or ""}
        candidates = [(sid, buf) for sid, buf in self._buffers.items() if sid not in protected]
        if not candidates:
            return
        # Sort by the timestamp of the last log entry in each deque; evict the oldest
        oldest_sid = min(
            candidates,
            key=lambda x: x[1][-1].timestamp if x[1] else "",
        )[0]
        del self._buffers[oldest_sid]

    def clear_session(self, session_id: str) -> None:
        """
        Clear logs for a specified session.

        Args:
            session_id: Session ID
        """
        with self._buffer_lock:
            if session_id in self._buffers:
                self._buffers[session_id].clear()

    def clear_all(self) -> None:
        """Clear all logs."""
        with self._buffer_lock:
            self._buffers.clear()

    def get_stats(self) -> dict:
        """
        Get statistics.

        Returns:
            Statistics dictionary
        """
        with self._buffer_lock:
            return {
                "total_sessions": len(self._buffers),
                "sessions": {sid: len(buf) for sid, buf in self._buffers.items()},
                "current_session": self._current_session_id,
            }


# Global singleton
_session_log_buffer: SessionLogBuffer | None = None


def get_session_log_buffer() -> SessionLogBuffer:
    """Get the session log buffer singleton."""
    global _session_log_buffer
    if _session_log_buffer is None:
        _session_log_buffer = SessionLogBuffer()
    return _session_log_buffer
