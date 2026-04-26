"""SQLite store for excel-maker projects and workbook assets."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any

import aiosqlite
from excel_models import (
    ArtifactKind,
    ArtifactRecord,
    AuditItemRecord,
    ProjectCreate,
    ProjectRecord,
    ProjectStatus,
    SheetRecord,
    TemplateRecord,
    TemplateStatus,
    WorkbookRecord,
    WorkbookStatus,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    goal            TEXT NOT NULL DEFAULT '',
    audience        TEXT NOT NULL DEFAULT '',
    period          TEXT NOT NULL DEFAULT '',
    style           TEXT NOT NULL DEFAULT 'business',
    status          TEXT NOT NULL DEFAULT 'draft',
    report_brief_json TEXT NOT NULL DEFAULT '{}',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS workbooks (
    id              TEXT PRIMARY KEY,
    project_id      TEXT,
    filename        TEXT NOT NULL,
    original_path   TEXT NOT NULL,
    imported_path   TEXT,
    profile_path    TEXT,
    status          TEXT NOT NULL DEFAULT 'uploaded',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sheets (
    id                  TEXT PRIMARY KEY,
    workbook_id          TEXT NOT NULL,
    name                TEXT NOT NULL,
    sheet_index          INTEGER NOT NULL DEFAULT 0,
    row_count            INTEGER NOT NULL DEFAULT 0,
    column_count         INTEGER NOT NULL DEFAULT 0,
    header_row           INTEGER,
    data_range           TEXT NOT NULL DEFAULT '',
    formula_count        INTEGER NOT NULL DEFAULT 0,
    merged_range_count   INTEGER NOT NULL DEFAULT 0,
    hidden_row_count     INTEGER NOT NULL DEFAULT 0,
    hidden_column_count  INTEGER NOT NULL DEFAULT 0,
    metadata_json        TEXT NOT NULL DEFAULT '{}',
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    op              TEXT NOT NULL,
    params_json     TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',
    result_json     TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    kind            TEXT NOT NULL,
    path            TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    original_path       TEXT NOT NULL,
    diagnostic_path     TEXT,
    status              TEXT NOT NULL DEFAULT 'created',
    metadata_json       TEXT NOT NULL DEFAULT '{}',
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_items (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    artifact_id     TEXT,
    severity        TEXT NOT NULL DEFAULT 'info',
    category        TEXT NOT NULL DEFAULT 'general',
    message         TEXT NOT NULL,
    location        TEXT NOT NULL DEFAULT '',
    suggestion      TEXT NOT NULL DEFAULT '',
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_created ON projects(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_workbooks_project ON workbooks(project_id);
CREATE INDEX IF NOT EXISTS idx_sheets_workbook ON sheets(workbook_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_id);
CREATE INDEX IF NOT EXISTS idx_audit_project ON audit_items(project_id);
"""


_PROJECT_WRITABLE = frozenset(
    {"title", "goal", "audience", "period", "style", "status", "report_brief_json", "metadata_json"}
)
_WORKBOOK_WRITABLE = frozenset({"imported_path", "profile_path", "status", "metadata_json"})
_TEMPLATE_WRITABLE = frozenset({"diagnostic_path", "status", "metadata_json"})


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


