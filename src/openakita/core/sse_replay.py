"""C17 Phase B — SSE Last-Event-ID 续传 + per-session ringbuffer。

设计目标
========

OpenAkita 的 ``/api/chat`` 走 SSE 流式回复。前端 / Tauri / IM gateway 在
网络抖动（移动网络切换、VPN 重连、Nginx idle timeout）后断开连接，重新
``fetch /api/chat`` 一次就会丢掉断点前后生成的 ``text_delta`` / ``tool_call_start``
事件。这不是数据丢失问题——后端日志里都在——而是 UI 上看起来"答了一半就消失"。

C17 引入 SSE 标准的 ``Last-Event-ID`` 头 + ``id:`` 行机制，把断点续传的能力
推给客户端：

1. 服务端每条 SSE event 加一行 ``id: <seq>``（单调递增 per-session）。
2. 服务端在内存里维护 :class:`SSESession`，用 ``deque(maxlen=100)``
   按时间窗口保留最近事件（默认 5 分钟 TTL）。
3. 客户端在 ``fetch`` 时通过 ``Last-Event-ID`` 头声明"我已经看到 seq=42"，
   服务端从 ``ringbuffer.replay_from(42)`` 把 43..N 重发一遍，**之后**才接
   active stream，对客户端表现为"无缝续传"。
4. 客户端用 ``seenSequenceNums: Set<number>`` dedup，防止 active 流和
   replay 出现重叠时同一个 delta 被消费两次。

业界证据
========

- claude-code ``SSETransport.ts``: ``Last-Event-ID`` + sequence + replay。
- claude-code ``print.ts``: 客户端 ``seenSequenceNums`` dedup。
- QwenPaw ``TaskTracker``: per-session 内存 ringbuffer + GC + multi-subscriber。

并发与边界
==========

- :class:`SSESession` 内部 ``threading.Lock`` 保护 deque + seq counter。
- :class:`SSESessionRegistry` 全局 cap ``MAX_SESSIONS = 1024``——超过时
  LRU evict 最久 idle 的 session（防止 memory growth 攻击）。
- :class:`SSESessionRegistry` 自带 GC: ``gc_idle_sessions(now=)`` 把
  ``last_activity`` 比 ``ttl_s`` 早的 session 整个清掉。
- :func:`SSESession.replay_from`: ``last_seq`` 在 ringbuffer 范围外（太老
  → 已 evict / 太新 → 还没到）时返回**空列表**而非抛异常，让客户端从
  active 流自然续上；老 seq 的丢失会在 UI 表现为缺一段，但比强制重新
  全量回放对用户更友好。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables — module-level so tests can monkeypatch / pyproject can override.
# ---------------------------------------------------------------------------

DEFAULT_MAXLEN: int = 100
DEFAULT_TTL_SECONDS: float = 300.0  # 5 min idle → GC
MAX_SESSIONS: int = 1024  # cap on per-process active SSE sessions


@dataclass(frozen=True)
class SSEEvent:
    """A single SSE event captured in the ringbuffer.

    Frozen so callers can hand the same instance to multiple subscribers
    without worrying about mutation.
    """

    seq: int
    event_type: str
    payload: dict[str, Any]
    ts: float


@dataclass
class SSESession:
    """Per-session SSE state — ringbuffer + monotonic sequence + lock.

    Use via :class:`SSESessionRegistry`. Direct instantiation is allowed
    for tests / specialised replay scenarios.
    """

    session_id: str
    maxlen: int = DEFAULT_MAXLEN
    ttl_s: float = DEFAULT_TTL_SECONDS
    _events: deque[SSEEvent] = field(default_factory=deque, repr=False)
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_activity: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        # ``deque`` doesn't accept a maxlen from a default_factory; bind it
        # after construction so we still get the configurable maxlen.
        if self._events.maxlen != self.maxlen:
            self._events = deque(self._events, maxlen=self.maxlen)

    # ------------------------------------------------------------------
    # Event ingest
    # ------------------------------------------------------------------

    def add_event(self, event_type: str, payload: dict[str, Any] | None) -> SSEEvent:
        """Append an event with the next monotonic seq and return it."""
        with self._lock:
            self._seq += 1
            evt = SSEEvent(
                seq=self._seq,
                event_type=event_type,
                payload=dict(payload or {}),
                ts=time.time(),
            )
            self._events.append(evt)
            self._last_activity = evt.ts
            return evt

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay_from(self, last_seq: int | None) -> list[SSEEvent]:
        """Return all buffered events with ``seq > last_seq``.

        - ``last_seq=None`` or ``< 0`` → empty (no replay requested).
        - ``last_seq == 0`` → every event still in the buffer (client has
          seen nothing yet).
        - ``last_seq`` newer than ``_seq`` → empty (client claims a future
          seq; bug, but tolerate by not replaying anything).
        - ``last_seq`` older than the buffer's oldest seq → return whatever
          we still have (gap will be visible to the user).

        Caller still needs to send these in order **before** any new live
        events to preserve ordering guarantees.
        """
        if last_seq is None or last_seq < 0:
            return []
        with self._lock:
            if last_seq >= self._seq:
                return []
            return [e for e in self._events if e.seq > last_seq]

    # ------------------------------------------------------------------
    # GC
    # ------------------------------------------------------------------

    def is_idle(self, now: float | None = None) -> bool:
        """True if the session has had no activity within ``ttl_s``."""
        now = now if now is not None else time.time()
        with self._lock:
            return (now - self._last_activity) > self.ttl_s

    @property
    def current_seq(self) -> int:
        with self._lock:
            return self._seq

    @property
    def last_activity(self) -> float:
        with self._lock:
            return self._last_activity

    def __len__(self) -> int:  # noqa: D401 - len of buffered events
        with self._lock:
            return len(self._events)


class SSESessionRegistry:
    """Process-wide registry of :class:`SSESession`.

    Use the module-level :func:`get_registry` / :func:`reset_registry_for_testing`
    helpers; direct instantiation is for tests only.
    """

    def __init__(
        self,
        *,
        max_sessions: int = MAX_SESSIONS,
        default_maxlen: int = DEFAULT_MAXLEN,
        default_ttl_s: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        # OrderedDict keeps insertion / access order so we can LRU-evict on overflow.
        self._sessions: OrderedDict[str, SSESession] = OrderedDict()
        self._lock = threading.Lock()
        self._max_sessions = max_sessions
        self._default_maxlen = default_maxlen
        self._default_ttl_s = default_ttl_s

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_or_create(self, session_id: str) -> SSESession:
        """Return (or create) the session for ``session_id``.

        Moves the entry to the end of the LRU on access; new sessions are
        evicted from the head when ``max_sessions`` overflows.
        """
        if not session_id:
            raise ValueError("session_id must be non-empty")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = SSESession(
                    session_id=session_id,
                    maxlen=self._default_maxlen,
                    ttl_s=self._default_ttl_s,
                )
                self._sessions[session_id] = session
                self._evict_overflow_locked()
            else:
                # Touch — LRU update on access keeps active sessions alive.
                self._sessions.move_to_end(session_id)
            return session

    def get(self, session_id: str) -> SSESession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                self._sessions.move_to_end(session_id)
            return session

    # ------------------------------------------------------------------
    # GC
    # ------------------------------------------------------------------

    def gc_idle_sessions(self, *, now: float | None = None) -> int:
        """Remove sessions idle longer than ``ttl_s``. Return count removed."""
        removed = 0
        now = now if now is not None else time.time()
        with self._lock:
            # Iterate over a copy to safely mutate during iteration.
            for sid, session in list(self._sessions.items()):
                if session.is_idle(now=now):
                    self._sessions.pop(sid, None)
                    removed += 1
        if removed:
            logger.debug("[sse_replay] GC removed %d idle session(s)", removed)
        return removed

    def _evict_overflow_locked(self) -> None:
        """Called under ``_lock``; evict oldest entries until under cap."""
        while len(self._sessions) > self._max_sessions:
            evicted_id, _ = self._sessions.popitem(last=False)
            logger.warning(
                "[sse_replay] LRU-evicted session %s (cap=%d reached)",
                evicted_id,
                self._max_sessions,
            )

    # ------------------------------------------------------------------
    # Diagnostics / test helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def session_ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_registry_lock = threading.Lock()
_registry: SSESessionRegistry | None = None


def get_registry() -> SSESessionRegistry:
    """Lazy-init the process-wide SSE session registry."""
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = SSESessionRegistry()
    return _registry


def reset_registry_for_testing() -> None:
    """Test-only: drop the singleton so each test gets a fresh registry."""
    global _registry
    with _registry_lock:
        _registry = None


# ---------------------------------------------------------------------------
# Wire-format helpers
# ---------------------------------------------------------------------------


def parse_last_event_id(header_value: str | None) -> int | None:
    """Parse the ``Last-Event-ID`` HTTP header into an int seq.

    Returns ``None`` for missing / malformed / non-positive values. The
    SSE spec says the header is a free-form string; we use simple int
    sequences and reject anything else (rather than reset to 0 silently).
    """
    if not header_value:
        return None
    try:
        parsed = int(header_value.strip())
    except (TypeError, ValueError):
        logger.debug(
            "[sse_replay] ignoring malformed Last-Event-ID header: %r",
            header_value,
        )
        return None
    return parsed if parsed > 0 else None


def format_sse_frame(event: SSEEvent, *, data_json: str) -> str:
    """Format a single event as an SSE frame with ``id:`` + ``data:`` lines.

    ``data_json`` is the already-serialized JSON payload — caller owns
    JSON encoding so this stays cheap and side-effect-free.

    NOTE: standard SSE frames end with ``\\n\\n``; that's what most
    parsers expect. We deliberately keep ``event:`` line out unless the
    caller really needs it — most OpenAkita clients dispatch on the
    ``type`` inside the JSON payload, not on the SSE event_type line.
    """
    return f"id: {event.seq}\ndata: {data_json}\n\n"


__all__ = [
    "DEFAULT_MAXLEN",
    "DEFAULT_TTL_SECONDS",
    "MAX_SESSIONS",
    "SSEEvent",
    "SSESession",
    "SSESessionRegistry",
    "format_sse_frame",
    "get_registry",
    "parse_last_event_id",
    "reset_registry_for_testing",
]
