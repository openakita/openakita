from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite

from .models import InboxMessage, utc_now_iso


class InboxStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def _connect(self) -> aiosqlite.Connection:
        await asyncio.to_thread(self.db_path.parent.mkdir, parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS inbox_messages (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                body_markdown   TEXT NOT NULL,
                type            TEXT NOT NULL,
                priority        TEXT NOT NULL,
                cta_json        TEXT,
                target_rule_json TEXT NOT NULL,
                rollout_percent INTEGER NOT NULL,
                publish_at      TEXT,
                expire_at       TEXT,
                source          TEXT NOT NULL,
                raw_json        TEXT NOT NULL,
                received_at     TEXT NOT NULL,
                read_at         TEXT,
                clicked_at      TEXT,
                dismissed_at    TEXT
            );
            """
        )
        return conn

    async def upsert_messages(self, messages: list[InboxMessage]) -> None:
        if not messages:
            return
        conn = await self._connect()
        try:
            now = utc_now_iso()
            for message in messages:
                await conn.execute(
                    """
                    INSERT INTO inbox_messages (
                        id, title, body_markdown, type, priority, cta_json,
                        target_rule_json, rollout_percent, publish_at, expire_at,
                        source, raw_json, received_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        body_markdown = excluded.body_markdown,
                        type = excluded.type,
                        priority = excluded.priority,
                        cta_json = excluded.cta_json,
                        target_rule_json = excluded.target_rule_json,
                        rollout_percent = excluded.rollout_percent,
                        publish_at = excluded.publish_at,
                        expire_at = excluded.expire_at,
                        source = excluded.source,
                        raw_json = excluded.raw_json
                    """,
                    (
                        message.id,
                        message.title,
                        message.body_markdown,
                        message.type,
                        message.priority,
                        _dump_json(message.cta),
                        _dump_json(message.target_rule),
                        message.rollout_percent,
                        message.publish_at,
                        message.expire_at,
                        message.source,
                        _dump_json(message.raw),
                        now,
                    ),
                )
            await conn.commit()
        finally:
            await conn.close()

    async def list_messages(self, include_dismissed: bool = False) -> list[dict[str, Any]]:
        conn = await self._connect()
        try:
            where = "" if include_dismissed else "WHERE dismissed_at IS NULL"
            cursor = await conn.execute(
                f"""
                SELECT * FROM inbox_messages
                {where}
                ORDER BY
                    CASE priority
                        WHEN 'critical' THEN 4
                        WHEN 'high' THEN 3
                        WHEN 'normal' THEN 2
                        WHEN 'low' THEN 1
                        ELSE 0
                    END DESC,
                    COALESCE(publish_at, received_at) DESC
                """
            )
            rows = await cursor.fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            await conn.close()

    async def get_message(self, message_id: str) -> dict[str, Any] | None:
        conn = await self._connect()
        try:
            cursor = await conn.execute("SELECT * FROM inbox_messages WHERE id = ?", (message_id,))
            row = await cursor.fetchone()
            return _row_to_dict(row) if row else None
        finally:
            await conn.close()

    async def mark_event(self, message_id: str, event: str) -> bool:
        column = {
            "read": "read_at",
            "clicked": "clicked_at",
            "dismissed": "dismissed_at",
        }.get(event)
        if column is None:
            raise ValueError(f"Unsupported inbox event: {event}")
        conn = await self._connect()
        try:
            cursor = await conn.execute(
                f"UPDATE inbox_messages SET {column} = COALESCE({column}, ?) WHERE id = ?",
                (utc_now_iso(), message_id),
            )
            await conn.commit()
            return cursor.rowcount > 0
        finally:
            await conn.close()

    async def unread_count(self) -> int:
        conn = await self._connect()
        try:
            cursor = await conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM inbox_messages
                WHERE read_at IS NULL AND dismissed_at IS NULL
                """
            )
            row = await cursor.fetchone()
            return int(row["cnt"] if row else 0)
        finally:
            await conn.close()


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "body_markdown": row["body_markdown"],
        "type": row["type"],
        "priority": row["priority"],
        "cta": _load_json(row["cta_json"]),
        "target_rule": _load_json(row["target_rule_json"]) or {},
        "rollout_percent": row["rollout_percent"],
        "publish_at": row["publish_at"],
        "expire_at": row["expire_at"],
        "source": row["source"],
        "raw": _load_json(row["raw_json"]) or {},
        "received_at": row["received_at"],
        "read_at": row["read_at"],
        "clicked_at": row["clicked_at"],
        "dismissed_at": row["dismissed_at"],
        "unread": row["read_at"] is None and row["dismissed_at"] is None,
    }


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None
