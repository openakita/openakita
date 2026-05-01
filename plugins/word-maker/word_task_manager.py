"""SQLite-backed project store for word-maker."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any

import aiosqlite
from word_models import DOC_TYPES, PROJECT_STATUSES, ProjectSpec

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    doc_type            TEXT NOT NULL,
    audience            TEXT NOT NULL DEFAULT '',
    tone                TEXT NOT NULL DEFAULT 'professional',
    language            TEXT NOT NULL DEFAULT 'zh-CN',
    requirements        TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'draft',
    current_version     INTEGER NOT NULL DEFAULT 0,
    output_path         TEXT,
    error_kind          TEXT,
    error_message       TEXT,
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    completed_at        REAL
);

CREATE TABLE IF NOT EXISTS sources (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    filename            TEXT NOT NULL,
    path                TEXT NOT NULL,
    text_preview        TEXT NOT NULL DEFAULT '',
    parse_status        TEXT NOT NULL DEFAULT 'pending',
    error_message       TEXT,
    created_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    label               TEXT NOT NULL,
    path                TEXT NOT NULL,
    vars_json           TEXT NOT NULL DEFAULT '[]',
    validation_json     TEXT NOT NULL DEFAULT '{}',
    created_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS draft_versions (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    version             INTEGER NOT NULL,
    outline_json        TEXT NOT NULL DEFAULT '{}',
    fields_json         TEXT NOT NULL DEFAULT '{}',
    doc_markdown        TEXT NOT NULL DEFAULT '',
    export_path         TEXT,
    audit_json          TEXT NOT NULL DEFAULT '{}',
    created_at          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project_id);
CREATE INDEX IF NOT EXISTS idx_templates_project ON templates(project_id);
CREATE INDEX IF NOT EXISTS idx_versions_project ON draft_versions(project_id, version DESC);
"""

PROJECT_WRITABLE = frozenset(
    {
        "title",
        "audience",
        "tone",
        "language",
        "requirements",
        "status",
        "current_version",
        "output_path",
        "error_kind",
        "error_message",
        "metadata_json",
        "completed_at",
    }
)


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _project_row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["metadata"] = _json_loads(data.pop("metadata_json", "{}"), {})
    return data


def _source_row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def _template_row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["vars"] = _json_loads(data.pop("vars_json", "[]"), [])
    data["validation"] = _json_loads(data.pop("validation_json", "{}"), {})
    return data


def _version_row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key, fallback in (
        ("outline_json", {}),
        ("fields_json", {}),
        ("audit_json", {}),
    ):
        data[key.removesuffix("_json")] = _json_loads(data.pop(key, ""), fallback)
    return data


