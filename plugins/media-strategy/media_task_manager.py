# ruff: noqa: N999
"""SQLite persistence for Media Strategy tasks, sources and reports."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite
from media_models import DEFAULT_SETTINGS, PACKAGE_DEFS, SOURCE_DEFS

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'rss',
    package_ids_json TEXT NOT NULL DEFAULT '[]',
    label_zh TEXT NOT NULL,
    label_en TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    authority REAL NOT NULL DEFAULT 0.5,
    custom INTEGER NOT NULL DEFAULT 0,
    last_fetch_at TEXT,
    last_status TEXT,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_enabled ON sources(enabled, kind);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0,
    pipeline_step TEXT,
    params_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_kind TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, mode);

CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    package_ids_json TEXT NOT NULL DEFAULT '[]',
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    hot_score REAL NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL DEFAULT 'medium',
    ai_summary TEXT NOT NULL DEFAULT '',
    ai_reason TEXT NOT NULL DEFAULT '',
    duplicate_count INTEGER NOT NULL DEFAULT 1,
    topic_key TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_articles_time ON articles(COALESCE(published_at, fetched_at) DESC);
CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(hot_score DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_topic ON articles(topic_key);

CREATE TABLE IF NOT EXISTS crawl_records (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_source_time ON crawl_records(source_id, finished_at DESC);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    markdown TEXT NOT NULL,
    html TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);

CREATE TABLE IF NOT EXISTS push_records (
    id TEXT PRIMARY KEY,
    report_id TEXT,
    channel TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
"""

_TASK_JSON_KEYS = {"params", "result"}
_TASK_COLUMNS = {
    "status": "status",
    "progress": "progress",
    "pipeline_step": "pipeline_step",
    "params": "params_json",
    "result": "result_json",
    "error_kind": "error_kind",
    "error_message": "error_message",
    "started_at": "started_at",
    "finished_at": "finished_at",
}


def utcnow_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("params_json", "result_json", "package_ids_json", "tags_json", "raw_json", "meta_json"):
        if key in data:
            public = key.removesuffix("_json")
            data[public] = _json_loads(data.pop(key), [] if key.endswith("ids_json") or key == "tags_json" else {})
    if "enabled" in data:
        data["enabled"] = bool(data["enabled"])
    if "custom" in data:
        data["custom"] = bool(data["custom"])
    return data


async def _fetchone(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] | list[Any] = (),
) -> aiosqlite.Row | None:
    cursor = await db.execute(sql, params)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


def _short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def article_id_for(source_id: str, url: str) -> tuple[str, str]:
    h = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()
    return f"ms-a-{hashlib.sha1(f'{source_id}:{h}'.encode()).hexdigest()[:16]}", h


def topic_key_for(title: str) -> str:
    cleaned = "".join(ch.lower() for ch in title if ch.isalnum())
    return hashlib.sha1(cleaned[:80].encode("utf-8")).hexdigest()[:12] if cleaned else ""


