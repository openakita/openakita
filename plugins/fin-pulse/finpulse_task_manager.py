# ruff: noqa: N999
"""SQLite-backed task / article / digest / config manager for fin-pulse.

Schema follows §6 of the plan — four primary tables
(``tasks`` / ``articles`` / ``digests`` / ``config``) plus the v2.0
``assets_bus`` reservation (created on init, never written in V1.0 — the
Phase-1 red-line tests assert the count stays zero after every CRUD path).

Hardening reused from ``footage-gate`` / ``subtitle-craft``:

* :meth:`update_task_safe` accepts only whitelisted column mappings;
  passing an unknown column raises ``ValueError`` so a typo surfaces in
  tests instead of being silently dropped.
* All writes serialise via ``aiosqlite``'s implicit per-connection lock;
  a single connection is opened on ``init`` and closed on ``close`` —
  never two, never leaked across events.
* ``PRAGMA journal_mode=WAL`` and ``synchronous=NORMAL`` mirror the
  sibling plugins so background crawlers and the REST surface can read
  while the LLM pass writes scores.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

from finpulse_models import DEFAULT_CRONS, SOURCE_DEFS

logger = logging.getLogger(__name__)


# ── Schema ───────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    mode TEXT NOT NULL,
    pipeline_step TEXT,
    scheduled_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    error_kind TEXT,
    error_message TEXT,
    error_hints_json TEXT,
    progress REAL NOT NULL DEFAULT 0,
    params_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    origin_plugin_id TEXT,
    origin_task_id TEXT,
    asset_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status_mode ON tasks(status, mode);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT,
    content TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    raw_json TEXT,
    ai_tags_json TEXT,
    ai_score REAL,
    sentiment TEXT,
    tickers_json TEXT,
    simhash TEXT,
    dedupe_primary_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_event_time ON articles(COALESCE(published_at, fetched_at) DESC);
CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(ai_score DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS digests (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    session TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    title TEXT,
    markdown_blob TEXT,
    html_blob TEXT,
    push_results_json TEXT,
    stats_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_digests_session ON digests(session, generated_at DESC);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- v2.0 reserved (created on init, NEVER written in v1.0).
CREATE TABLE IF NOT EXISTS assets_bus (
    asset_id TEXT PRIMARY KEY,
    kind TEXT,
    mime TEXT,
    path TEXT,
    meta_json TEXT,
    created_at TEXT
);
"""

_ARTICLE_EVENT_TIME_SQL = (
    "CASE "
    "WHEN published_at GLOB '????-??-??T??:??*' THEN published_at "
    "WHEN published_at GLOB '????-??-?? ??:??*' THEN REPLACE(published_at, ' ', 'T') "
    "ELSE fetched_at "
    "END"
)


# ── Default config ───────────────────────────────────────────────────────
#
# Source-level ``{source_id}.enabled`` flags seed from SOURCE_DEFS. Anything
# LLM-provider-specific stays out: fin-pulse never manages LLM credentials
# directly — we delegate to the host LLMClient via ``api.get_brain()``.

