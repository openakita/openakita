"""
Token usage tracking: contextvars context + background writer thread.

Architecture:
- Upper-layer callers (ReasoningEngine / Agent / ContextManager etc.) call
  set_tracking_context() before making an LLM request to set metadata
  (session_id / operation_type …).
- Brain.messages_create / messages_create_async call record_usage() after receiving
  a response; this reads contextvars metadata and enqueues a write.
- A background daemon thread (_writer_loop) holds an independent sqlite3 sync
  connection and batch-flushes queued records.
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
    operation_type: str = "unknown"
    operation_detail: str = ""
    channel: str = ""
    user_id: str = ""
    iteration: int = 0
    agent_profile_id: str = "default"


_tracking_ctx: contextvars.ContextVar[TokenTrackingContext | None] = contextvars.ContextVar(
    "token_tracking_ctx", default=None
)


def set_tracking_context(ctx: TokenTrackingContext) -> contextvars.Token:
    return _tracking_ctx.set(ctx)


def get_tracking_context() -> TokenTrackingContext | None:
    return _tracking_ctx.get()


def reset_tracking_context(token: contextvars.Token) -> None:
    _tracking_ctx.reset(token)


# ──────────────────────── Write queue & background thread ────────────────────────

_write_queue: queue.Queue = queue.Queue()
_initialized = False


def init_token_tracking(db_path: str) -> None:
    """Start the background writer thread. Call once at application startup."""
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
    """Enqueue token usage from one LLM call into the write queue (non-blocking)."""
    if not _initialized:
        return
    ctx = _tracking_ctx.get()
    _write_queue.put(
        {
            "session_id": ctx.session_id if ctx else "",
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


# ──────────────────────── Background writer implementation ────────────────────────

_INSERT_SQL = """
INSERT INTO token_usage (
    session_id, endpoint_name, model, operation_type, operation_detail,
    input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
    context_tokens, iteration, channel, user_id, agent_profile_id, estimated_cost
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_COLUMN_ORDER = (
    "session_id",
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


def _writer_loop(db_path: str) -> None:
    """Background daemon thread main loop: batch-write token_usage records."""
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                session_id TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_token_usage_ts ON token_usage(timestamp);
            CREATE INDEX IF NOT EXISTS idx_token_usage_session ON token_usage(session_id);
            CREATE INDEX IF NOT EXISTS idx_token_usage_endpoint ON token_usage(endpoint_name);
        """)
        # Migration: add estimated_cost column for legacy databases
        try:
            conn.execute("ALTER TABLE token_usage ADD COLUMN estimated_cost REAL DEFAULT 0")
            conn.commit()
        except Exception:
            pass  # Column already exists; ignore
        # Migration: add agent_profile_id column for legacy databases
        try:
            conn.execute(
                "ALTER TABLE token_usage ADD COLUMN agent_profile_id TEXT DEFAULT 'default'"
            )
            conn.commit()
        except Exception:
            pass  # Column already exists; ignore
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
