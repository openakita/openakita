"""v2 ProjectStore (P-RC-9 P9.2).

Replaces v1 ``openakita.orgs.project_store.ProjectStore``
(281 LOC, 15 public methods, single JSON-file backend) with a
:class:`typing.Protocol`-typed surface plus pluggable backends
(:class:`JsonProjectStore` here; :class:`SqliteProjectStore` +
``get_default_project_store`` factory in P9.2c) selected by
``settings.orgs_v2_backend``. Public API is 1:1 with v1 so the
P9.8 caller migration is one import-line change.

ID minting switches from v1''s ``uuid.uuid4().hex[:12]`` to a
ULID-style ``<13-digit ms>_<10 hex>`` (see
:mod:`openakita.runtime.orgs.project_models`); parity tests
ignore IDs because the timestamp prefix differs across runs
(P-RC-9-PLAN section 5.2).

Commit split (≤380 LOC per step):

* P9.2a (this commit) -- :class:`ProjectStoreProtocol` plus the
  CRUD half of :class:`JsonProjectStore`: project + task
  create/read/update/delete + ``close``.
* P9.2b -- tree / query half: ``all_tasks``,
  ``find_task_by_chain``, ``get_task``, ``get_subtasks``,
  ``get_task_tree``, ``get_ancestors``, ``recalc_progress``.
* P9.2c -- :class:`SqliteProjectStore` + factory.

ADR refs: ADR-0011 (Protocol-typed subsystem decomposition),
ADR-0012 (orgs/ deletion strategy -- no shim under v1).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from .project_models import OrgProject, ProjectTask, TaskStatus, now_iso

__all__ = ["JsonProjectStore", "ProjectStoreProtocol"]

logger = logging.getLogger(__name__)


@runtime_checkable
class ProjectStoreProtocol(Protocol):
    """Public surface of the v2 ProjectStore (ADR-0011).

    Mirrors v1 ``openakita.orgs.project_store.ProjectStore`` 1:1
    so P9.8 caller migration is one import-line change. All
    methods are synchronous; concurrency is bounded by an
    in-process ``threading.RLock`` per backend instance plus
    (for SQLite, P9.2c) WAL + ``BEGIN IMMEDIATE``.
    """

    # project CRUD
    def list_projects(self) -> list[OrgProject]: ...
    def get_project(self, project_id: str) -> OrgProject | None: ...
    def create_project(self, proj: OrgProject) -> OrgProject: ...
    def update_project(self, project_id: str, updates: dict) -> OrgProject | None: ...
    def delete_project(self, project_id: str) -> bool: ...

    # task CRUD
    def add_task(self, project_id: str, task: ProjectTask) -> ProjectTask | None: ...
    def update_task(self, project_id: str, task_id: str, updates: dict) -> ProjectTask | None: ...
    def delete_task(self, project_id: str, task_id: str) -> bool: ...

    # queries / tree (P9.2b -- declared here so the Protocol is the
    # v1 surface from day one; JsonProjectStore raises
    # NotImplementedError until P9.2b lands the implementations)
    def all_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
        chain_id: str | None = None,
        parent_task_id: str | None = None,
        root_only: bool = False,
        delegated_by: str | None = None,
        project_id: str | None = None,
    ) -> list[dict]: ...
    def find_task_by_chain(self, chain_id: str) -> ProjectTask | None: ...
    def get_task(self, task_id: str) -> tuple[ProjectTask | None, OrgProject | None]: ...
    def get_subtasks(self, parent_task_id: str) -> list[ProjectTask]: ...
    def get_task_tree(self, task_id: str) -> dict: ...
    def get_ancestors(self, task_id: str) -> list[ProjectTask]: ...
    def recalc_progress(self, task_id: str) -> int | None: ...

    # backend lifecycle
    def close(self) -> None: ...


class JsonProjectStore:
    """JSON-backed projects store; one ``projects.json`` per org dir.

    File format matches v1
    ``openakita.orgs.project_store.ProjectStore`` byte-for-byte
    (a flat JSON array of ``OrgProject.to_dict()`` payloads) so
    a v1-written file is readable by v2 and vice versa. This is
    what gates parity at P9.2d.

    Concurrency: a per-instance ``threading.RLock`` serialises
    every mutator. v1 only locked ``_save`` -- v2 takes the lock
    across the entire read-modify-write window for ``add_task`` /
    ``update_task`` / ``delete_task`` so concurrent writers
    cannot lose rows (same correctness upgrade as P9.1''s
    OrgBlackboard).
    """

    def __init__(self, org_dir: Path | str) -> None:
        self._path = Path(org_dir) / "projects.json"
        self._projects: dict[str, OrgProject] = {}
        self._mtime: float = 0.0
        self._lock = threading.RLock()
        self._closed = False
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _file_mtime(self) -> float:
        try:
            return self._path.stat().st_mtime
        except FileNotFoundError:
            return 0.0

    def _reload_if_changed(self) -> None:
        """Re-read from disk if the file mtime advanced.

        v1 mtime-watch semantics preserved so two
        ``JsonProjectStore`` instances pointed at the same file
        see each other''s writes (subject to OS mtime
        resolution).
        """
        if self._file_mtime() > self._mtime:
            self._load()

    def _load(self) -> None:
        with self._lock:
            if not self._path.exists():
                self._projects = {}
                self._mtime = 0.0
                return
            try:
                data = json.loads(self._path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("[ProjectStore] load failed for %s: %s", self._path, exc)
                return
            loaded: dict[str, OrgProject] = {}
            for raw in data if isinstance(data, list) else []:
                try:
                    proj = OrgProject.from_dict(raw)
                except (KeyError, ValueError, TypeError) as exc:
                    logger.warning("[ProjectStore] dropping malformed row: %s", exc)
                    continue
                loaded[proj.id] = proj
            self._projects = loaded
            self._mtime = self._file_mtime()

    def _save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = [p.to_dict() for p in self._projects.values()]
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self._path)
            self._mtime = self._file_mtime()

    # ------------------------------------------------------------------
    # Project CRUD
    # ------------------------------------------------------------------

    def list_projects(self) -> list[OrgProject]:
        with self._lock:
            self._reload_if_changed()
            return list(self._projects.values())

    def get_project(self, project_id: str) -> OrgProject | None:
        with self._lock:
            self._reload_if_changed()
            return self._projects.get(project_id)

    def create_project(self, proj: OrgProject) -> OrgProject:
        with self._lock:
            self._projects[proj.id] = proj
            self._save()
            return proj

    def update_project(self, project_id: str, updates: dict) -> OrgProject | None:
        with self._lock:
            proj = self._projects.get(project_id)
            if not proj:
                return None
            for key, val in updates.items():
                if key == "tasks":
                    continue  # task list mutated via add/update/delete_task
                if hasattr(proj, key):
                    setattr(proj, key, val)
            proj.updated_at = now_iso()
            self._save()
            return proj

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            if project_id not in self._projects:
                return False
            del self._projects[project_id]
            self._save()
            return True

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def add_task(self, project_id: str, task: ProjectTask) -> ProjectTask | None:
        with self._lock:
            proj = self._projects.get(project_id)
            if not proj:
                return None
            task.project_id = project_id
            proj.tasks.append(task)
            proj.updated_at = now_iso()
            self._save()
            return task

    def update_task(self, project_id: str, task_id: str, updates: dict) -> ProjectTask | None:
        with self._lock:
            proj = self._projects.get(project_id)
            if not proj:
                return None
            for t in proj.tasks:
                if t.id != task_id:
                    continue
                new_status_raw = updates.get("status")
                new_status: TaskStatus | None = None
                if isinstance(new_status_raw, TaskStatus):
                    new_status = new_status_raw
                elif isinstance(new_status_raw, str):
                    try:
                        new_status = TaskStatus(new_status_raw)
                    except ValueError:
                        new_status = None
                for key, val in updates.items():
                    if not hasattr(t, key):
                        continue
                    if key == "status" and isinstance(val, str):
                        try:
                            val = TaskStatus(val)
                        except ValueError:
                            continue
                    setattr(t, key, val)
                if new_status is not None:
                    now = now_iso()
                    if new_status == TaskStatus.IN_PROGRESS and not t.started_at:
                        t.started_at = now
                    elif new_status == TaskStatus.DELIVERED and not t.delivered_at:
                        t.delivered_at = now
                    elif new_status == TaskStatus.ACCEPTED and not t.completed_at:
                        t.completed_at = now
                proj.updated_at = now_iso()
                self._save()
                return t
            return None

    def delete_task(self, project_id: str, task_id: str) -> bool:
        with self._lock:
            proj = self._projects.get(project_id)
            if not proj:
                return False
            before = len(proj.tasks)
            proj.tasks = [t for t in proj.tasks if t.id != task_id]
            if len(proj.tasks) < before:
                proj.updated_at = now_iso()
                self._save()
                return True
            return False

    # ------------------------------------------------------------------
    # Queries / tree -- land in P9.2b
    # ------------------------------------------------------------------

    def all_tasks(self, **_kwargs: object) -> list[dict]:
        raise NotImplementedError("JsonProjectStore.all_tasks lands in P9.2b")

    def find_task_by_chain(self, chain_id: str) -> ProjectTask | None:
        raise NotImplementedError("JsonProjectStore.find_task_by_chain lands in P9.2b")

    def get_task(self, task_id: str) -> tuple[ProjectTask | None, OrgProject | None]:
        raise NotImplementedError("JsonProjectStore.get_task lands in P9.2b")

    def get_subtasks(self, parent_task_id: str) -> list[ProjectTask]:
        raise NotImplementedError("JsonProjectStore.get_subtasks lands in P9.2b")

    def get_task_tree(self, task_id: str) -> dict:
        raise NotImplementedError("JsonProjectStore.get_task_tree lands in P9.2b")

    def get_ancestors(self, task_id: str) -> list[ProjectTask]:
        raise NotImplementedError("JsonProjectStore.get_ancestors lands in P9.2b")

    def recalc_progress(self, task_id: str) -> int | None:
        raise NotImplementedError("JsonProjectStore.recalc_progress lands in P9.2b")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release backend resources. Idempotent.

        JSON backend has no open file handles to release; the
        ``close`` method exists to satisfy the
        :class:`ProjectStoreProtocol` lifecycle contract that
        the P9.2c :class:`SqliteProjectStore` will need.
        """
        with self._lock:
            self._closed = True
