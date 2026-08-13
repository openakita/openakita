"""Durable receiver for account status propagation events."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from openakita.storage.safe_sqlite import safe_open_async

EVENT_TYPE = "account.user.status.changed"


class StatusPropagationError(Exception):
    status_code = 400
    error = "invalid_event"


class InvalidSignatureError(StatusPropagationError):
    status_code = 401
    error = "invalid_signature"


class StaleTimestampError(StatusPropagationError):
    status_code = 401
    error = "stale_timestamp"


class EventConflictError(StatusPropagationError):
    status_code = 409
    error = "event_id_conflict"


@dataclass(frozen=True)
class ProcessResult:
    duplicate: bool = False


def status_signature(secret: str, timestamp: str, event_id: str, body: bytes) -> str:
    signed = timestamp.encode() + b"\n" + event_id.encode() + b"\n" + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class AccountStatusStore:
    """SQLite-backed identity projection for server-mode Account integration.

    This store deliberately does not modify ``WebAccessConfig``. Local access
    remains an independent break-glass path when a central account is suspended.
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "account_identity.db"

    async def _connect(self) -> aiosqlite.Connection:
        conn = await safe_open_async(
            self._path,
            want_wal=True,
            foreign_keys=True,
            row_factory=aiosqlite.Row,
        )
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS account_users (
                account_user_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('active', 'suspended')),
                status_reason TEXT,
                status_changed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inbound_service_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                processed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_sessions (
                session_id TEXT PRIMARY KEY,
                account_user_id TEXT NOT NULL,
                refresh_token_hash TEXT,
                revoked_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (account_user_id) REFERENCES account_users(account_user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_account_sessions_user
                ON account_sessions(account_user_id, revoked_at);
            CREATE TABLE IF NOT EXISTS account_identity_snapshot (
                account_user_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                entitlements_json TEXT NOT NULL DEFAULT '{}',
                fetched_at TEXT NOT NULL
            );
            """
        )
        return conn

    async def process(
        self,
        *,
        secret: str,
        window_seconds: int,
        timestamp: str,
        event_id: str,
        idempotency_key: str,
        signature: str,
        body: bytes,
        now: datetime | None = None,
    ) -> ProcessResult:
        expected = status_signature(secret, timestamp, event_id, body)
        if not secret or not hmac.compare_digest(expected, signature.strip()):
            raise InvalidSignatureError

        try:
            request_time = datetime.fromtimestamp(int(timestamp), tz=UTC)
        except (ValueError, OverflowError) as exc:
            raise StaleTimestampError from exc
        current_time = now or datetime.now(UTC)
        if abs(current_time - request_time) > timedelta(seconds=max(window_seconds, 1)):
            raise StaleTimestampError

        try:
            payload = json.loads(body)
            occurred_at = datetime.fromisoformat(str(payload["occurred_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StatusPropagationError from exc
        if (
            payload.get("event_id") != event_id
            or idempotency_key != event_id
            or payload.get("event_type") != EVENT_TYPE
            or not payload.get("user_id")
            or payload.get("status") not in {"active", "suspended"}
            or payload.get("previous_status") not in {"active", "suspended"}
        ):
            raise StatusPropagationError

        payload_hash = hashlib.sha256(body).hexdigest()
        processed_at = current_time.isoformat()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            cursor = await conn.execute(
                "SELECT payload_sha256 FROM inbound_service_events WHERE event_id = ?",
                (event_id,),
            )
            existing = await cursor.fetchone()
            if existing:
                await conn.rollback()
                if not hmac.compare_digest(existing["payload_sha256"], payload_hash):
                    raise EventConflictError
                return ProcessResult(duplicate=True)

            await conn.execute(
                """
                INSERT INTO account_users
                    (account_user_id, status, status_reason, status_changed_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_user_id) DO UPDATE SET
                    status = excluded.status,
                    status_reason = excluded.status_reason,
                    status_changed_at = excluded.status_changed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    payload["user_id"],
                    payload["status"],
                    payload.get("reason"),
                    occurred_at.isoformat(),
                    processed_at,
                ),
            )
            if payload["status"] == "suspended":
                await conn.execute(
                    """
                    UPDATE account_sessions SET revoked_at = ?
                    WHERE account_user_id = ? AND revoked_at IS NULL
                    """,
                    (processed_at, payload["user_id"]),
                )
            await conn.execute(
                """
                INSERT INTO inbound_service_events
                    (event_id, event_type, payload_sha256, occurred_at, processed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, EVENT_TYPE, payload_hash, occurred_at.isoformat(), processed_at),
            )
            await conn.commit()
            return ProcessResult()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def save_authenticated(
        self, *, account_user_id: str, profile_json: str, session_id: str
    ) -> None:
        now = datetime.now(UTC).isoformat()
        conn = await self._connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute(
                """
                INSERT INTO account_users
                    (account_user_id, status, status_changed_at, updated_at)
                VALUES (?, 'active', ?, ?)
                ON CONFLICT(account_user_id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (account_user_id, now, now),
            )
            cursor = await conn.execute(
                "SELECT status FROM account_users WHERE account_user_id = ?",
                (account_user_id,),
            )
            status_row = await cursor.fetchone()
            if status_row and status_row["status"] != "active":
                await conn.rollback()
                raise RuntimeError("account is suspended")
            await conn.execute(
                """
                INSERT INTO account_identity_snapshot
                    (account_user_id, profile_json, entitlements_json, fetched_at)
                VALUES (?, ?, '{}', ?)
                ON CONFLICT(account_user_id) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    fetched_at = excluded.fetched_at
                """,
                (account_user_id, profile_json, now),
            )
            await conn.execute(
                """
                INSERT INTO account_sessions
                    (session_id, account_user_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET revoked_at = NULL
                """,
                (session_id, account_user_id, now),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def save_entitlements(self, *, account_user_id: str, entitlements_json: str) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                """
                UPDATE account_identity_snapshot
                SET entitlements_json = ?, fetched_at = ?
                WHERE account_user_id = ?
                """,
                (entitlements_json, datetime.now(UTC).isoformat(), account_user_id),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def snapshot(self) -> dict | None:
        conn = await self._connect()
        try:
            cursor = await conn.execute(
                """
                SELECT s.account_user_id, s.profile_json, s.entitlements_json,
                       s.fetched_at, u.status, u.status_reason
                FROM account_identity_snapshot s
                JOIN account_users u ON u.account_user_id = s.account_user_id
                ORDER BY s.fetched_at DESC LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "account_user_id": row["account_user_id"],
                "profile": json.loads(row["profile_json"]),
                "entitlements": json.loads(row["entitlements_json"]),
                "fetched_at": row["fetched_at"],
                "status": row["status"],
                "status_reason": row["status_reason"],
            }
        finally:
            await conn.close()

    async def session_is_active(self, session_id: str) -> bool:
        conn = await self._connect()
        try:
            cursor = await conn.execute(
                """
                SELECT 1 FROM account_sessions s
                JOIN account_users u ON u.account_user_id = s.account_user_id
                WHERE s.session_id = ? AND s.revoked_at IS NULL AND u.status = 'active'
                """,
                (session_id,),
            )
            return await cursor.fetchone() is not None
        finally:
            await conn.close()

    async def revoke_session(self, session_id: str) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                "UPDATE account_sessions SET revoked_at = ? WHERE session_id = ?",
                (datetime.now(UTC).isoformat(), session_id),
            )
            await conn.commit()
        finally:
            await conn.close()