class WordTaskManager:
    """Project storage with explicit lifecycle and safe updates."""

    def __init__(self, db_path: Path, projects_root: Path) -> None:
        self._db_path = Path(db_path)
        self._projects_root = Path(projects_root)
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> WordTaskManager:
        await self.init()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def init(self) -> None:
        if self._db is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._projects_root.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def project_dir(self, project_id: str) -> Path:
        return self._projects_root / project_id

    def ensure_project_dirs(self, project_id: str) -> dict[str, Path]:
        root = self.project_dir(project_id)
        dirs = {
            "root": root,
            "sources": root / "sources",
            "templates": root / "templates",
            "drafts": root / "drafts",
            "exports": root / "exports",
        }
        for path in dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        return dirs

    async def create_project(self, spec: ProjectSpec | dict[str, Any]) -> dict[str, Any]:
        await self.init()
        assert self._db is not None
        if isinstance(spec, dict):
            spec = ProjectSpec(**spec)
        spec.validate()
        if spec.doc_type not in DOC_TYPES:
            raise ValueError(f"Unsupported doc_type: {spec.doc_type}")
        project_id = _new_id("doc")
        now = _now()
        self.ensure_project_dirs(project_id)
        await self._db.execute(
            """
            INSERT INTO projects (
                id, title, doc_type, audience, tone, language, requirements,
                status, current_version, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', 0, '{}', ?, ?)
            """,
            (
                project_id,
                spec.title.strip(),
                spec.doc_type,
                spec.audience,
                spec.tone,
                spec.language,
                spec.requirements,
                now,
                now,
            ),
        )
        await self._db.commit()
        project = await self.get_project(project_id)
        assert project is not None
        return project

    async def get_project(self, project_id: str) -> dict[str, Any] | None:
        await self.init()
        assert self._db is not None
        async with self._db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)) as cursor:
            return _project_row(await cursor.fetchone())

    async def list_projects(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        await self.init()
        assert self._db is not None
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        if status:
            sql = "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?"
            args: tuple[Any, ...] = (status, limit, offset)
        else:
            sql = "SELECT * FROM projects ORDER BY created_at DESC LIMIT ? OFFSET ?"
            args = (limit, offset)
        async with self._db.execute(sql, args) as cursor:
            rows = await cursor.fetchall()
        return [row for row in (_project_row(item) for item in rows) if row is not None]

    async def update_project_safe(self, project_id: str, **updates: Any) -> dict[str, Any] | None:
        await self.init()
        assert self._db is not None
        if "metadata" in updates:
            updates["metadata_json"] = _json_dumps(updates.pop("metadata"))
        illegal = sorted(set(updates) - PROJECT_WRITABLE)
        if illegal:
            raise ValueError(f"Unsupported project update fields: {', '.join(illegal)}")
        if "status" in updates and updates["status"] not in PROJECT_STATUSES:
            raise ValueError(f"Unsupported project status: {updates['status']}")
        updates["updated_at"] = _now()
        fields = ", ".join(f"{key} = ?" for key in updates)
        values = list(updates.values()) + [project_id]
        await self._db.execute(f"UPDATE projects SET {fields} WHERE id = ?", values)
        await self._db.commit()
        return await self.get_project(project_id)

    async def delete_project(self, project_id: str) -> bool:
        await self.init()
        assert self._db is not None
        await self._db.execute("DELETE FROM sources WHERE project_id = ?", (project_id,))
        await self._db.execute("DELETE FROM templates WHERE project_id = ?", (project_id,))
        await self._db.execute("DELETE FROM draft_versions WHERE project_id = ?", (project_id,))
        cursor = await self._db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await self._db.commit()
        shutil.rmtree(self.project_dir(project_id), ignore_errors=True)
        return cursor.rowcount > 0

    async def add_source(
        self,
        project_id: str,
        *,
        source_type: str,
        filename: str,
        path: str,
        text_preview: str = "",
        parse_status: str = "pending",
        error_message: str | None = None,
    ) -> dict[str, Any]:
        await self.init()
        assert self._db is not None
        source_id = _new_id("src")
        await self._db.execute(
            """
            INSERT INTO sources (
                id, project_id, source_type, filename, path, text_preview,
                parse_status, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                project_id,
                source_type,
                filename,
                path,
                text_preview,
                parse_status,
                error_message,
                _now(),
            ),
        )
        await self._db.commit()
        async with self._db.execute("SELECT * FROM sources WHERE id = ?", (source_id,)) as cursor:
            row = _source_row(await cursor.fetchone())
        assert row is not None
        return row

    async def list_sources(self, project_id: str) -> list[dict[str, Any]]:
        await self.init()
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM sources WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [row for row in (_source_row(item) for item in rows) if row is not None]

    async def add_template(
        self,
        project_id: str,
        *,
        label: str,
        path: str,
        variables: list[str] | None = None,
        validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.init()
        assert self._db is not None
        template_id = _new_id("tpl")
        await self._db.execute(
            """
            INSERT INTO templates (
                id, project_id, label, path, vars_json, validation_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template_id,
                project_id,
                label,
                path,
                json.dumps(variables or [], ensure_ascii=False, sort_keys=True),
                _json_dumps(validation or {}),
                _now(),
            ),
        )
        await self._db.commit()
        async with self._db.execute("SELECT * FROM templates WHERE id = ?", (template_id,)) as cursor:
            row = _template_row(await cursor.fetchone())
        assert row is not None
        return row

    async def add_draft_version(
        self,
        project_id: str,
        *,
        outline: dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
        doc_markdown: str = "",
        export_path: str | None = None,
        audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.init()
        assert self._db is not None
        project = await self.get_project(project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")
        version = int(project.get("current_version") or 0) + 1
        version_id = _new_id("ver")
        await self._db.execute(
            """
            INSERT INTO draft_versions (
                id, project_id, version, outline_json, fields_json, doc_markdown,
                export_path, audit_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                project_id,
                version,
                _json_dumps(outline or {}),
                _json_dumps(fields or {}),
                doc_markdown,
                export_path,
                _json_dumps(audit or {}),
                _now(),
            ),
        )
        await self.update_project_safe(project_id, current_version=version)
        async with self._db.execute(
            "SELECT * FROM draft_versions WHERE id = ?",
            (version_id,),
        ) as cursor:
            row = _version_row(await cursor.fetchone())
        assert row is not None
        return row

    async def list_versions(self, project_id: str) -> list[dict[str, Any]]:
        await self.init()
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM draft_versions WHERE project_id = ? ORDER BY version DESC",
            (project_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [row for row in (_version_row(item) for item in rows) if row is not None]

    async def cleanup_expired(self, *, retention_days: int = 30) -> int:
        await self.init()
        assert self._db is not None
        cutoff = _now() - max(1, retention_days) * 86400
        async with self._db.execute(
            "SELECT id FROM projects WHERE completed_at IS NOT NULL AND completed_at < ?",
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()
        removed = 0
        for row in rows:
            if await self.delete_project(str(row["id"])):
                removed += 1
        return removed