def _seed_default_config() -> dict[str, str]:
    cfg: dict[str, str] = {
        # General
        "fetch_timeout_sec": "15",
        "fetch_concurrency": "4",
        "task_retention_days": "60",
        # LLM task preferences (values consumed by finpulse_ai)
        "llm_model_hint": "",
        "llm_max_tokens": "1200",
        "llm_temperature": "0.2",
        "llm_batch_size": "10",
        # AI filter cache invalidation key (sha256 of ai_interests.txt)
        "ai_interests": "",
        "ai_interests_sha256": "",
        # Cross-source dedupe switch; LLM topic clustering is off by
        # default to avoid unexpected LLM spend.
        "dedupe.url_merge": "true",
        "dedupe.use_llm": "false",
        "dedupe.topic_top_k": "60",
        # NewsNow integration — public aggregator is enabled by default so
        # fresh installs light up hot-list coverage without extra config.
        # ``api_url`` is pinned to the upstream community instance; users
        # can override it via /config if they self-host. ``min_interval_s``
        # is a server-enforced floor that prevents probe-hammering the
        # volunteer-run public node (see pipeline._fetch_one).
        "newsnow.mode": "public",  # off | public | self_host
        "newsnow.api_url": "https://newsnow.busiyi.world/api/s",
        "newsnow.min_interval_s": "300",  # 5 minutes — hard UI floor
        "newsnow.last_fetch_ts": "",  # unix seconds (str) of last ok fetch
        # Daily-brief schedules — disabled by default; Settings toggles
        # flip these on and call POST /schedules to hand a task to the
        # host scheduler.
        "schedule.morning.enabled": "false",
        "schedule.morning.cron": DEFAULT_CRONS["morning"],
        "schedule.morning.channel": "",
        "schedule.morning.chat_id": "",
        "schedule.noon.enabled": "false",
        "schedule.noon.cron": DEFAULT_CRONS["noon"],
        "schedule.noon.channel": "",
        "schedule.noon.chat_id": "",
        "schedule.evening.enabled": "false",
        "schedule.evening.cron": DEFAULT_CRONS["evening"],
        "schedule.evening.channel": "",
        "schedule.evening.chat_id": "",
    }
    # Per-source enable flags + last-health probes.
    for source_id, meta in SOURCE_DEFS.items():
        cfg[f"source.{source_id}.enabled"] = "true" if meta["default_enabled"] else "false"
        cfg[f"source.{source_id}.last_ok"] = ""
        cfg[f"source.{source_id}.last_error"] = ""
    return cfg


DEFAULT_CONFIG: dict[str, str] = _seed_default_config()


# ── Update whitelist ─────────────────────────────────────────────────────
#
# Logical key → physical column. Keys listed in ``_JSON_KEYS`` are
# ``json.dumps``-ed on write and ``json.loads``-ed on read.

_UPDATABLE_COLUMNS: dict[str, str] = {
    "status": "status",
    "pipeline_step": "pipeline_step",
    "progress": "progress",
    "params": "params_json",
    "result": "result_json",
    "scheduled_at": "scheduled_at",
    "started_at": "started_at",
    "finished_at": "finished_at",
    "error_kind": "error_kind",
    "error_message": "error_message",
    "error_hints": "error_hints_json",
    "completed_at": "completed_at",
    "asset_id": "asset_id",
    "origin_plugin_id": "origin_plugin_id",
    "origin_task_id": "origin_task_id",
}
_JSON_KEYS: frozenset[str] = frozenset({"params", "result", "error_hints"})


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def _article_id(source_id: str, url_hash: str) -> str:
    """Deterministic article id derived from source + url hash so the
    same physical article can be looked up across ingest runs without
    touching ``articles.url_hash`` (kept unique for dedupe).
    """
    seed = f"{source_id}:{url_hash[:16]}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


# ── Task manager ─────────────────────────────────────────────────────────


