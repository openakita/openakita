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
    global _initialized
    if _initialized:
        return
    _initialized = True
    t = threading.Thread(
        target=_writer_loop,
        args=(str(db_path),),
        daemon=True,
        name="token-usage-writer",
    )
    t.start()
    logger.info(f"[TokenTracking] Background writer started (db={db_path})")


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
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        ensure_token_usage_schema_sync(conn)
    except Exception as e:
        logger.error(f"[TokenTracking] Failed to open database: {e}")
        return

    batch: list[tuple] = []
    while True:
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


def _flush(conn: sqlite3.Connection, batch: list[tuple]) -> None:
    try:
        conn.executemany(_INSERT_SQL, batch)
        conn.commit()
    except Exception as e:
        logger.warning(f"[TokenTracking] Failed to write {len(batch)} records: {e}")
