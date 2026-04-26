"""SQLite project store for ppt-maker."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any

import aiosqlite

from ppt_models import (
    DatasetRecord,
    DeckMode,
    ProjectCreate,
    ProjectRecord,
    ProjectStatus,
    SourceRecord,
    SourceStatus,
    TaskCreate,
    TaskRecord,
    TaskStatus,
    TemplateCategory,
    TemplateRecord,
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    mode            TEXT NOT NULL,
    title           TEXT NOT NULL,
    prompt          TEXT NOT NULL DEFAULT '',
    audience        TEXT NOT NULL DEFAULT '',
    style           TEXT NOT NULL DEFAULT 'tech_business',
    slide_count     INTEGER NOT NULL DEFAULT 8,
    status          TEXT NOT NULL DEFAULT 'draft',
    template_id     TEXT,
    dataset_id      TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    project_id      TEXT,
    kind            TEXT NOT NULL,
    filename        TEXT NOT NULL,
    path            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'uploaded',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    id                TEXT PRIMARY KEY,
    project_id        TEXT,
    name              TEXT NOT NULL,
    original_path     TEXT NOT NULL,
    profile_path      TEXT,
    insights_path     TEXT,
    chart_specs_path  TEXT,
    status            TEXT NOT NULL DEFAULT 'created',
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    category            TEXT,
    original_path       TEXT,
    profile_path        TEXT,
    brand_tokens_path   TEXT,
    layout_map_path     TEXT,
    status              TEXT NOT NULL DEFAULT 'created',
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS outlines (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    outline_json    TEXT NOT NULL DEFAULT '{}',
    confirmed       INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS design_specs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    design_markdown TEXT NOT NULL DEFAULT '',
    spec_lock_json  TEXT NOT NULL DEFAULT '{}',
    confirmed       INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS slides (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    slide_index     INTEGER NOT NULL,
    slide_type      TEXT NOT NULL,
    slide_json      TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS exports (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'pptx',
    path            TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    project_id        TEXT,
    task_type         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    progress          REAL NOT NULL DEFAULT 0,
    params_json       TEXT NOT NULL DEFAULT '{}',
    result_json       TEXT NOT NULL DEFAULT '{}',
    error_kind        TEXT,
    error_message     TEXT,
    error_hints_json  TEXT NOT NULL DEFAULT '[]',
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    completed_at      REAL
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_mode ON projects(mode);
CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project_id);
CREATE INDEX IF NOT EXISTS idx_datasets_project ON datasets(project_id);
CREATE INDEX IF NOT EXISTS idx_templates_status ON templates(status);
"""

