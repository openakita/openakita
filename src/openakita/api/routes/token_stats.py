"""
Token usage statistics API endpoints.

GET  /api/stats/tokens/summary   — aggregated stats by dimension
GET  /api/stats/tokens/timeline  — time series for charts
GET  /api/stats/tokens/sessions  — per-session breakdown
GET  /api/stats/tokens/total     — grand total
GET  /api/stats/tokens/context   — current context size + limit
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request

from openakita.storage.database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats/tokens", tags=["token_stats"])

_db_instance: Database | None = None
_db_lock = asyncio.Lock()


async def _get_db() -> Database | None:
    """Lazy-init a shared Database instance for stats queries."""
    global _db_instance
    if _db_instance is not None and _db_instance._connection is not None:
        return _db_instance
    async with _db_lock:
        if _db_instance is not None and _db_instance._connection is not None:
            return _db_instance
        try:
            db = Database()
            await db.connect()
            if db._connection is None:
                logger.error("[TokenStats] Database connect() returned but _connection is None")
                return None
            _db_instance = db
        except Exception as e:
            logger.error(f"[TokenStats] Failed to connect database: {e}")
            return None
    return _db_instance


async def _reset_db() -> None:
    """Reset the cached db instance so next call re-connects."""
    global _db_instance
    async with _db_lock:
        if _db_instance is not None:
            try:
                await _db_instance.close()
            except Exception:
                pass
            _db_instance = None


def _parse_range(
    start: str | None,
    end: str | None,
    period: str | None,
) -> tuple[str, str]:
    """Resolve time range and return as SQLite-compatible UTC timestamp strings.

    SQLite CURRENT_TIMESTAMP stores UTC in 'YYYY-MM-DD HH:MM:SS' format (space separator).
    We must query with the same format and timezone to get correct string comparisons.
    """
    if start and end:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        return s.strftime("%Y-%m-%d %H:%M:%S"), e.strftime("%Y-%m-%d %H:%M:%S")

    now_utc = datetime.now(UTC).replace(tzinfo=None)

    delta_map = {
        "1d": timedelta(days=1),
        "3d": timedelta(days=3),
        "1w": timedelta(weeks=1),
        "1m": timedelta(days=30),
        "6m": timedelta(days=180),
        "1y": timedelta(days=365),
    }
    delta = delta_map.get(period or "1d", timedelta(days=1))
    start_utc = now_utc - delta
    return start_utc.strftime("%Y-%m-%d %H:%M:%S"), now_utc.strftime("%Y-%m-%d %H:%M:%S")


@router.get("/summary")
async def summary(
    request: Request,
    group_by: str = Query("endpoint_name"),
    period: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    endpoint_name: str | None = Query(None),
    operation_type: str | None = Query(None),
):
    db = await _get_db()
    if db is None:
        return {"error": "database not available"}
    start_str, end_str = _parse_range(start, end, period)
    try:
        rows = await db.get_token_usage_summary(
            start_time=start_str,
            end_time=end_str,
            group_by=group_by,
            endpoint_name=endpoint_name,
            operation_type=operation_type,
        )
    except Exception as e:
        logger.error(f"[TokenStats] summary query failed: {e}")
        await _reset_db()
        return {"error": "query failed, connection reset"}
    return {"start": start_str, "end": end_str, "group_by": group_by, "data": rows}


@router.get("/timeline")
async def timeline(
    request: Request,
    interval: str = Query("hour"),
    period: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    endpoint_name: str | None = Query(None),
):
    db = await _get_db()
    if db is None:
        return {"error": "database not available"}
    start_str, end_str = _parse_range(start, end, period)
    try:
        rows = await db.get_token_usage_timeline(
            start_time=start_str,
            end_time=end_str,
            interval=interval,
            endpoint_name=endpoint_name,
        )
    except Exception as e:
        logger.error(f"[TokenStats] timeline query failed: {e}")
        await _reset_db()
        return {"error": "query failed, connection reset"}
    return {"start": start_str, "end": end_str, "interval": interval, "data": rows}


@router.get("/sessions")
async def sessions(
    request: Request,
    period: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
):
    db = await _get_db()
    if db is None:
        return {"error": "database not available"}
    start_str, end_str = _parse_range(start, end, period)
    try:
        rows = await db.get_token_usage_sessions(
            start_time=start_str, end_time=end_str, limit=limit, offset=offset
        )
    except Exception as e:
        logger.error(f"[TokenStats] sessions query failed: {e}")
        await _reset_db()
        return {"error": "query failed, connection reset"}
    return {"start": start_str, "end": end_str, "data": rows}


@router.get("/total")
async def total(
    request: Request,
    period: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    db = await _get_db()
    if db is None:
        return {"error": "database not available"}
    start_str, end_str = _parse_range(start, end, period)
    try:
        row = await db.get_token_usage_total(start_time=start_str, end_time=end_str)
    except Exception as e:
        logger.error(f"[TokenStats] total query failed: {e}")
        await _reset_db()
        return {"error": "query failed, connection reset"}
    return {"start": start_str, "end": end_str, "data": row}


@router.get("/context")
async def context(request: Request):
    """Return the current session's context token usage and limit."""
    agent = getattr(request.app.state, "agent", None)
    actual = getattr(agent, "_local_agent", agent) if agent else None
    if actual is None:
        return {"error": "agent not available"}

    try:
        re = getattr(actual, "reasoning_engine", None)
        ctx_mgr = getattr(actual, "context_manager", None) or getattr(re, "_context_manager", None)
        if ctx_mgr and hasattr(ctx_mgr, "get_max_context_tokens"):
            max_ctx = ctx_mgr.get_max_context_tokens()
            messages = getattr(re, "_last_working_messages", None) or getattr(
                getattr(actual, "_context", None), "messages", []
            )
            cur_ctx = ctx_mgr.estimate_messages_tokens(messages) if messages else 0
            return {
                "context_tokens": cur_ctx,
                "context_limit": max_ctx,
                "percent": round(cur_ctx / max_ctx * 100, 1) if max_ctx else 0,
            }
    except Exception as e:
        logger.warning(f"[TokenStats] Failed to get context size: {e}")

    return {"context_tokens": 0, "context_limit": 0, "percent": 0}
