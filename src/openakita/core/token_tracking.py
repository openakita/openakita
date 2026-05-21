"""
Token 用量追踪：contextvars 上下文 + 后台写入线程。

架构：
- 上层调用方（ReasoningEngine / Agent / ContextManager 等）在发起 LLM 调用前
  通过 set_tracking_context() 设置本次调用的元数据（session_id / operation_type …）。
- Brain.messages_create / messages_create_async 在拿到响应后调用 record_usage()，
  该函数读取 contextvars 中的元数据并投递到写入队列。
- 后台守护线程 (_writer_loop) 持有独立的 sqlite3 同步连接，批量 flush 队列中的记录。
"""

from __future__ import annotations

import contextvars
import logging
import queue
import sqlite3
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ──────────────────────── 健康状态 ────────────────────────
#
# 后台写入线程崩溃后我们必须做两件事：
#   (a) 暴露给 /api/health 让用户看见降级；
#   (b) 拒收 record_usage() 的入队，避免 _write_queue 在 writer 死后
#       无界堆积（每次 LLM 调用都会丢一条进去）。
#
# 这里用 threading.Event 让 daemon thread 和主线程之间不需要再过 lock，
# 同时方便测试通过 `_writer_dead.is_set()` 断言状态。

_writer_dead = threading.Event()
_drop_warned = threading.Event()
_writer_stop = threading.Event()
_writer_thread: threading.Thread | None = None


# ──────────────────────── contextvars ────────────────────────


@dataclass
class TokenTrackingContext:
    session_id: str = ""
    request_id: str = ""
    turn_id: str = ""
    operation_type: str = "unknown"
    operation_detail: str = ""
    channel: str = ""
    user_id: str = ""
    iteration: int = 0
    agent_profile_id: str = "default"


@dataclass
class TokenBudgetState:
    """Mutable token budget shared by nested background LLM calls."""

    name: str
    max_tokens: int
    used_tokens: int = 0
    exceeded: bool = False

    def record(self, tokens: int) -> None:
        if self.max_tokens <= 0 or tokens <= 0:
            return
        self.used_tokens += tokens
        if self.used_tokens >= self.max_tokens:
            self.exceeded = True

    @property
    def remaining_tokens(self) -> int:
        if self.max_tokens <= 0:
            return 0
        return max(0, self.max_tokens - self.used_tokens)


_tracking_ctx: contextvars.ContextVar[TokenTrackingContext | None] = contextvars.ContextVar(
    "token_tracking_ctx", default=None
)
_token_budget_ctx: contextvars.ContextVar[TokenBudgetState | None] = contextvars.ContextVar(
    "token_budget_ctx", default=None
)


def set_tracking_context(ctx: TokenTrackingContext) -> contextvars.Token:
    return _tracking_ctx.set(ctx)


def get_tracking_context() -> TokenTrackingContext | None:
    return _tracking_ctx.get()


def reset_tracking_context(token: contextvars.Token) -> None:
    _tracking_ctx.reset(token)


def set_token_budget(state: TokenBudgetState | None) -> contextvars.Token:
    return _token_budget_ctx.set(state)


def get_token_budget() -> TokenBudgetState | None:
    return _token_budget_ctx.get()


def reset_token_budget(token: contextvars.Token) -> None:
    _token_budget_ctx.reset(token)


def token_budget_exceeded() -> bool:
    state = _token_budget_ctx.get()
    return bool(state and state.exceeded)


def token_budget_status() -> dict:
    state = _token_budget_ctx.get()
    if not state:
        return {"enabled": False, "name": "", "used_tokens": 0, "max_tokens": 0, "exceeded": False}
    return {
        "enabled": state.max_tokens > 0,
        "name": state.name,
        "used_tokens": state.used_tokens,
        "max_tokens": state.max_tokens,
        "remaining_tokens": state.remaining_tokens,
        "exceeded": state.exceeded,
    }


# ──────────────────────── 写入队列 & 后台线程 ────────────────────────

_write_queue: queue.Queue = queue.Queue()
_initialized = False


def init_token_tracking(db_path: str) -> None:
    """启动后台写入线程。在应用启动时调用一次。"""
    global _initialized, _writer_thread
    if _initialized:
        return
    _initialized = True
    _writer_stop.clear()
    _writer_dead.clear()
    _drop_warned.clear()
    _writer_thread = threading.Thread(
        target=_writer_loop,
        args=(str(db_path),),
        daemon=True,
        name="token-usage-writer",
    )
    _writer_thread.start()
    logger.info(f"[TokenTracking] Background writer started (db={db_path})")


def shutdown_token_tracking(timeout: float = 5.0) -> None:
    """Stop the background writer and wait for it to drain (idempotent).

    Used by the quarantine flow before renaming ``agent.db`` so the
    writer doesn't hold the file open on Windows. Safe to call multiple
    times.
    """
    global _writer_thread, _initialized
    if not _initialized:
        return
    _writer_stop.set()
    thread = _writer_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    _writer_thread = None
    _initialized = False
    _writer_dead.set()