class ExcelTaskManager:
    """SQLite-backed CRUD for report projects and workbook assets."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> ExcelTaskManager:
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
            raise RuntimeError("ExcelTaskManager.init() must be called first")
        return self._db

    async def create_project(self, payload: ProjectCreate) -> ProjectRecord:
        now = _now()
        project_id = _new_id("proj")
        await self._conn.execute(
            """
            INSERT INTO projects
                (id, title, goal, audience, period, style, status, report_brief_json,
                 metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                payload.title,
                payload.goal,
                payload.audience,
                payload.period,
                payload.style,
                ProjectStatus.DRAFT.value,
                "{}",
                _json(payload.metadata),
                now,
                now,
            ),
        )
        await self._conn.commit()
        project = await self.get_project(project_id)
        assert project is not None
        return project

    async def get_project(self, project_id: str) -> ProjectRecord | None:
        row = await self._fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        return self._project_from_row(row) if row else None

    async def list_projects(self) -> list[ProjectRecord]:
        rows = await self._fetchall("SELECT * FROM projects ORDER BY created_at DESC")
        return [self._project_from_row(row) for row in rows]

    async def update_project_safe(self, project_id: str, **fields: Any) -> ProjectRecord | None:
        converted: dict[str, Any] = {}
        for key, value in fields.items():
            column = f"{key}_json" if key in {"metadata", "report_brief"} else key
            if column not in _PROJECT_WRITABLE:
                raise ValueError(f"Unsupported project field: {key}")
            if column.endswith("_json"):
                value = _json(value)
            elif key == "status" and isinstance(value, ProjectStatus):
                value = value.value
            converted[column] = value
        if not converted:
            return await self.get_project(project_id)
        converted["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in converted)
        await self._conn.execute(
            f"UPDATE projects SET {assignments} WHERE id = ?",
            (*converted.values(), project_id),
        )
        await self._conn.commit()
        return await self.get_project(project_id)

    async def delete_project(self, project_id: str) -> bool:
        cursor = await self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await self._conn.execute("DELETE FROM workbooks WHERE project_id = ?", (project_id,))
        await self._conn.execute("DELETE FROM artifacts WHERE project_id = ?", (project_id,))
        await self._conn.execute("DELETE FROM audit_items WHERE project_id = ?", (project_id,))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def create_workbook(
        self,
        *,
        filename: str,
        original_path: str,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkbookRecord:
        now = _now()
        workbook_id = _new_id("wb")
        await self._conn.execute(
            """
            INSERT INTO workbooks
                (id, project_id, filename, original_path, status, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workbook_id,
                project_id,
                filename,
                original_path,
                WorkbookStatus.UPLOADED.value,
                _json(metadata or {}),
                now,
                now,
            ),
        )
        await self._conn.commit()
        workbook = await self.get_workbook(workbook_id)
        assert workbook is not None
        return workbook

    async def get_workbook(self, workbook_id: str) -> WorkbookRecord | None:
        row = await self._fetchone("SELECT * FROM workbooks WHERE id = ?", (workbook_id,))
        return self._workbook_from_row(row) if row else None

    async def list_workbooks(self, project_id: str | None = None) -> list[WorkbookRecord]:
        if project_id:
            rows = await self._fetchall(
                "SELECT * FROM workbooks WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            )
        else:
            rows = await self._fetchall("SELECT * FROM workbooks ORDER BY created_at DESC")
        return [self._workbook_from_row(row) for row in rows]

    async def update_workbook_safe(self, workbook_id: str, **fields: Any) -> WorkbookRecord | None:
        converted: dict[str, Any] = {}
        for key, value in fields.items():
            column = f"{key}_json" if key == "metadata" else key
            if column not in _WORKBOOK_WRITABLE:
                raise ValueError(f"Unsupported workbook field: {key}")
            if column.endswith("_json"):
                value = _json(value)
            elif key == "status" and isinstance(value, WorkbookStatus):
                value = value.value
            converted[column] = value
        converted["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in converted)
        await self._conn.execute(
            f"UPDATE workbooks SET {assignments} WHERE id = ?",
            (*converted.values(), workbook_id),
        )
        await self._conn.commit()
        return await self.get_workbook(workbook_id)

    async def replace_sheets(self, workbook_id: str, sheets: list[dict[str, Any]]) -> list[SheetRecord]:
        await self._conn.execute("DELETE FROM sheets WHERE workbook_id = ?", (workbook_id,))
        now = _now()
        for index, sheet in enumerate(sheets):
            await self._conn.execute(
                """
                INSERT INTO sheets
                    (id, workbook_id, name, sheet_index, row_count, column_count, header_row,
                     data_range, formula_count, merged_range_count, hidden_row_count,
                     hidden_column_count, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("sheet"),
                    workbook_id,
                    str(sheet.get("name") or f"Sheet{index + 1}"),
                    index,
                    int(sheet.get("row_count") or 0),
                    int(sheet.get("column_count") or 0),
                    sheet.get("header_row"),
                    str(sheet.get("data_range") or ""),
                    int(sheet.get("formula_count") or 0),
                    int(sheet.get("merged_range_count") or 0),
                    int(sheet.get("hidden_row_count") or 0),
                    int(sheet.get("hidden_column_count") or 0),
                    _json(sheet.get("metadata") or {}),
                    now,
                    now,
                ),
            )
        await self._conn.commit()
        return await self.list_sheets(workbook_id)

    async def list_sheets(self, workbook_id: str) -> list[SheetRecord]:
        rows = await self._fetchall(
            "SELECT * FROM sheets WHERE workbook_id = ? ORDER BY sheet_index ASC",
            (workbook_id,),
        )
        return [self._sheet_from_row(row) for row in rows]

    async def create_artifact(
        self,
        *,
        project_id: str,
        kind: ArtifactKind,
        path: str,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        version = await self.next_artifact_version(project_id, kind)
        now = _now()
        artifact_id = _new_id("art")
        await self._conn.execute(
            """
            INSERT INTO artifacts (id, project_id, kind, path, version, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (artifact_id, project_id, kind.value, path, version, _json(metadata or {}), now),
        )
        await self._conn.commit()
        artifact = await self.get_artifact(artifact_id)
        assert artifact is not None
        return artifact

    async def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        row = await self._fetchone("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
        return self._artifact_from_row(row) if row else None

    async def list_artifacts(self, project_id: str) -> list[ArtifactRecord]:
        rows = await self._fetchall(
            "SELECT * FROM artifacts WHERE project_id = ? ORDER BY version DESC, created_at DESC",
            (project_id,),
        )
        return [self._artifact_from_row(row) for row in rows]

    async def next_artifact_version(self, project_id: str, kind: ArtifactKind) -> int:
        row = await self._fetchone(
            "SELECT MAX(version) AS max_version FROM artifacts WHERE project_id = ? AND kind = ?",
            (project_id, kind.value),
        )
        return int((row["max_version"] if row else None) or 0) + 1

    async def replace_audit_items(
        self,
        project_id: str,
        items: list[dict[str, Any]],
        artifact_id: str | None = None,
    ) -> list[AuditItemRecord]:
        await self._conn.execute("DELETE FROM audit_items WHERE project_id = ?", (project_id,))
        now = _now()
        for item in items:
            await self._conn.execute(
                """
                INSERT INTO audit_items
                    (id, project_id, artifact_id, severity, category, message, location,
                     suggestion, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id("audit"),
                    project_id,
                    artifact_id,
                    item.get("severity", "info"),
                    item.get("category", "general"),
                    item.get("message", ""),
                    item.get("location", ""),
                    item.get("suggestion", ""),
                    _json(item.get("metadata") or {}),
                    now,
                ),
            )
        await self._conn.commit()
        return await self.list_audit_items(project_id)

    async def list_audit_items(self, project_id: str) -> list[AuditItemRecord]:
        rows = await self._fetchall(
            "SELECT * FROM audit_items WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        )
        return [self._audit_from_row(row) for row in rows]

    async def create_template(
        self, *, name: str, original_path: str, metadata: dict[str, Any] | None = None
    ) -> TemplateRecord:
        now = _now()
        template_id = _new_id("tpl")
        await self._conn.execute(
            """
            INSERT INTO templates
                (id, name, original_path, status, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template_id,
                name,
                original_path,
                TemplateStatus.CREATED.value,
                _json(metadata or {}),
                now,
                now,
            ),
        )
        await self._conn.commit()
        template = await self.get_template(template_id)
        assert template is not None
        return template

    async def get_template(self, template_id: str) -> TemplateRecord | None:
        row = await self._fetchone("SELECT * FROM templates WHERE id = ?", (template_id,))
        return self._template_from_row(row) if row else None

    async def list_templates(self) -> list[TemplateRecord]:
        rows = await self._fetchall("SELECT * FROM templates ORDER BY created_at DESC")
        return [self._template_from_row(row) for row in rows]

    async def update_template_safe(self, template_id: str, **fields: Any) -> TemplateRecord | None:
        converted: dict[str, Any] = {}
        for key, value in fields.items():
            column = f"{key}_json" if key == "metadata" else key
            if column not in _TEMPLATE_WRITABLE:
                raise ValueError(f"Unsupported template field: {key}")
            if column.endswith("_json"):
                value = _json(value)
            elif key == "status" and isinstance(value, TemplateStatus):
                value = value.value
            converted[column] = value
        converted["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in converted)
        await self._conn.execute(
            f"UPDATE templates SET {assignments} WHERE id = ?",
            (*converted.values(), template_id),
        )
        await self._conn.commit()
        return await self.get_template(template_id)

    async def delete_template(self, template_id: str) -> bool:
        cursor = await self._conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        await self._conn.commit()
        return cursor.rowcount > 0

    async def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        async with self._conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        async with self._conn.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    def _project_from_row(self, row: aiosqlite.Row) -> ProjectRecord:
        return ProjectRecord(
            id=row["id"],
            title=row["title"],
            goal=row["goal"],
            audience=row["audience"],
            period=row["period"],
            style=row["style"],
            status=ProjectStatus(row["status"]),
            report_brief=_loads(row["report_brief_json"], {}),
            metadata=_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _workbook_from_row(self, row: aiosqlite.Row) -> WorkbookRecord:
        return WorkbookRecord(
            id=row["id"],
            project_id=row["project_id"],
            filename=row["filename"],
            original_path=row["original_path"],
            imported_path=row["imported_path"],
            profile_path=row["profile_path"],
            status=WorkbookStatus(row["status"]),
            metadata=_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _sheet_from_row(self, row: aiosqlite.Row) -> SheetRecord:
        return SheetRecord(
            id=row["id"],
            workbook_id=row["workbook_id"],
            name=row["name"],
            index=row["sheet_index"],
            row_count=row["row_count"],
            column_count=row["column_count"],
            header_row=row["header_row"],
            data_range=row["data_range"],
            formula_count=row["formula_count"],
            merged_range_count=row["merged_range_count"],
            hidden_row_count=row["hidden_row_count"],
            hidden_column_count=row["hidden_column_count"],
            metadata=_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _artifact_from_row(self, row: aiosqlite.Row) -> ArtifactRecord:
        return ArtifactRecord(
            id=row["id"],
            project_id=row["project_id"],
            kind=ArtifactKind(row["kind"]),
            path=row["path"],
            version=row["version"],
            metadata=_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
        )

    def _audit_from_row(self, row: aiosqlite.Row) -> AuditItemRecord:
        return AuditItemRecord(
            id=row["id"],
            project_id=row["project_id"],
            artifact_id=row["artifact_id"],
            severity=row["severity"],
            category=row["category"],
            message=row["message"],
            location=row["location"],
            suggestion=row["suggestion"],
            metadata=_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
        )

    def _template_from_row(self, row: aiosqlite.Row) -> TemplateRecord:
        return TemplateRecord(
            id=row["id"],
            name=row["name"],
            original_path=row["original_path"],
            diagnostic_path=row["diagnostic_path"],
            status=TemplateStatus(row["status"]),
            metadata=_loads(row["metadata_json"], {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

