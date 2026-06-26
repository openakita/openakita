"""
Token usage statistics API endpoints.

GET  /api/stats/tokens/summary   — aggregated stats by dimension
GET  /api/stats/tokens/timeline  — time series for charts
GET  /api/stats/tokens/sessions  — per-session breakdown
GET  /api/stats/tokens/records   — recent attributed requests
GET  /api/stats/tokens/total     — grand total
GET  /api/stats/tokens/context   — current context size + limit
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query, Request

from openakita.config import settings
from openakita.core.context_stats import get_context_snapshot
from openakita.storage.database import Database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats/tokens", tags=["token_stats"])

_last_db_error: dict[str, str] = {}


def _db_unavailable_payload() -> dict[str, object]:
    return {
        "error": "database not available",
        "diagnostic": {
            "db_path": str(settings.db_full_path),
            "stage": _last_db_error.get("stage", "unknown"),
            "exception_type": _last_db_error.get("exception_type", ""),
            "message": _last_db_error.get("message", ""),
        },
    }


async def _get_db() -> Database | None:
    """Open a short-lived Database instance for one stats request.

    ``aiosqlite`` owns a non-daemon worker thread for each open connection.
    Keeping a module-level connection cached makes ASGITransport-based tests
    hang at interpreter shutdown because those tests do not reliably run the
    FastAPI shutdown hooks. Use per-request connections instead so every route
    can close its worker thread in a local ``finally`` block.
    """
    try:
        db = Database()
        await db.connect()
        if db._connection is None:
            logger.error("[TokenStats] Database connect() returned but _connection is None")
            _last_db_error.update(
                {
                    "stage": "post_connect",
                    "exception_type": "NoConnection",
                    "message": "Database.connect() completed but connection is None",
                }
            )
            _register_degraded_token_stats(
                "no_connection",
                "Database.connect() returned but connection is None",
            )
            return None
        _last_db_error.clear()
        return db
    except Exception as e:
        logger.error(f"[TokenStats] Failed to connect database: {e}")
        _last_db_error.update(
            {
                "stage": "connect",
                "exception_type": type(e).__name__,
                "message": str(e)[:500],
            }
        )
        # Bubble the failure into the cross-subsystem registry so the
        # unified ``DegradedBanner`` reflects token_stats outages,
        # not just the daemon-thread writer (token_tracking) ones.
        # We map both `Database` and `SQLiteUnavailable` failures to
        # the same key ``token_tracking`` because the underlying
        # ``agent.db`` is shared; quarantining one cures the other.
        _register_degraded_token_stats(
            _classify_db_error(e),
            str(e)[:200],
        )
        return None


def _classify_db_error(exc: BaseException) -> str:
    try:
        from openakita.storage.safe_sqlite import SQLiteUnavailable

        if isinstance(exc, SQLiteUnavailable):
            return exc.reason
    except Exception:
        pass
    return "open_failed"


def _register_degraded_token_stats(reason: str, details: str) -> None:
    try:
        from openakita.storage.degraded import registry as _degraded

        _degraded.register(
            "token_tracking",
            reason or "unknown",
            repair="quarantine_token_db",
            details=details[:200] if details else None,
        )
    except Exception:
        pass


async def _reset_db() -> None:
    """Compatibility no-op for callers that reset after a query failure.

    Token stats now uses short-lived connections, so there is no process-wide
    cached connection to reset.
    """
    return None


def _get_existing_agent(request: Request, conversation_id: str | None):
    """Get an existing conversation agent from pool if available."""
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is not None and conversation_id:
        agent = pool.get_existing(conversation_id)
        if agent is not None:
            return agent
    return getattr(request.app.state, "agent", None)


async def _close_db(db: Database | None) -> None:
    if db is not None:
        with contextlib.suppress(Exception):
            await db.close()


def _parse_range(
    start: str | None,
    end: str | None,
    period: str | None,
    hours: int | None = None,
) -> tuple[str, str]:
    """Resolve time range and return as SQLite-compatible UTC timestamp strings.

    SQLite CURRENT_TIMESTAMP stores UTC in 'YYYY-MM-DD HH:MM:SS' format (space separator).
    We must query with the same format and timezone to get correct string comparisons.

    新增：兼容前端/CLI 历史调用习惯
      - hours: 直接给"过去 N 小时"，比 period 别名更直观
      - period 增加 12h/24h/7d/30d 等更友好的别名
      - 任何越界（负数/过大）都裁剪到 [1h, 365d]，避免空结果或扫全表
    """
    if start and end:
        try:
            s = datetime.fromisoformat(start)
            e = datetime.fromisoformat(end)
            return s.strftime("%Y-%m-%d %H:%M:%S"), e.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            logger.warning(
                f"[TokenStats] Invalid time range: start={start!r}, end={end!r}, falling back to default"
            )

    now_utc = datetime.now(UTC).replace(tzinfo=None)

    delta: timedelta | None = None
    if hours is not None:
        try:
            h = int(hours)
        except Exception:
            h = 24
        h = max(1, min(h, 365 * 24))
        delta = timedelta(hours=h)

    if delta is None:
        delta_map = {
            "1h": timedelta(hours=1),
            "12h": timedelta(hours=12),
            "24h": timedelta(hours=24),
            "1d": timedelta(days=1),
            "3d": timedelta(days=3),
            "7d": timedelta(days=7),
            "1w": timedelta(weeks=1),
            "30d": timedelta(days=30),
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
    hours: int | None = Query(None, ge=1, le=8760),
    endpoint_name: str | None = Query(None),
    operation_type: str | None = Query(None),
):
    db = await _get_db()
    if db is None:
        return _db_unavailable_payload()
    start_str, end_str = _parse_range(start, end, period, hours=hours)
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
    finally:
        await _close_db(db)
    return {"start": start_str, "end": end_str, "group_by": group_by, "data": rows}


@router.get("/timeline")
async def timeline(
    request: Request,
    interval: str = Query("hour"),
    period: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    hours: int | None = Query(None, ge=1, le=8760),
    endpoint_name: str | None = Query(None),
):
    db = await _get_db()
    if db is None:
        return _db_unavailable_payload()
    start_str, end_str = _parse_range(start, end, period, hours=hours)
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
    finally:
        await _close_db(db)
    return {"start": start_str, "end": end_str, "interval": interval, "data": rows}


@router.get("/sessions")
async def sessions(
    request: Request,
    period: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    hours: int | None = Query(None, ge=1, le=8760),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db = await _get_db()
    if db is None:
        return _db_unavailable_payload()
    start_str, end_str = _parse_range(start, end, period, hours=hours)
    try:
        rows = await db.get_token_usage_sessions(
            start_time=start_str, end_time=end_str, limit=limit, offset=offset
        )
    except Exception as e:
        logger.error(f"[TokenStats] sessions query failed: {e}")
        await _reset_db()
        return {"error": "query failed, connection reset"}
    finally:
        await _close_db(db)
    return {"start": start_str, "end": end_str, "data": rows}


@router.get("/records")
async def records(
    request: Request,
    period: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    hours: int | None = Query(None, ge=1, le=8760),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    endpoint_name: str | None = Query(None),
    operation_type: str | None = Query(None),
):
    db = await _get_db()
    if db is None:
        return _db_unavailable_payload()
    start_str, end_str = _parse_range(start, end, period, hours=hours)
    try:
        rows = await db.get_token_usage_records(
            start_time=start_str,
            end_time=end_str,
            limit=limit,
            offset=offset,
            endpoint_name=endpoint_name,
            operation_type=operation_type,
        )
    except Exception as e:
        logger.error(f"[TokenStats] records query failed: {e}")
        await _reset_db()
        return {"error": "query failed, connection reset"}
    finally:
        await _close_db(db)
    return {"start": start_str, "end": end_str, "data": rows}


@router.get("/total")
async def total(
    request: Request,
    period: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    hours: int | None = Query(None, ge=1, le=8760),
):
    db = await _get_db()
    if db is None:
        return _db_unavailable_payload()
    start_str, end_str = _parse_range(start, end, period, hours=hours)
    try:
        row = await db.get_token_usage_total(start_time=start_str, end_time=end_str)
    except Exception as e:
        logger.error(f"[TokenStats] total query failed: {e}")
        await _reset_db()
        return {"error": "query failed, connection reset"}
    finally:
        await _close_db(db)
    return {"start": start_str, "end": end_str, "data": row}


@router.get("/by-agent")
async def by_agent(
    request: Request,
    period: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    hours: int | None = Query(None, ge=1, le=8760),
):
    """Token usage grouped by agent_profile_id for multi-agent mode."""
    db = await _get_db()
    if db is None:
        return _db_unavailable_payload()
    start_str, end_str = _parse_range(start, end, period, hours=hours)
    try:
        by_agent_data = await db.get_token_usage_by_agent(start_time=start_str, end_time=end_str)
    except Exception as e:
        logger.error(f"[TokenStats] by-agent query failed: {e}")
        await _reset_db()
        return {"error": "query failed, connection reset"}
    finally:
        await _close_db(db)
    return {"start": start_str, "end": end_str, "by_agent": by_agent_data}


@router.get("/pricing")
async def pricing_overview(request: Request):
    """Return pricing-source overview for all currently configured endpoints.

    Useful for the LLMView UI to show which endpoints have user-configured
    prices, which fall back to the built-in table, and which still resolve
    to "-" (unknown). Fix-5.
    """
    try:
        from openakita.api.routes.config import _get_endpoint_manager
        from openakita.llm.pricing import list_builtin_prices
    except Exception as e:
        return {"error": f"endpoint_manager unavailable: {e}"}

    manager = _get_endpoint_manager()
    endpoints_info: list[dict] = []
    if manager:
        try:
            for ep in manager.get_endpoints() or []:
                tier = None
                source = "unknown"
                try:
                    tier = ep.get_effective_pricing()
                except Exception:
                    tier = None
                if tier:
                    source = tier.get("source") or ("user" if ep.pricing_tiers else "builtin")
                endpoints_info.append(
                    {
                        "name": getattr(ep, "name", ""),
                        "provider": getattr(ep, "provider", ""),
                        "model": getattr(ep, "model", ""),
                        "currency": getattr(ep, "price_currency", "CNY"),
                        "source": source,
                        "tier": tier,
                    }
                )
        except Exception as e:
            logger.warning(f"[TokenStats] pricing overview enumerate failed: {e}")

    return {
        "endpoints": endpoints_info,
        "builtin_table": list_builtin_prices(),
    }


@router.get("/context")
async def context(request: Request, conversation_id: str | None = Query(default=None)):
    """Return the current session's context token usage and limit."""
    agent = _get_existing_agent(request, conversation_id)
    actual = getattr(agent, "_local_agent", agent) if agent else None
    if actual is None:
        return {"error": "agent not available"}

    try:
        snapshot = get_context_snapshot(actual, conversation_id=conversation_id)
        if snapshot is not None:
            return snapshot.to_dict()
    except Exception as e:
        logger.warning(f"[TokenStats] Failed to get context size: {e}")

    return {"context_tokens": 0, "context_limit": 0, "remaining_tokens": 0, "percent": 0}
