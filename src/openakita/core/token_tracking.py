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
    operation_type: str = "unknown"
    operation_detail: str = ""
    channel: str = ""
    user_id: str = ""
    iteration: int = 0


_tracking_ctx: contextvars.ContextVar[TokenTrackingContext | None] = contextvars.ContextVar(
    "token_tracking_ctx", default=None
)


def set_tracking_context(ctx: TokenTrackingContext) -> contextvars.Token:
    return _tracking_ctx.set(ctx)


def get_tracking_context() -> TokenTrackingContext | None:
    return _tracking_ctx.get()


def reset_tracking_context(token: contextvars.Token) -> None:
    _tracking_ctx.reset(token)


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
) -> None:
    """将一次 LLM 调用的 token 用量投递到写入队列（非阻塞）。"""
    if not _initialized:
        return
    ctx = _tracking_ctx.get()
    _write_queue.put({
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
    })


# ──────────────────────── 后台写入实现 ────────────────────────

_INSERT_SQL = """
INSERT INTO token_usage (
    session_id, endpoint_name, model, operation_type, operation_detail,
    input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
    context_tokens, iteration, channel, user_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_COLUMN_ORDER = (
    "session_id", "endpoint_name", "model", "operation_type", "operation_detail",
    "input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens",
    "context_tokens", "iteration", "channel", "user_id",
)


def _writer_loop(db_path: str) -> None:
    """后台守护线程主循环：批量写入 token_usage 记录。"""
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
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
