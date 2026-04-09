"""
Project store — persistent JSON-file storage for OrgProject / ProjectTask.

Each organisation has its own ``projects.json`` under ``data/orgs/<org_id>/``.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from openakita.orgs.models import OrgProject, ProjectTask, _now_iso

logger = logging.getLogger(__name__)


class ProjectStore:
    """Simple JSON-backed project store, one file per org."""

    def __init__(self, org_dir: Path) -> None:
        self._path = org_dir / "projects.json"
        self._projects: dict[str, OrgProject] = {}
        self._mtime: float = 0.0
        self._lock = threading.Lock()
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _file_mtime(self) -> float:
        try:
            return self._path.stat().st_mtime
        except FileNotFoundError:
            return 0.0

    def _reload_if_changed(self) -> None:
        """Re-read from disk if another process/instance modified the file."""
        mt = self._file_mtime()
        if mt > self._mtime:
            self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text("utf-8"))
            self._projects = {}
            for raw in data:
                proj = OrgProject.from_dict(raw)
                self._projects[proj.id] = proj
            self._mtime = self._file_mtime()
        except Exception as exc:
            logger.warning("Failed to load projects from %s: %s", self._path, exc)

    def _save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = [p.to_dict() for p in self._projects.values()]
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Project CRUD
    # ------------------------------------------------------------------

    def list_projects(self) -> list[OrgProject]:
        self._reload_if_changed()
        return list(self._projects.values())

    def get_project(self, project_id: str) -> OrgProject | None:
        self._reload_if_changed()
        return self._projects.get(project_id)

    def create_project(self, proj: OrgProject) -> OrgProject:
        self._projects[proj.id] = proj
        self._save()
        return proj

    def update_project(self, project_id: str, updates: dict) -> OrgProject | None:
        proj = self._projects.get(project_id)
        if not proj:
            return None
        for key, val in updates.items():
            if key == "tasks":
                continue
            if hasattr(proj, key):
                setattr(proj, key, val)
        proj.updated_at = _now_iso()
        self._save()
        return proj

    def delete_project(self, project_id: str) -> bool:
        if project_id not in self._projects:
            return False
        del self._projects[project_id]
        self._save()
        return True

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def add_task(self, project_id: str, task: ProjectTask) -> ProjectTask | None:
        proj = self._projects.get(project_id)
        if not proj:
            return None
        task.project_id = project_id
        proj.tasks.append(task)
        proj.updated_at = _now_iso()
        self._save()
        return task

    def update_task(self, project_id: str, task_id: str, updates: dict) -> ProjectTask | None:
        from openakita.orgs.models import TaskStatus
        proj = self._projects.get(project_id)
        if not proj:
            return None
        for t in proj.tasks:
            if t.id == task_id:
                new_status = updates.get("status")
                if isinstance(new_status, str):
                    new_status = TaskStatus(new_status)
                for key, val in updates.items():
                    if hasattr(t, key):
                        if key == "status" and isinstance(val, str):
                            val = TaskStatus(val)
                        setattr(t, key, val)
                if new_status is not None:
                    now = _now_iso()
                    if new_status == TaskStatus.IN_PROGRESS and not t.started_at:
                        t.started_at = now
                    elif new_status == TaskStatus.DELIVERED and not t.delivered_at:
                        t.delivered_at = now
                    elif new_status in (TaskStatus.ACCEPTED,) and not t.completed_at:
                        t.completed_at = now
                proj.updated_at = _now_iso()
                self._save()
                return t
        return None

    def delete_task(self, project_id: str, task_id: str) -> bool:
        proj = self._projects.get(project_id)
        if not proj:
            return False
        before = len(proj.tasks)
        proj.tasks = [t for t in proj.tasks if t.id != task_id]
        if len(proj.tasks) < before:
            proj.updated_at = _now_iso()
            self._save()
            return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def all_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
        chain_id: str | None = None,
        parent_task_id: str | None = None,
        root_only: bool = False,
        delegated_by: str | None = None,
        project_id: str | None = None,
    ) -> list[dict]:
        """Flat list of tasks across all projects, with optional filters."""
        self._reload_if_changed()
        result: list[dict] = []
        for proj in self._projects.values():
            if project_id and proj.id != project_id:
                continue
            for t in proj.tasks:
                if status and t.status.value != status:
                    continue
                if assignee and t.assignee_node_id != assignee:
                    continue
                if chain_id and t.chain_id != chain_id:
                    continue
                if parent_task_id is not None and t.parent_task_id != parent_task_id:
                    continue
                if root_only and t.parent_task_id is not None:
                    continue
                if delegated_by is not None and t.delegated_by != delegated_by:
                    continue
                d = t.to_dict()
                d["project_name"] = proj.name
                d["project_type"] = proj.project_type.value
                result.append(d)
        return result

    def find_task_by_chain(self, chain_id: str) -> ProjectTask | None:
        """Find a task by its task_chain_id across all projects."""
        self._reload_if_changed()
        for proj in self._projects.values():
            for t in proj.tasks:
                if t.chain_id == chain_id:
                    return t
        return None

    def get_task(self, task_id: str) -> tuple[ProjectTask | None, OrgProject | None]:
        """Get a task by id across all projects. Returns (task, project) or (None, None)."""
        self._reload_if_changed()
        for proj in self._projects.values():
            for t in proj.tasks:
                if t.id == task_id:
                    return t, proj
        return None, None

    def get_subtasks(self, parent_task_id: str) -> list[ProjectTask]:
        """Get direct children of a task across all projects."""
        self._reload_if_changed()
        result: list[ProjectTask] = []
        for proj in self._projects.values():
            for t in proj.tasks:
                if t.parent_task_id == parent_task_id:
                    result.append(t)
        return result

    def get_task_tree(self, task_id: str) -> dict:
        """Get a task and all its descendants recursively. Returns a tree structure."""
        self._reload_if_changed()
        task, proj = self.get_task(task_id)
        if not task:
            return {}
        node: dict = task.to_dict()
        node["project_name"] = proj.name if proj else ""
        node["children"] = []
        for child in self.get_subtasks(task_id):
            node["children"].append(self.get_task_tree(child.id))
        return node

    def get_ancestors(self, task_id: str) -> list[ProjectTask]:
        """Get all ancestors of a task (parent, grandparent, ...) from nearest to root."""
        self._reload_if_changed()
        result: list[ProjectTask] = []
        task, _ = self.get_task(task_id)
        while task and task.parent_task_id:
            parent, _ = self.get_task(task.parent_task_id)
            if not parent:
                break
            result.append(parent)
            task = parent
        return result

    def recalc_progress(self, task_id: str) -> int | None:
        """Recalculate progress_pct from children. Returns new value or None if task not found."""
        self._reload_if_changed()
        task, proj = self.get_task(task_id)
        if not task or not proj:
            return None
        children = self.get_subtasks(task_id)
        if not children:
            return task.progress_pct
        total = sum(c.progress_pct for c in children)
        new_pct = total // len(children)
        self._update_task_field(task_id, "progress_pct", new_pct)
        return new_pct

    def _update_task_field(self, task_id: str, field: str, value: object) -> bool:
        """Update a single field on a task. Returns True if updated."""
        from openakita.orgs.models import TaskStatus

        self._reload_if_changed()
        for proj in self._projects.values():
            for t in proj.tasks:
                if t.id == task_id:
                    if hasattr(t, field):
                        if field == "status" and isinstance(value, str):
                            value = TaskStatus(value)
                        setattr(t, field, value)
                    proj.updated_at = _now_iso()
                    self._save()
                    return True
        return False
