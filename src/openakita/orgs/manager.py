"""
OrgManager — 组织 CRUD、持久化、模板管理

负责组织的创建/读取/更新/删除，以及持久化目录结构初始化。
不涉及运行时逻辑（由 OrgRuntime 负责）。
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from .models import (
    NodeSchedule,
    Organization,
    OrgNode,
    OrgStatus,
    _new_id,
    _now_iso,
)

logger = logging.getLogger(__name__)


class OrgManager:
    """组织持久化管理器"""

    def __init__(self, data_dir: Path) -> None:
        self._orgs_dir = data_dir / "orgs"
        self._templates_dir = data_dir / "org_templates"
        self._orgs_dir.mkdir(parents=True, exist_ok=True)
        self._templates_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Organization] = {}

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _org_dir(self, org_id: str) -> Path:
        if ".." in org_id or "/" in org_id or "\\" in org_id:
            raise ValueError(f"Invalid org_id: {org_id}")
        return self._orgs_dir / org_id

    def _org_json(self, org_id: str) -> Path:
        return self._org_dir(org_id) / "org.json"

    def _state_json(self, org_id: str) -> Path:
        return self._org_dir(org_id) / "state.json"

    def _node_dir(self, org_id: str, node_id: str) -> Path:
        return self._org_dir(org_id) / "nodes" / node_id

    def _schedules_json(self, org_id: str, node_id: str) -> Path:
        return self._node_dir(org_id, node_id) / "schedules.json"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_orgs(self, include_archived: bool = False) -> list[dict]:
        """Return summary list of all organizations."""
        result: list[dict] = []
        if not self._orgs_dir.exists():
            return result
        for p in sorted(self._orgs_dir.iterdir()):
            org_json = p / "org.json"
            if not org_json.is_file():
                continue
            try:
                org = self._load(p.name)
                if not include_archived and org.status == OrgStatus.ARCHIVED:
                    continue
                result.append({
                    "id": org.id,
                    "name": org.name,
                    "description": org.description,
                    "icon": org.icon,
                    "status": org.status.value,
                    "node_count": len(org.nodes),
                    "edge_count": len(org.edges),
                    "tags": org.tags,
                    "created_at": org.created_at,
                    "updated_at": org.updated_at,
                })
            except Exception as exc:
                logger.warning(f"Failed to load org {p.name}: {exc}")
        return result

    def get(self, org_id: str) -> Organization | None:
        try:
            return self._load(org_id)
        except FileNotFoundError:
            return None

    def create(self, data: dict) -> Organization:
        """Create a new organization from dict payload."""
        org = Organization.from_dict(data)
        if not org.id:
            org.id = _new_id("org_")
        org.created_at = _now_iso()
        org.updated_at = org.created_at
        self._init_dirs(org)
        self._save(org)
        logger.info(f"[OrgManager] Created org: {org.id} ({org.name})")
        return org

    def update(self, org_id: str, data: dict) -> Organization:
        """Update an existing organization. Merges provided fields."""
        org = self._load(org_id)
        nodes_raw = data.pop("nodes", None)
        edges_raw = data.pop("edges", None)

        for key, val in data.items():
            if key in ("id", "created_at"):
                continue
            if hasattr(org, key):
                if key == "status" and isinstance(val, str):
                    val = OrgStatus(val)
                elif key == "user_persona" and isinstance(val, dict):
                    from .models import UserPersona
                    val = UserPersona.from_dict(val)
                setattr(org, key, val)

        if nodes_raw is not None:
            org.nodes = [OrgNode.from_dict(n) for n in nodes_raw]
        if edges_raw is not None:
            from .models import OrgEdge
            org.edges = [OrgEdge.from_dict(e) for e in edges_raw]

        org.updated_at = _now_iso()
        self._ensure_node_dirs(org)
        self._save(org)
        logger.info(f"[OrgManager] Updated org: {org.id}")
        return org

    def save_direct(self, org: Organization) -> bool:
        """Write an Organization directly to disk without load-merge.

        Returns True on success, False if the org directory no longer exists
        (i.e. org was already deleted).  Unlike update(), this never triggers
        a disk reload and will NOT re-create a deleted org directory.
        """
        d = self._org_dir(org.id)
        if not d.exists():
            self._cache.pop(org.id, None)
            return False
        self._save(org)
        return True

    def delete(self, org_id: str) -> bool:
        """Permanently delete an organization and all its data."""
        d = self._org_dir(org_id)
        if not d.exists():
            return False
        shutil.rmtree(d, ignore_errors=True)
        self._cache.pop(org_id, None)
        logger.info(f"[OrgManager] Deleted org: {org_id}")
        return True

    def archive(self, org_id: str) -> Organization:
        return self.update(org_id, {"status": "archived"})

    def unarchive(self, org_id: str) -> Organization:
        return self.update(org_id, {"status": "active"})

    def duplicate(self, org_id: str, new_name: str | None = None) -> Organization:
        """Deep-copy an organization."""
        src = self._load(org_id)
        data = src.to_dict()
        data["id"] = _new_id("org_")
        data["name"] = new_name or f"{src.name} (副本)"
        data["status"] = OrgStatus.DORMANT.value
        data["created_at"] = _now_iso()
        data["updated_at"] = data["created_at"]
        data["total_tasks_completed"] = 0
        data["total_messages_exchanged"] = 0
        data["total_tokens_used"] = 0

        for node in data.get("nodes", []):
            node["id"] = _new_id("node_")
            node["status"] = "idle"
            node["frozen_by"] = None
            node["frozen_reason"] = None
            node["frozen_at"] = None

        id_map: dict[str, str] = {}
        for old_n, new_n in zip(src.to_dict()["nodes"], data["nodes"]):
            id_map[old_n["id"]] = new_n["id"]

        for edge in data.get("edges", []):
            edge["id"] = _new_id("edge_")
            edge["source"] = id_map.get(edge["source"], edge["source"])
            edge["target"] = id_map.get(edge["target"], edge["target"])

        return self.create(data)

    # ------------------------------------------------------------------
    # Node schedules (stored independently)
    # ------------------------------------------------------------------

    def get_node_schedules(self, org_id: str, node_id: str) -> list[NodeSchedule]:
        p = self._schedules_json(org_id, node_id)
        if not p.is_file():
            return []
        raw = json.loads(p.read_text(encoding="utf-8"))
        return [NodeSchedule.from_dict(s) for s in raw]

    def save_node_schedules(
        self, org_id: str, node_id: str, schedules: list[NodeSchedule]
    ) -> None:
        p = self._schedules_json(org_id, node_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps([s.to_dict() for s in schedules], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_node_schedule(
        self, org_id: str, node_id: str, schedule: NodeSchedule
    ) -> NodeSchedule:
        schedules = self.get_node_schedules(org_id, node_id)
        schedules.append(schedule)
        self.save_node_schedules(org_id, node_id, schedules)
        return schedule

    def update_node_schedule(
        self, org_id: str, node_id: str, schedule_id: str, data: dict
    ) -> NodeSchedule | None:
        schedules = self.get_node_schedules(org_id, node_id)
        for i, s in enumerate(schedules):
            if s.id == schedule_id:
                for k, v in data.items():
                    if hasattr(s, k) and k != "id":
                        if k == "schedule_type" and isinstance(v, str):
                            from .models import ScheduleType
                            v = ScheduleType(v)
                        setattr(s, k, v)
                schedules[i] = s
                self.save_node_schedules(org_id, node_id, schedules)
                return s
        return None

    def delete_node_schedule(
        self, org_id: str, node_id: str, schedule_id: str
    ) -> bool:
        schedules = self.get_node_schedules(org_id, node_id)
        before = len(schedules)
        schedules = [s for s in schedules if s.id != schedule_id]
        if len(schedules) == before:
            return False
        self.save_node_schedules(org_id, node_id, schedules)
        return True

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def list_templates(self) -> list[dict]:
        result: list[dict] = []
        for p in sorted(self._templates_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                result.append({
                    "id": p.stem,
                    "name": data.get("name", p.stem),
                    "description": data.get("description", ""),
                    "icon": data.get("icon", "🏢"),
                    "node_count": len(data.get("nodes", [])),
                    "tags": data.get("tags", []),
                })
            except Exception as exc:
                logger.warning(f"Failed to load template {p.name}: {exc}")
        return result

    def get_template(self, template_id: str) -> dict | None:
        p = self._templates_dir / f"{template_id}.json"
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def create_from_template(
        self, template_id: str, overrides: dict | None = None
    ) -> Organization:
        tpl = self.get_template(template_id)
        if tpl is None:
            raise FileNotFoundError(f"Template not found: {template_id}")
        tpl.pop("is_template", None)
        tpl["id"] = _new_id("org_")
        tpl["status"] = OrgStatus.DORMANT.value
        if overrides:
            tpl.update(overrides)
        return self.create(tpl)

    def save_as_template(self, org_id: str, template_id: str | None = None) -> str:
        org = self._load(org_id)
        data = org.to_dict()
        data["is_template"] = True
        data.pop("id", None)
        data["status"] = OrgStatus.DORMANT.value
        data["total_tasks_completed"] = 0
        data["total_messages_exchanged"] = 0
        data["total_tokens_used"] = 0
        tid = template_id or org.name.lower().replace(" ", "-")
        p = self._templates_dir / f"{tid}.json"
        p.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"[OrgManager] Saved template: {tid}")
        return tid

    # ------------------------------------------------------------------
    # Runtime state (read/write by OrgRuntime)
    # ------------------------------------------------------------------

    def load_state(self, org_id: str) -> dict:
        p = self._state_json(org_id)
        if not p.is_file():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def save_state(self, org_id: str, state: dict) -> None:
        p = self._state_json(org_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self, org_id: str) -> Organization:
        if org_id in self._cache:
            return self._cache[org_id]
        p = self._org_json(org_id)
        if not p.is_file():
            raise FileNotFoundError(f"Organization not found: {org_id}")
        data = json.loads(p.read_text(encoding="utf-8"))
        org = Organization.from_dict(data)
        self._cache[org_id] = org
        return org

    def _save(self, org: Organization) -> None:
        p = self._org_json(org.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(org.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._cache[org.id] = org

    def _init_dirs(self, org: Organization) -> None:
        """Create the full directory tree for a new organization."""
        base = self._org_dir(org.id)
        for sub in [
            "nodes",
            "policies",
            "departments",
            "memory",
            "memory/departments",
            "memory/nodes",
            "events",
            "logs",
            "logs/tasks",
            "reports",
            "artifacts",
            "artifacts/meetings",
        ]:
            (base / sub).mkdir(parents=True, exist_ok=True)

        self._ensure_node_dirs(org)

        readme = base / "policies" / "README.md"
        if not readme.exists():
            readme.write_text(
                "# 制度索引\n\n> 此文件由系统自动维护。\n\n"
                "| 文件 | 标题 | 适用范围 | 最后更新 |\n"
                "|------|------|---------|--------|\n",
                encoding="utf-8",
            )

    def _ensure_node_dirs(self, org: Organization) -> None:
        for node in org.nodes:
            nd = self._node_dir(org.id, node.id)
            (nd / "identity").mkdir(parents=True, exist_ok=True)

            mcp_cfg = nd / "mcp_config.json"
            if not mcp_cfg.exists():
                mcp_cfg.write_text(
                    json.dumps({"mode": "inherit"}, indent=2),
                    encoding="utf-8",
                )

            sched = nd / "schedules.json"
            if not sched.exists():
                sched.write_text("[]", encoding="utf-8")

        for dept in org.get_departments():
            (self._org_dir(org.id) / "departments" / dept).mkdir(
                parents=True, exist_ok=True
            )

    def invalidate_cache(self, org_id: str | None = None) -> None:
        if org_id:
            self._cache.pop(org_id, None)
        else:
            self._cache.clear()