class MediaTaskManager:
    """Single-connection SQLite manager used by the plugin and tests."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(SCHEMA_SQL)
        await self._seed_defaults()
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def ready(self) -> bool:
        return self._db is not None

    def _require(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("MediaTaskManager is not initialized")
        return self._db

    async def _seed_defaults(self) -> None:
        db = self._require()
        now = time.time()
        for key, value in DEFAULT_SETTINGS.items():
            await db.execute(
                "INSERT OR IGNORE INTO config(key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False), now),
            )
        for source_id, meta in SOURCE_DEFS.items():
            await db.execute(
                """
                INSERT OR IGNORE INTO sources(
                    id, kind, package_ids_json, label_zh, label_en, url, enabled,
                    authority, custom, created_at, updated_at
                ) VALUES (?, 'rss', ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    source_id,
                    json.dumps(meta["packages"], ensure_ascii=False),
                    meta["label_zh"],
                    meta["label_en"],
                    meta["url"],
                    1 if meta["default_enabled"] else 0,
                    float(meta["authority"]),
                    now,
                    now,
                ),
            )
        for package_id, meta in PACKAGE_DEFS.items():
            await db.execute(
                "INSERT OR IGNORE INTO config(key, value, updated_at) VALUES (?, ?, ?)",
                (
                    f"package.{package_id}.enabled",
                    json.dumps(bool(meta["default_enabled"]), ensure_ascii=False),
                    now,
                ),
            )

    async def get_settings(self) -> dict[str, Any]:
        db = self._require()
        rows = await db.execute_fetchall("SELECT key, value FROM config")
        out: dict[str, Any] = dict(DEFAULT_SETTINGS)
        for row in rows:
            key = str(row["key"])
            if key in DEFAULT_SETTINGS:
                out[key] = _json_loads(row["value"], DEFAULT_SETTINGS[key])
        return out

    async def set_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        db = self._require()
        now = time.time()
        allowed = set(DEFAULT_SETTINGS)
        for key, value in updates.items():
            if key not in allowed:
                continue
            await db.execute(
                "INSERT OR REPLACE INTO config(key, value, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False), now),
            )
        await db.commit()
        return await self.get_settings()

    async def set_package_enabled(self, package_id: str, enabled: bool) -> dict[str, Any]:
        if package_id not in PACKAGE_DEFS:
            raise KeyError(package_id)
        db = self._require()
        await db.execute(
            "INSERT OR REPLACE INTO config(key, value, updated_at) VALUES (?, ?, ?)",
            (f"package.{package_id}.enabled", json.dumps(bool(enabled)), time.time()),
        )
        await db.commit()
        return await self.list_packages()

    async def list_packages(self) -> dict[str, Any]:
        db = self._require()
        rows = await db.execute_fetchall("SELECT key, value FROM config WHERE key LIKE 'package.%.enabled'")
        enabled_map = {
            str(row["key"]).split(".")[1]: bool(_json_loads(row["value"], False)) for row in rows
        }
        return {
            pid: {**meta, "id": pid, "enabled": enabled_map.get(pid, bool(meta["default_enabled"]))}
            for pid, meta in PACKAGE_DEFS.items()
        }

    async def list_sources(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        db = self._require()
        sql = "SELECT * FROM sources"
        params: list[Any] = []
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY custom ASC, id ASC"
        rows = await db.execute_fetchall(sql, params)
        return [_row_to_dict(row) or {} for row in rows]

    async def add_custom_source(
        self,
        *,
        name: str,
        url: str,
        package_ids: list[str],
        enabled: bool = True,
    ) -> dict[str, Any]:
        db = self._require()
        now = time.time()
        source_id = "custom-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        labels = name.strip() or url
        await db.execute(
            """
            INSERT OR REPLACE INTO sources(
                id, kind, package_ids_json, label_zh, label_en, url, enabled,
                authority, custom, created_at, updated_at
            ) VALUES (?, 'rss', ?, ?, ?, ?, ?, 0.45, 1, COALESCE((SELECT created_at FROM sources WHERE id=?), ?), ?)
            """,
            (
                source_id,
                json.dumps(package_ids, ensure_ascii=False),
                labels,
                labels,
                url,
                1 if enabled else 0,
                source_id,
                now,
                now,
            ),
        )
        await db.commit()
        row = await _fetchone(db, "SELECT * FROM sources WHERE id=?", (source_id,))
        return _row_to_dict(row) or {}

    async def update_source_status(
        self,
        source_id: str,
        *,
        status: str,
        error: str = "",
        fetched_at: str | None = None,
    ) -> None:
        db = self._require()
        await db.execute(
            """
            UPDATE sources
            SET last_status=?, last_error=?, last_fetch_at=?, updated_at=?
            WHERE id=?
            """,
            (status, error, fetched_at or utcnow_iso(), time.time(), source_id),
        )
        await db.commit()

    async def create_task(self, mode: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        db = self._require()
        task_id = _short_id("ms")
        now = time.time()
        await db.execute(
            """
            INSERT INTO tasks(id, mode, status, progress, params_json, created_at, updated_at)
            VALUES (?, ?, 'pending', 0, ?, ?, ?)
            """,
            (task_id, mode, json.dumps(params or {}, ensure_ascii=False), now, now),
        )
        await db.commit()
        return await self.get_task(task_id) or {"id": task_id, "mode": mode, "status": "pending"}

    async def update_task(self, task_id: str, **updates: Any) -> None:
        db = self._require()
        sets: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            column = _TASK_COLUMNS.get(key)
            if column is None:
                raise ValueError(f"unknown task update column: {key}")
            sets.append(f"{column}=?")
            if key in _TASK_JSON_KEYS:
                values.append(json.dumps(value, ensure_ascii=False))
            else:
                values.append(value)
        if not sets:
            return
        sets.append("updated_at=?")
        values.append(time.time())
        values.append(task_id)
        await db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", values)
        await db.commit()

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        db = self._require()
        row = await _fetchone(db, "SELECT * FROM tasks WHERE id=?", (task_id,))
        return _row_to_dict(row)

    async def list_tasks(self, *, limit: int = 50) -> list[dict[str, Any]]:
        db = self._require()
        rows = await db.execute_fetchall(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        )
        return [_row_to_dict(row) or {} for row in rows]

    async def upsert_article(self, item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        db = self._require()
        article_id, url_hash = article_id_for(str(item["source_id"]), str(item["url"]))
        topic_key = topic_key_for(str(item.get("title") or ""))
        fetched_at = item.get("fetched_at") or utcnow_iso()
        row = await _fetchone(db, "SELECT duplicate_count FROM articles WHERE url_hash=?", (url_hash,))
        inserted = row is None
        if inserted:
            await db.execute(
                """
                INSERT INTO articles(
                    id, source_id, package_ids_json, url, url_hash, title, summary,
                    author, tags_json, published_at, fetched_at, raw_json, hot_score,
                    risk_level, ai_summary, ai_reason, duplicate_count, topic_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    article_id,
                    item["source_id"],
                    json.dumps(item.get("package_ids") or [], ensure_ascii=False),
                    item["url"],
                    url_hash,
                    item.get("title") or "",
                    item.get("summary") or "",
                    item.get("author") or "",
                    json.dumps(item.get("tags") or [], ensure_ascii=False),
                    item.get("published_at"),
                    fetched_at,
                    json.dumps(item.get("raw") or {}, ensure_ascii=False),
                    float(item.get("hot_score") or 0),
                    item.get("risk_level") or "medium",
                    item.get("ai_summary") or "",
                    item.get("ai_reason") or "",
                    topic_key,
                ),
            )
        else:
            await db.execute(
                """
                UPDATE articles
                SET duplicate_count=duplicate_count+1, fetched_at=?, hot_score=max(hot_score, ?)
                WHERE url_hash=?
                """,
                (fetched_at, float(item.get("hot_score") or 0), url_hash),
            )
        await db.commit()
        row2 = await _fetchone(db, "SELECT * FROM articles WHERE url_hash=?", (url_hash,))
        return _row_to_dict(row2) or {}, inserted

    async def update_article_analysis(
        self,
        article_id: str,
        *,
        hot_score: float,
        risk_level: str,
        ai_summary: str = "",
        ai_reason: str = "",
    ) -> None:
        db = self._require()
        await db.execute(
            """
            UPDATE articles
            SET hot_score=?, risk_level=?, ai_summary=?, ai_reason=?
            WHERE id=?
            """,
            (hot_score, risk_level, ai_summary, ai_reason, article_id),
        )
        await db.commit()

    async def recent_articles(
        self,
        *,
        since_hours: int = 24,
        package_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        db = self._require()
        import datetime as _dt

        cutoff = _dt.datetime.fromtimestamp(
            time.time() - max(1, since_hours) * 3600,
            tz=_dt.UTC,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        sql = "SELECT * FROM articles WHERE fetched_at >= ?"
        params: list[Any] = [cutoff]
        if package_id:
            sql += " AND package_ids_json LIKE ?"
            params.append(f'%"{package_id}"%')
        sql += " ORDER BY hot_score DESC, COALESCE(published_at, fetched_at) DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        rows = await db.execute_fetchall(sql, params)
        return [_row_to_dict(row) or {} for row in rows]

    async def search_articles(
        self,
        *,
        q: str = "",
        package_id: str = "",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        db = self._require()
        sql = "SELECT * FROM articles WHERE 1=1"
        params: list[Any] = []
        if q.strip():
            like = f"%{q.strip()}%"
            sql += " AND (title LIKE ? OR summary LIKE ? OR ai_summary LIKE ?)"
            params.extend([like, like, like])
        if package_id:
            sql += " AND package_ids_json LIKE ?"
            params.append(f'%"{package_id}"%')
        sql += " ORDER BY hot_score DESC, COALESCE(published_at, fetched_at) DESC LIMIT ?"
        params.append(max(1, min(int(limit), 100)))
        rows = await db.execute_fetchall(sql, params)
        return [_row_to_dict(row) or {} for row in rows]

    async def get_articles_by_ids(self, article_ids: list[str]) -> list[dict[str, Any]]:
        if not article_ids:
            return []
        db = self._require()
        placeholders = ",".join("?" for _ in article_ids)
        rows = await db.execute_fetchall(
            f"SELECT * FROM articles WHERE id IN ({placeholders})",
            article_ids,
        )
        by_id = {_row_to_dict(row)["id"]: _row_to_dict(row) for row in rows if _row_to_dict(row)}
        return [by_id[i] for i in article_ids if i in by_id]

    async def insert_crawl_record(
        self,
        *,
        source_id: str,
        status: str,
        fetched_count: int,
        inserted_count: int,
        error_message: str = "",
        started_at: str,
        finished_at: str,
    ) -> None:
        db = self._require()
        await db.execute(
            """
            INSERT INTO crawl_records(
                id, source_id, status, fetched_count, inserted_count,
                error_message, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _short_id("ms-crawl"),
                source_id,
                status,
                fetched_count,
                inserted_count,
                error_message,
                started_at,
                finished_at,
            ),
        )
        await db.commit()

    async def save_report(
        self,
        *,
        task_id: str,
        kind: str,
        title: str,
        markdown: str,
        html: str = "",
        meta: dict[str, Any] | None = None,
        path: str = "",
    ) -> dict[str, Any]:
        db = self._require()
        report_id = _short_id("ms-rpt")
        created_at = utcnow_iso()
        await db.execute(
            """
            INSERT INTO reports(id, task_id, kind, title, markdown, html, meta_json, path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                task_id,
                kind,
                title,
                markdown,
                html,
                json.dumps(meta or {}, ensure_ascii=False),
                path,
                created_at,
            ),
        )
        await db.commit()
        row = await _fetchone(db, "SELECT * FROM reports WHERE id=?", (report_id,))
        return _row_to_dict(row) or {}

    async def get_report(self, report_id: str) -> dict[str, Any] | None:
        db = self._require()
        row = await _fetchone(db, "SELECT * FROM reports WHERE id=?", (report_id,))
        return _row_to_dict(row)

    async def list_reports(self, *, limit: int = 30) -> list[dict[str, Any]]:
        db = self._require()
        rows = await db.execute_fetchall(
            "SELECT * FROM reports ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 100)),),
        )
        return [_row_to_dict(row) or {} for row in rows]
