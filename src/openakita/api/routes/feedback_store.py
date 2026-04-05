"""
Local feedback record storage using aiosqlite.

Tracks submitted feedback for the "My Feedback" feature, enabling users
to view submission history and check progress without re-contacting the cloud.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_DB_PATH: Path | None = None
_MAX_RECORDS = 500


def _resolve_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        try:
            from openakita.config import settings
            _DB_PATH = settings.data_dir / "feedback.db"
        except Exception:
            _DB_PATH = Path.cwd() / "data" / "feedback.db"
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


async def _get_conn() -> aiosqlite.Connection:
    db_path = _resolve_db_path()
    conn = await aiosqlite.connect(db_path)
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS feedback_records (
                report_id       TEXT PRIMARY KEY,
                feedback_token  TEXT,
                title           TEXT NOT NULL,
                type            TEXT NOT NULL,
                contact_email   TEXT DEFAULT '',
                submitted_at    TEXT NOT NULL,
                last_checked_at TEXT,
                last_reply_at   TEXT,
                cached_status   TEXT DEFAULT 'pending'
            );
        """)
    except Exception:
        await conn.close()
        raise
    return conn


async def save_record(
    *,
    report_id: str,
    feedback_token: str | None,
    title: str,
    report_type: str,
    contact_email: str = "",
    submitted_at: str | None = None,
) -> None:
    conn = await _get_conn()
    try:
        now = submitted_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        await conn.execute(
            """INSERT INTO feedback_records
               (report_id, feedback_token, title, type, contact_email, submitted_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(report_id) DO UPDATE SET
                 feedback_token = COALESCE(excluded.feedback_token, feedback_token),
                 title = excluded.title""",
            (report_id, feedback_token, title, report_type, contact_email, now),
        )
        await conn.commit()
        count_row = await conn.execute(
            "SELECT COUNT(*) as cnt FROM feedback_records"
        )
        row = await count_row.fetchone()
        if row and row["cnt"] > _MAX_RECORDS:
            excess = row["cnt"] - _MAX_RECORDS
            await conn.execute(
                """DELETE FROM feedback_records WHERE report_id IN (
                    SELECT report_id FROM feedback_records
                    ORDER BY
                      CASE WHEN cached_status IN ('resolved','closed') THEN 0 ELSE 1 END,
                      submitted_at ASC
                    LIMIT ?
                )""",
                (excess,),
            )
            await conn.commit()
    finally:
        await conn.close()


async def get_all_records() -> list[dict]:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            """SELECT report_id, feedback_token, title, type,
                      contact_email, submitted_at, last_checked_at,
                      last_reply_at, cached_status
               FROM feedback_records ORDER BY submitted_at DESC"""
        )
        rows = await cursor.fetchall()
        results = []
        for r in rows:
            has_unread = bool(
                r["last_reply_at"]
                and (
                    not r["last_checked_at"]
                    or r["last_reply_at"] > r["last_checked_at"]
                )
            )
            results.append({
                "report_id": r["report_id"],
                "has_token": r["feedback_token"] is not None,
                "feedback_token": r["feedback_token"],
                "title": r["title"],
                "type": r["type"],
                "contact_email": r["contact_email"] or "",
                "submitted_at": r["submitted_at"],
                "cached_status": r["cached_status"] or "pending",
                "has_unread": has_unread,
            })
        return results
    finally:
        await conn.close()


async def get_record(report_id: str) -> dict | None:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "SELECT * FROM feedback_records WHERE report_id = ?",
            (report_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        await conn.close()


async def update_status(
    report_id: str,
    *,
    cached_status: str | None = None,
    last_reply_at: str | None = None,
    last_checked_at: str | None = None,
) -> None:
    conn = await _get_conn()
    try:
        sets = []
        vals: list[str] = []
        if cached_status is not None:
            sets.append("cached_status = ?")
            vals.append(cached_status)
        if last_reply_at is not None:
            sets.append("last_reply_at = ?")
            vals.append(last_reply_at)
        if last_checked_at is not None:
            sets.append("last_checked_at = ?")
            vals.append(last_checked_at)
        if not sets:
            return
        vals.append(report_id)
        await conn.execute(
            f"UPDATE feedback_records SET {', '.join(sets)} "
            "WHERE report_id = ?",
            vals,
        )
        await conn.commit()
    finally:
        await conn.close()


async def delete_record(report_id: str) -> bool:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            "DELETE FROM feedback_records WHERE report_id = ?",
            (report_id,),
        )
        await conn.commit()
        return cursor.rowcount > 0
    finally:
        await conn.close()


async def get_unread_count() -> int:
    conn = await _get_conn()
    try:
        cursor = await conn.execute(
            """SELECT COUNT(*) as cnt FROM feedback_records
               WHERE last_reply_at IS NOT NULL AND last_reply_at != ''
               AND (last_checked_at IS NULL OR last_checked_at = ''
                    OR last_reply_at > last_checked_at)"""
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0
    finally:
        await conn.close()
