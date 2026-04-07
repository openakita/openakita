"""
会话日志缓存

按 session_id 分组存储日志，供 AI 查询当前会话的执行日志。

功能:
- 内存中按 session_id 分组存储
- 每个 session 保留最近 N 条日志（默认 500）
- 全局单例访问
- 线程安全
"""

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class LogEntry:
    """单条日志记录"""

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
    会话日志缓存

    按 session_id 分组存储日志，每个 session 使用 deque 限制最大条数。
    """

    _instance: Optional["SessionLogBuffer"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, max_entries_per_session: int = 500, max_sessions: int = 50):
        """
        初始化会话日志缓存

        Args:
            max_entries_per_session: 每个 session 最多保留的日志条数
            max_sessions: 最多保留多少个 session 的缓冲区
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
        设置当前活跃的 session_id

        Args:
            session_id: 会话 ID
        """
        self._current_session_id = session_id

    def get_current_session(self) -> str | None:
        """获取当前活跃的 session_id"""
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
        添加一条日志

        Args:
            level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
            module: 模块名
            message: 日志消息
            session_id: 会话 ID（如果为 None，使用当前 session 或 _global）
            timestamp: 时间戳（如果为 None，使用当前时间）
        """
        # 确定 session_id
        sid = session_id or self._current_session_id or "_global"

        # 创建日志条目
        entry = LogEntry(
            timestamp=timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            level=level,
            module=module,
            message=message,
            session_id=sid,
        )

        with self._buffer_lock:
            # 确保 session 的 buffer 存在
            if sid not in self._buffers:
                # 超出 session 数上限时，淘汰最旧的非当前 session
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
        获取指定 session 的日志

        Args:
            session_id: 会话 ID（如果为 None，使用当前 session）
            count: 返回的日志条数（默认 20，最大 500）
            level_filter: 过滤日志级别（可选）
            include_global: 是否包含全局日志（默认 True）

        Returns:
            日志列表（最新的在最后）
        """
        sid = session_id or self._current_session_id or "_global"
        count = min(count, self._max_entries)

        logs = []

        with self._buffer_lock:
            # 获取 session 日志
            if sid in self._buffers:
                for entry in self._buffers[sid]:
                    if level_filter and entry.level != level_filter:
                        continue
                    logs.append((entry.timestamp, entry))

            # 如果需要，包含全局日志
            if include_global and sid != "_global" and "_global" in self._buffers:
                for entry in self._buffers["_global"]:
                    if level_filter and entry.level != level_filter:
                        continue
                    logs.append((entry.timestamp, entry))

        # 按时间排序并取最后 count 条
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
        获取格式化的日志文本

        Args:
            session_id: 会话 ID
            count: 返回的日志条数
            level_filter: 过滤日志级别

        Returns:
            格式化的日志文本
        """
        logs = self.get_logs(session_id, count, level_filter)

        if not logs:
            return "暂无日志记录"

        lines = []
        for log in logs:
            lines.append(
                f"[{log['timestamp']}] [{log['level']:7}] {log['module']}: {log['message']}"
            )

        return "\n".join(lines)

    def _evict_oldest_session(self, keep_sid: str) -> None:
        """淘汰最旧的 session 缓冲区（已在 _buffer_lock 内调用）。

        保留 _global 和 keep_sid，淘汰最不活跃的 session。
        """
        protected = {"_global", keep_sid, self._current_session_id or ""}
        candidates = [(sid, buf) for sid, buf in self._buffers.items() if sid not in protected]
        if not candidates:
            return
        # 按 deque 中最后一条日志的时间排序，淘汰最旧的
        oldest_sid = min(
            candidates,
            key=lambda x: x[1][-1].timestamp if x[1] else "",
        )[0]
        del self._buffers[oldest_sid]

    def clear_session(self, session_id: str) -> None:
        """
        清空指定 session 的日志

        Args:
            session_id: 会话 ID
        """
        with self._buffer_lock:
            if session_id in self._buffers:
                self._buffers[session_id].clear()

    def clear_all(self) -> None:
        """清空所有日志"""
        with self._buffer_lock:
            self._buffers.clear()

    def get_stats(self) -> dict:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        with self._buffer_lock:
            return {
                "total_sessions": len(self._buffers),
                "sessions": {sid: len(buf) for sid, buf in self._buffers.items()},
                "current_session": self._current_session_id,
            }


# 全局单例
_session_log_buffer: SessionLogBuffer | None = None


def get_session_log_buffer() -> SessionLogBuffer:
    """获取会话日志缓存单例"""
    global _session_log_buffer
    if _session_log_buffer is None:
        _session_log_buffer = SessionLogBuffer()
    return _session_log_buffer