class FinpulseTaskManager:
    """Async SQLite manager owning the four primary fin-pulse tables.

    Lifecycle:
    * :meth:`init` opens the connection, runs :data:`SCHEMA_SQL`, seeds
      default config.
    * :meth:`close` cleanly closes the connection (called from
      ``on_unload``).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA_SQL)
        await self._migrate_v2()
        await self._init_default_config()
        await self._db.commit()

    async def _migrate_v2(self) -> None:
        """Add columns introduced in the backend source refactor."""
        assert self._db is not None
        cols = {
            row[1]
            for row in await self._db.execute_fetchall("PRAGMA table_info(articles)")
        }
        if "is_stale" not in cols:
            await self._db.execute(
                "ALTER TABLE articles ADD COLUMN is_stale INTEGER DEFAULT 0"
            )
        if "extra_json" not in cols:
            await self._db.execute(
                "ALTER TABLE articles ADD COLUMN extra_json TEXT DEFAULT '{}'"
            )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _init_default_config(self) -> None:
        assert self._db is not None
        now = time.time()
        for key, val in DEFAULT_CONFIG.items():
            await self._db.execute(
                "INSERT OR IGNORE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                (key, val, now),
            )
        for key in (
            "newsnow.mode",
            "newsnow.api_url",
            "newsnow.min_interval_s",
        ):
            default_val = DEFAULT_CONFIG.get(key, "")
            if not default_val:
                continue
            await self._db.execute(
                "UPDATE config SET value = ?, updated_at = ? "
                "WHERE key = ? AND TRIM(value) = ''",
                (default_val, now, key),
            )

    # ── Config ───────────────────────────────────────────────────────

    async def get_config(self, key: str) -> str:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT value FROM config WHERE key = ?", (key,)
        )
        if rows:
            return rows[0][0]
        return DEFAULT_CONFIG.get(key, "")

    async def get_all_config(self) -> dict[str, str]:
        assert self._db is not None
        rows = await self._db.execute_fetchall("SELECT key, value FROM config")
        return {r[0]: r[1] for r in rows}

    async def set_config(self, key: str, value: str) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, time.time()),
        )
        await self._db.commit()

    async def set_configs(self, updates: dict[str, str]) -> None:
        assert self._db is not None
        now = time.time()
        for k, v in updates.items():
            await self._db.execute(
                "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                (k, v, now),
            )
        await self._db.commit()

    # ── Tasks ────────────────────────────────────────────────────────

    async def create_task(
        self,
        *,
        mode: str,
        params: dict[str, Any] | None = None,
        status: str = "pending",
        task_id: str | None = None,
        scheduled_at: str | None = None,
        origin_plugin_id: str | None = None,
        origin_task_id: str | None = None,
    ) -> dict[str, Any]:
        assert self._db is not None
        task_id = task_id or _short_id()
        now = time.time()
        row = {
            "id": task_id,
            "mode": mode,
            "status": status,
            "params_json": json.dumps(params or {}, ensure_ascii=False),
            "scheduled_at": scheduled_at,
            "origin_plugin_id": origin_plugin_id,
            "origin_task_id": origin_task_id,
            "created_at": now,
            "updated_at": now,
        }
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        await self._db.execute(
            f"INSERT INTO tasks ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        await self._db.commit()
        return await self.get_task(task_id) or {}

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def list_tasks(
        self,
        *,
        mode: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        assert self._db is not None
        wheres: list[str] = []
        args: list[Any] = []
        if mode:
            wheres.append("mode = ?")
            args.append(mode)
        if status:
            wheres.append("status = ?")
            args.append(status)
        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""

        count_rows = await self._db.execute_fetchall(
            f"SELECT COUNT(*) FROM tasks{where_sql}", args
        )
        total = int(count_rows[0][0]) if count_rows else 0

        rows = await self._db.execute_fetchall(
            f"SELECT * FROM tasks{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*args, int(limit), int(offset)],
        )
        return [self._row_to_task(r) for r in rows], total

    async def update_task_safe(self, task_id: str, **updates: Any) -> bool:
        """Strict-whitelist UPDATE — see ``_UPDATABLE_COLUMNS`` for the
        accepted keys. Raises ``ValueError`` on unknown column names so a
        typo surfaces in tests instead of being silently dropped.
        """
        assert self._db is not None
        if not updates:
            return False
        sets: list[str] = []
        args: list[Any] = []
        for k, v in updates.items():
            col = _UPDATABLE_COLUMNS.get(k)
            if col is None:
                raise ValueError(
                    f"update_task_safe: column '{k}' is not whitelisted "
                    f"(allowed: {sorted(_UPDATABLE_COLUMNS)})"
                )
            sets.append(f"{col} = ?")
            if k in _JSON_KEYS:
                args.append(json.dumps(v, ensure_ascii=False))
            else:
                args.append(v)
        sets.append("updated_at = ?")
        args.append(time.time())
        args.append(task_id)
        result = await self._db.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", args
        )
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def delete_task(self, task_id: str) -> bool:
        assert self._db is not None
        result = await self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def cleanup_expired(self, retention_days: int) -> int:
        assert self._db is not None
        if retention_days <= 0:
            return 0
        cutoff = time.time() - (retention_days * 86400)
        result = await self._db.execute(
            "DELETE FROM tasks WHERE completed_at IS NOT NULL AND completed_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return int(result.rowcount or 0)

    @staticmethod
    def _row_to_task(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json") or "{}")
        res_raw = d.pop("result_json", None)
        d["result"] = json.loads(res_raw) if res_raw else None
        hints_raw = d.pop("error_hints_json", None)
        d["error_hints"] = json.loads(hints_raw) if hints_raw else []
        return d

    # ── Articles ─────────────────────────────────────────────────────

    async def upsert_article(
        self,
        *,
        source_id: str,
        url: str,
        url_hash: str,
        title: str,
        fetched_at: str,
        summary: str | None = None,
        content: str | None = None,
        published_at: str | None = None,
        raw: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """Insert or update an article keyed by ``url_hash``.

        Returns ``(article_id, inserted)`` where ``inserted`` is True on
        a fresh row and False on conflict (the row was UPDATEd in place).
        """
        assert self._db is not None
        article_id = _article_id(source_id, url_hash)
        raw_json = json.dumps(raw or {}, ensure_ascii=False)

        rows = await self._db.execute_fetchall(
            "SELECT id, published_at, raw_json, title, extra_json "
            "FROM articles WHERE url_hash = ?",
            (url_hash,),
        )
        if rows:
            existing_id = rows[0][0]
            existing_pub = rows[0][1]
            existing_raw = json.loads(rows[0][2] or "{}")
            existing_title = rows[0][3] or ""
            existing_extra = json.loads(rows[0][4] or "{}")
            prior_rows = await self._db.execute_fetchall(
                "SELECT source_id FROM articles WHERE id = ?", (existing_id,)
            )
            prior_source = prior_rows[0][0] if prior_rows else source_id
            also_seen: list[str] = list(existing_raw.get("also_seen_from", []))
            if source_id != prior_source and source_id not in also_seen:
                also_seen.append(source_id)
            merged = {**existing_raw, **(raw or {})}
            if also_seen:
                merged["also_seen_from"] = also_seen

            if title and existing_title and title != existing_title:
                history: list[dict[str, str]] = existing_extra.get("title_history", [])
                history.append({
                    "old": existing_title,
                    "new": title,
                    "at": fetched_at,
                })
                existing_extra["title_history"] = history[-10:]

            newer_pub = (
                published_at
                if published_at and (not existing_pub or published_at > existing_pub)
                else existing_pub
            )
            await self._db.execute(
                "UPDATE articles SET title = ?, summary = ?, content = ?, "
                "published_at = ?, fetched_at = ?, raw_json = ?, extra_json = ? "
                "WHERE id = ?",
                (
                    title,
                    summary,
                    content,
                    newer_pub,
                    fetched_at,
                    json.dumps(merged, ensure_ascii=False),
                    json.dumps(existing_extra, ensure_ascii=False),
                    existing_id,
                ),
            )
            await self._db.commit()
            return existing_id, False

        await self._db.execute(
            "INSERT INTO articles (id, source_id, url, url_hash, title, summary, "
            "content, published_at, fetched_at, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                article_id,
                source_id,
                url,
                url_hash,
                title,
                summary,
                content,
                published_at,
                fetched_at,
                raw_json,
            ),
        )
        await self._db.commit()
        return article_id, True

    async def mark_stale_articles(self, cutoffs: dict[str, str]) -> int:
        """Mark articles older than their source's freshness ceiling.

        ``cutoffs`` maps ``source_id`` → ISO timestamp string.  Articles
        from that source with sortable ``published_at < cutoff`` (or
        ``fetched_at`` if ``published_at`` is missing / non-ISO) get
        ``is_stale = 1``.

        Returns the total number of rows updated.
        """
        assert self._db is not None
        total = 0
        for sid, cutoff in cutoffs.items():
            cur = await self._db.execute(
                "UPDATE articles SET is_stale = 1 "
                "WHERE source_id = ? AND is_stale = 0 "
                f"AND {_ARTICLE_EVENT_TIME_SQL} < ?",
                (sid, cutoff),
            )
            total += cur.rowcount
        if total:
            await self._db.commit()
        return total

    async def get_article(self, article_id: str) -> dict[str, Any] | None:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        )
        if not rows:
            return None
        return self._row_to_article(rows[0])

    async def list_articles(
        self,
        *,
        source_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
        min_score: float | None = None,
        sort: str = "time",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        assert self._db is not None
        wheres: list[str] = []
        args: list[Any] = []
        if source_id:
            wheres.append("source_id = ?")
            args.append(source_id)
        if since:
            wheres.append(f"{_ARTICLE_EVENT_TIME_SQL} >= ?")
            args.append(since)
        if until:
            wheres.append(f"{_ARTICLE_EVENT_TIME_SQL} <= ?")
            args.append(until)
        if q:
            wheres.append("(title LIKE ? OR summary LIKE ?)")
            like = f"%{q}%"
            args.extend([like, like])
        if min_score is not None:
            wheres.append("ai_score >= ?")
            args.append(float(min_score))
        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        sort_key = (sort or "time_desc").lower()
        direction = "ASC" if sort_key in {"time_asc", "asc", "oldest"} else "DESC"
        order_sql = f" ORDER BY {_ARTICLE_EVENT_TIME_SQL} {direction}"
        count_rows = await self._db.execute_fetchall(
            f"SELECT COUNT(*) FROM articles{where_sql}", args
        )
        total = int(count_rows[0][0]) if count_rows else 0

        rows = await self._db.execute_fetchall(
            f"SELECT * FROM articles{where_sql}{order_sql} LIMIT ? OFFSET ?",
            [*args, int(limit), int(offset)],
        )
        return [self._row_to_article(r) for r in rows], total

    async def update_article_ai(
        self,
        article_id: str,
        *,
        ai_score: float | None = None,
        ai_tags: list[dict[str, Any]] | None = None,
        sentiment: str | None = None,
        tickers: list[str] | None = None,
        simhash: str | None = None,
        dedupe_primary_id: str | None = None,
    ) -> bool:
        assert self._db is not None
        sets: list[str] = []
        args: list[Any] = []
        if ai_score is not None:
            sets.append("ai_score = ?")
            args.append(float(ai_score))
        if ai_tags is not None:
            sets.append("ai_tags_json = ?")
            args.append(json.dumps(ai_tags, ensure_ascii=False))
        if sentiment is not None:
            sets.append("sentiment = ?")
            args.append(sentiment)
        if tickers is not None:
            sets.append("tickers_json = ?")
            args.append(json.dumps(tickers, ensure_ascii=False))
        if simhash is not None:
            sets.append("simhash = ?")
            args.append(simhash)
        if dedupe_primary_id is not None:
            sets.append("dedupe_primary_id = ?")
            args.append(dedupe_primary_id)
        if not sets:
            return False
        args.append(article_id)
        result = await self._db.execute(
            f"UPDATE articles SET {', '.join(sets)} WHERE id = ?", args
        )
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def reset_ai_scores(self) -> int:
        """Null out ``ai_score`` for every article — used when the
        ``ai_interests`` sha256 changes so the next AI pass re-scores
        everything with the fresh interest profile.
        """
        assert self._db is not None
        result = await self._db.execute("UPDATE articles SET ai_score = NULL")
        await self._db.commit()
        return int(result.rowcount or 0)

    @staticmethod
    def _row_to_article(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        raw = d.pop("raw_json", None)
        d["raw"] = json.loads(raw) if raw else {}
        tags = d.pop("ai_tags_json", None)
        d["ai_tags"] = json.loads(tags) if tags else []
        tickers = d.pop("tickers_json", None)
        d["tickers"] = json.loads(tickers) if tickers else []
        return d

    # ── Digests ──────────────────────────────────────────────────────

    async def create_digest(
        self,
        *,
        session: str,
        generated_at: str,
        title: str | None = None,
        markdown_blob: str | None = None,
        html_blob: str | None = None,
        push_results: dict[str, Any] | None = None,
        stats: dict[str, Any] | None = None,
        task_id: str | None = None,
        digest_id: str | None = None,
    ) -> str:
        assert self._db is not None
        did = digest_id or _short_id()
        await self._db.execute(
            "INSERT INTO digests (id, task_id, session, generated_at, title, "
            "markdown_blob, html_blob, push_results_json, stats_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                did,
                task_id,
                session,
                generated_at,
                title,
                markdown_blob,
                html_blob,
                json.dumps(push_results or {}, ensure_ascii=False),
                json.dumps(stats or {}, ensure_ascii=False),
            ),
        )
        await self._db.commit()
        return did

    async def get_digest(self, digest_id: str) -> dict[str, Any] | None:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM digests WHERE id = ?", (digest_id,)
        )
        if not rows:
            return None
        return self._row_to_digest(rows[0])

    async def update_digest_push_results(
        self, digest_id: str, push_results: dict[str, Any]
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE digests SET push_results_json = ? WHERE id = ?",
            (json.dumps(push_results or {}, ensure_ascii=False), digest_id),
        )
        await self._db.commit()

    async def delete_digest(self, digest_id: str) -> bool:
        assert self._db is not None
        result = await self._db.execute("DELETE FROM digests WHERE id = ?", (digest_id,))
        await self._db.commit()
        return (result.rowcount or 0) > 0

    async def list_digests(
        self, *, session: str | None = None, offset: int = 0, limit: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        assert self._db is not None
        wheres: list[str] = []
        args: list[Any] = []
        if session:
            wheres.append("session = ?")
            args.append(session)
        where_sql = (" WHERE " + " AND ".join(wheres)) if wheres else ""
        count_rows = await self._db.execute_fetchall(
            f"SELECT COUNT(*) FROM digests{where_sql}", args
        )
        total = int(count_rows[0][0]) if count_rows else 0
        rows = await self._db.execute_fetchall(
            f"SELECT * FROM digests{where_sql} "
            f"ORDER BY generated_at DESC LIMIT ? OFFSET ?",
            [*args, int(limit), int(offset)],
        )
        return [self._row_to_digest(r) for r in rows], total

    @staticmethod
    def _row_to_digest(row: aiosqlite.Row) -> dict[str, Any]:
        d = dict(row)
        pr_raw = d.pop("push_results_json", None)
        d["push_results"] = json.loads(pr_raw) if pr_raw else {}
        st_raw = d.pop("stats_json", None)
        d["stats"] = json.loads(st_raw) if st_raw else {}
        return d

    # ── assets_bus (v2.0 reserved — never written in V1.0) ───────────

    async def list_assets_bus(self) -> list[dict[str, Any]]:
        assert self._db is not None
        rows = await self._db.execute_fetchall(
            "SELECT * FROM assets_bus ORDER BY created_at"
        )
        return [dict(r) for r in rows]

    async def count_assets_bus(self) -> int:
        assert self._db is not None
        rows = await self._db.execute_fetchall("SELECT COUNT(*) FROM assets_bus")
        return int(rows[0][0]) if rows else 0


__all__ = [
    "DEFAULT_CONFIG",
    "FinpulseTaskManager",
    "SCHEMA_SQL",
]