def record_usage(
    *,
    model: str = "",
    endpoint_name: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    context_tokens: int = 0,
    estimated_cost: float = 0.0,
) -> None:
    """将一次 LLM 调用的 token 用量投递到写入队列（非阻塞）。"""
    budget = _token_budget_ctx.get()
    if budget:
        budget.record(
            input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens
        )
        if budget.exceeded:
            logger.info(
                "[TokenBudget] %s reached budget: used=%s max=%s",
                budget.name,
                budget.used_tokens,
                budget.max_tokens,
            )
    if not _initialized:
        return
    if _writer_dead.is_set():
        # Writer thread already gave up (DB corruption / disk full / etc).
        # Drop the record silently — but warn once so it shows up in logs.
        if not _drop_warned.is_set():
            _drop_warned.set()
            logger.warning(
                "[TokenTracking] dropping new records — writer thread is dead"
            )
        return
    ctx = _tracking_ctx.get()
    _write_queue.put(
        {
            "session_id": ctx.session_id if ctx else "",
            "request_id": ctx.request_id if ctx else "",
            "turn_id": ctx.turn_id if ctx else "",
            "endpoint_name": endpoint_name,
            "model": model,
            "operation_type": ctx.operation_type if ctx else "unknown",
            "operation_detail": ctx.operation_detail if ctx else "",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_read_tokens": cache_read_tokens,
            "context_tokens": context_tokens,
            "iteration": ctx.iteration if ctx else 0,
            "channel": ctx.channel if ctx else "",
            "user_id": ctx.user_id if ctx else "",
            "agent_profile_id": ctx.agent_profile_id if ctx else "default",
            "estimated_cost": estimated_cost,
        }
    )


# ──────────────────────── 后台写入实现 ────────────────────────

_INSERT_SQL = """
INSERT INTO token_usage (
    session_id, request_id, turn_id, endpoint_name, model, operation_type, operation_detail,
    input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
    context_tokens, iteration, channel, user_id, agent_profile_id, estimated_cost
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_COLUMN_ORDER = (
    "session_id",
    "request_id",
    "turn_id",
    "endpoint_name",
    "model",
    "operation_type",
    "operation_detail",
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "context_tokens",
    "iteration",
    "channel",
    "user_id",
    "agent_profile_id",
    "estimated_cost",
)


def ensure_token_usage_schema_sync(conn: sqlite3.Connection) -> None:
    """Create/migrate token_usage before indexes reference newly added columns."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            session_id TEXT,
            request_id TEXT DEFAULT '',
            turn_id TEXT DEFAULT '',
            endpoint_name TEXT,
            model TEXT,
            operation_type TEXT,
            operation_detail TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            context_tokens INTEGER DEFAULT 0,
            iteration INTEGER DEFAULT 0,
            channel TEXT,
            user_id TEXT,
            agent_profile_id TEXT DEFAULT 'default',
            estimated_cost REAL DEFAULT 0
        );
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(token_usage)").fetchall()}
    required_columns = {
        "request_id": "TEXT DEFAULT ''",
        "turn_id": "TEXT DEFAULT ''",
        "agent_profile_id": "TEXT DEFAULT 'default'",
        "estimated_cost": "REAL DEFAULT 0",
    }
    for column, ddl in required_columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE token_usage ADD COLUMN {column} {ddl}")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_token_usage_ts ON token_usage(timestamp);
        CREATE INDEX IF NOT EXISTS idx_token_usage_session ON token_usage(session_id);
        CREATE INDEX IF NOT EXISTS idx_token_usage_request ON token_usage(request_id);
        CREATE INDEX IF NOT EXISTS idx_token_usage_endpoint ON token_usage(endpoint_name);
        CREATE INDEX IF NOT EXISTS idx_token_usage_op ON token_usage(operation_type);
    """)
    conn.commit()


def _writer_loop(db_path: str) -> None:
    """后台守护线程主循环：批量写入 token_usage 记录。"""
    from openakita.storage.degraded import registry as _degraded_registry
    from openakita.storage.safe_sqlite import SQLiteUnavailable, safe_open_sync

    try:
        conn = safe_open_sync(
            db_path,
            want_wal=True,
            run_quick_check=True,
            foreign_keys=False,
            check_same_thread=False,
        )
        ensure_token_usage_schema_sync(conn)
    except SQLiteUnavailable as e:
        # Database is unrecoverable for this process — flag the writer as
        # dead so record_usage() stops feeding _write_queue. Registering
        # degraded lets /api/health surface a banner to the user.
        logger.error(
            "[TokenTracking] disabled: reason=%s details=%s",
            e.reason,
            e.details or "",
        )
        _writer_dead.set()
        _degraded_registry.register(
            "token_tracking",
            e.reason,
            repair="quarantine_token_db",
            details=e.details or None,
        )
        return
    except Exception as e:  # noqa: BLE001 - last-line guard
        logger.error(f"[TokenTracking] Failed to open database: {e}")
        _writer_dead.set()
        _degraded_registry.register(
            "token_tracking",
            "open_failed",
            repair="quarantine_token_db",
            details=str(e)[:200],
        )
        return

    batch: list[tuple] = []
    try:
        while not _writer_stop.is_set():
            try:
                data = _write_queue.get(timeout=2.0)
            except queue.Empty:
                if batch:
                    _flush(conn, batch)
                    batch.clear()
                continue

            row = tuple(data[col] for col in _COLUMN_ORDER)
            batch.append(row)

            if len(batch) >= 10:
                _flush(conn, batch)
                batch.clear()
        # Drain pending batch on graceful shutdown.
        if batch:
            _flush(conn, batch)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _flush(conn: sqlite3.Connection, batch: list[tuple]) -> None:
    try:
        conn.executemany(_INSERT_SQL, batch)
        conn.commit()
    except Exception as e:
        logger.warning(f"[TokenTracking] Failed to write {len(batch)} records: {e}")