_PROJECT_WRITABLE = frozenset(
    {
        "title",
        "prompt",
        "audience",
        "style",
        "slide_count",
        "status",
        "template_id",
        "dataset_id",
        "metadata_json",
    }
)
_TASK_WRITABLE = frozenset(
    {
        "status",
        "progress",
        "result_json",
        "error_kind",
        "error_message",
        "error_hints_json",
        "completed_at",
    }
)


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _row_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class PptTaskManager:
    """SQLite-backed CRUD for ppt-maker projects and tasks."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> PptTaskManager:
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
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA_SQL)
        await self._migrate_projects()
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            try:
                await self._db.close()
            finally:
                self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("PptTaskManager.init() must be called first")
        return self._db

    async def _migrate_projects(self) -> None:
        async with self._conn.execute("PRAGMA table_info(projects)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        for name, ddl in {
            "template_id": "ALTER TABLE projects ADD COLUMN template_id TEXT",
            "dataset_id": "ALTER TABLE projects ADD COLUMN dataset_id TEXT",
            "metadata_json": "ALTER TABLE projects ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
        }.items():
            if name not in cols:
                await self._conn.execute(ddl)

    async def create_project(self, data: ProjectCreate) -> ProjectRecord:
        now = _now()
        project_id = _new_id("ppt")
        await self._conn.execute(
            """
            INSERT INTO projects (
                id, mode, title, prompt, audience, style, slide_count, status,
                template_id, dataset_id, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                data.mode.value,
                data.title,
                data.prompt,
                data.audience,
                data.style,
                data.slide_count,
                ProjectStatus.DRAFT.value,
                data.template_id,
                data.dataset_id,
                _json(data.metadata),
                now,
                now,
            ),
        )
        await self._conn.commit()
        record = await self.get_project(project_id)
        if record is None:
            raise RuntimeError("Project insert failed")
        return record

    async def get_project(self, project_id: str) -> ProjectRecord | None:
        async with self._conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)) as cur:
            row = _row_dict(await cur.fetchone())
        if row is None:
            return None
        return self._project_record(row)

    async def list_projects(self, *, limit: int = 50) -> list[ProjectRecord]:
        async with self._conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = [_row_dict(row) for row in await cur.fetchall()]
        return [self._project_record(row) for row in rows if row is not None]

    async def update_project_safe(self, project_id: str, **updates: Any) -> ProjectRecord | None:
        if not updates:
            return await self.get_project(project_id)
        if "metadata" in updates:
            updates["metadata_json"] = _json(updates.pop("metadata"))
        bad = set(updates) - _PROJECT_WRITABLE
        if bad:
            raise ValueError(f"Unsupported project update columns: {sorted(bad)}")
        if "status" in updates:
            updates["status"] = ProjectStatus(updates["status"]).value
        if "metadata_json" in updates and not isinstance(updates["metadata_json"], str):
            updates["metadata_json"] = _json(updates["metadata_json"])
        updates["updated_at"] = _now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        await self._conn.execute(
            f"UPDATE projects SET {sets} WHERE id = ?",
            [*updates.values(), project_id],
        )
        await self._conn.commit()
        return await self.get_project(project_id)

    async def create_task(self, data: TaskCreate) -> TaskRecord:
        now = _now()
        task_id = _new_id("task")
        await self._conn.execute(
            """
            INSERT INTO tasks (
                id, project_id, task_type, status, progress, params_json,
                result_json, error_hints_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                data.project_id,
                data.task_type,
                TaskStatus.PENDING.value,
                0,
                _json(data.params),
                "{}",
                "[]",
                now,
                now,
            ),
        )
        await self._conn.commit()
        record = await self.get_task(task_id)
        if record is None:
            raise RuntimeError("Task insert failed")
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        async with self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            row = _row_dict(await cur.fetchone())
        if row is None:
            return None
        return self._task_record(row)

    async def update_task_safe(self, task_id: str, **updates: Any) -> TaskRecord | None:
        if not updates:
            return await self.get_task(task_id)
        bad = set(updates) - _TASK_WRITABLE - {"result", "error_hints"}
        if bad:
            raise ValueError(f"Unsupported task update columns: {sorted(bad)}")
        if "status" in updates:
            updates["status"] = TaskStatus(updates["status"]).value
            if updates["status"] in {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
                updates.setdefault("completed_at", _now())
        if "result" in updates:
            updates["result_json"] = _json(updates.pop("result"))
        if "error_hints" in updates:
            updates["error_hints_json"] = _json(updates.pop("error_hints"))
        updates["updated_at"] = _now()
        sets = ", ".join(f"{key} = ?" for key in updates)
        await self._conn.execute(
            f"UPDATE tasks SET {sets} WHERE id = ?",
            [*updates.values(), task_id],
        )
        await self._conn.commit()
        return await self.get_task(task_id)

    async def create_source(
        self,
        *,
        kind: str,
        filename: str,
        path: str,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SourceRecord:
        now = _now()
        source_id = _new_id("src")
        await self._conn.execute(
            """
            INSERT INTO sources (
                id, project_id, kind, filename, path, status, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                project_id,
                kind,
                filename,
                path,
                SourceStatus.UPLOADED.value,
                _json(metadata),
                now,
                now,
            ),
        )
        await self._conn.commit()
        async with self._conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)) as cur:
            row = _row_dict(await cur.fetchone())
        if row is None:
            raise RuntimeError("Source insert failed")
        return self._source_record(row)

    async def create_dataset(
        self,
        *,
        name: str,
        original_path: str,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DatasetRecord:
        now = _now()
        dataset_id = _new_id("ds")
        await self._conn.execute(
            """
            INSERT INTO datasets (
                id, project_id, name, original_path, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (dataset_id, project_id, name, original_path, _json(metadata), now, now),
        )
        await self._conn.commit()
        async with self._conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)) as cur:
            row = _row_dict(await cur.fetchone())
        if row is None:
            raise RuntimeError("Dataset insert failed")
        return self._dataset_record(row)

    async def create_template(
        self,
        *,
        name: str,
        category: TemplateCategory | str | None = None,
        original_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TemplateRecord:
        now = _now()
        template_id = _new_id("tpl")
        category_value = TemplateCategory(category).value if category else None
        await self._conn.execute(
            """
            INSERT INTO templates (
                id, name, category, original_path, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (template_id, name, category_value, original_path, _json(metadata), now, now),
        )
        await self._conn.commit()
        async with self._conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)) as cur:
            row = _row_dict(await cur.fetchone())
        if row is None:
            raise RuntimeError("Template insert failed")
        return self._template_record(row)

    def _project_record(self, row: dict[str, Any]) -> ProjectRecord:
        return ProjectRecord(
            id=row["id"],
            mode=DeckMode(row["mode"]),
            title=row["title"],
            prompt=row["prompt"],
            audience=row["audience"],
            style=row["style"],
            slide_count=row["slide_count"],
            status=ProjectStatus(row["status"]),
            template_id=row.get("template_id"),
            dataset_id=row.get("dataset_id"),
            metadata=_loads(row.get("metadata_json"), {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _task_record(self, row: dict[str, Any]) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            project_id=row.get("project_id"),
            task_type=row["task_type"],
            status=TaskStatus(row["status"]),
            progress=row["progress"],
            params=_loads(row.get("params_json"), {}),
            result=_loads(row.get("result_json"), {}),
            error_kind=row.get("error_kind"),
            error_message=row.get("error_message"),
            error_hints=_loads(row.get("error_hints_json"), []),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row.get("completed_at"),
        )

    def _source_record(self, row: dict[str, Any]) -> SourceRecord:
        return SourceRecord(
            id=row["id"],
            project_id=row.get("project_id"),
            kind=row["kind"],
            filename=row["filename"],
            path=row["path"],
            status=SourceStatus(row["status"]),
            metadata=_loads(row.get("metadata_json"), {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _dataset_record(self, row: dict[str, Any]) -> DatasetRecord:
        return DatasetRecord(
            id=row["id"],
            project_id=row.get("project_id"),
            name=row["name"],
            original_path=row["original_path"],
            profile_path=row.get("profile_path"),
            insights_path=row.get("insights_path"),
            chart_specs_path=row.get("chart_specs_path"),
            status=row["status"],
            metadata=_loads(row.get("metadata_json"), {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _template_record(self, row: dict[str, Any]) -> TemplateRecord:
        return TemplateRecord(
            id=row["id"],
            name=row["name"],
            category=TemplateCategory(row["category"]) if row.get("category") else None,
            original_path=row.get("original_path"),
            profile_path=row.get("profile_path"),
            brand_tokens_path=row.get("brand_tokens_path"),
            layout_map_path=row.get("layout_map_path"),
            status=row["status"],
            metadata=_loads(row.get("metadata_json"), {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

