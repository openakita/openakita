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

from openakita.memory.types import normalize_tags

from .models import (
    NodeSchedule,
    Organization,
    OrgNode,
    OrgStatus,
    _new_id,
    _now_iso,
    infer_agent_profile_id_for_node,
)

logger = logging.getLogger(__name__)


class OrgNameConflictError(ValueError):
    """组织名字与已有组织重复时抛出。

    Args:
        name: 用户提交的、已经被占用的名字（保留原始大小写）。
        conflict_org_id: 占用该名字的现有组织 id；REST 层据此返回 409 + 提示。
    """

    def __init__(self, name: str, conflict_org_id: str) -> None:
        super().__init__(f"Organization name already exists: {name!r}")
        self.name = name
        self.conflict_org_id = conflict_org_id


def _normalize_org_name(name: str | None) -> str:
    """将组织名归一化，便于做 case/空白不敏感的唯一性比对。

    去掉首尾空白并转小写。所有"已存在则视为重复"的判断都基于这个归一化形式，
    用户层面则保留原始大小写。
    """
    return (name or "").strip().casefold()


_LAYOUT_NODE_W = 240
_LAYOUT_NODE_H = 100
_LAYOUT_GAP_X = 40
_LAYOUT_GAP_Y = 80


def _apply_initial_tree_layout(data: dict) -> None:
    """Assign a readable first-open layout to template-created organizations.

    Built-in and user-saved templates may carry stale or overly compact
    coordinates. Creating from a template is a fresh org, so normalize the
    initial canvas once before persisting it.
    """
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    if not nodes:
        return

    node_ids = [str(n.get("id") or "") for n in nodes if isinstance(n, dict) and n.get("id")]
    node_id_set = set(node_ids)
    if not node_id_set:
        return

    children_map: dict[str, list[str]] = {}
    parent_set: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in node_id_set or target not in node_id_set or source == target:
            continue
        children_map.setdefault(source, []).append(target)
        parent_set.add(target)

    roots = [node_id for node_id in node_ids if node_id not in parent_set]
    if not roots:
        roots = node_ids[:1]

    levels: list[list[str]] = []
    visited: set[str] = set()
    queue = list(roots)
    while queue:
        level: list[str] = []
        next_queue: list[str] = []
        for node_id in queue:
            if node_id in visited:
                continue
            visited.add(node_id)
            level.append(node_id)
            for child_id in children_map.get(node_id, []):
                if child_id not in visited:
                    next_queue.append(child_id)
        if level:
            levels.append(level)
        queue = next_queue

    orphaned = [node_id for node_id in node_ids if node_id not in visited]
    if orphaned:
        if levels:
            levels[-1].extend(orphaned)
        else:
            levels.append(orphaned)

    max_level_width = max(len(level) for level in levels)
    total_w = max_level_width * (_LAYOUT_NODE_W + _LAYOUT_GAP_X) - _LAYOUT_GAP_X
    pos_map: dict[str, dict[str, int]] = {}
    for level_index, level in enumerate(levels):
        level_w = len(level) * (_LAYOUT_NODE_W + _LAYOUT_GAP_X) - _LAYOUT_GAP_X
        offset_x = (total_w - level_w) // 2
        for node_index, node_id in enumerate(level):
            pos_map[node_id] = {
                "x": offset_x + node_index * (_LAYOUT_NODE_W + _LAYOUT_GAP_X),
                "y": level_index * (_LAYOUT_NODE_H + _LAYOUT_GAP_Y),
            }

    for node in nodes:
        if isinstance(node, dict) and node.get("id") in pos_map:
            node["position"] = pos_map[str(node["id"])]


class OrgManager:
    """组织持久化管理器"""

    def __init__(self, data_dir: Path) -> None:
        self._orgs_dir = data_dir / "orgs"
        self._templates_dir = data_dir / "org_templates"
        self._orgs_dir.mkdir(parents=True, exist_ok=True)
        self._templates_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Organization] = {}
        import threading
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def _org_dir(self, org_id: str) -> Path:
        if ".." in org_id or "/" in org_id or "\\" in org_id:
            raise ValueError(f"Invalid org_id: {org_id}")
        return self._orgs_dir / org_id

    def get_org_dir(self, org_id: str) -> Path:
        """公开版的 :pyfunc:`_org_dir`：返回组织在磁盘上的根目录。

        推荐的访问路径，外部调用方（command_service / api 路由 / 插件）应该
        通过这里而不是直接戳带前导下划线的私有方法。两者完全等价、无副作用。
        """
        return self._org_dir(org_id)

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

    def find_by_name(
        self,
        name: str,
        *,
        exclude_org_id: str | None = None,
        include_archived: bool = True,
    ) -> list[dict]:
        """按"归一化名字"查询匹配的组织（去首尾空白、大小写不敏感）。

        返回列表里每个元素和 :pyfunc:`list_orgs` 一致的摘要 dict（含 id/name/...）。
        ``exclude_org_id`` 用于"改名时不要把自己算成重名"。

        归档（ARCHIVED）默认也参与查重——归档后改名能复活老组织名容易让人困惑；
        如果调用方明确要忽略归档，可显式传 ``include_archived=False``。
        """
        norm = _normalize_org_name(name)
        if not norm:
            return []
        result: list[dict] = []
        for item in self.list_orgs(include_archived=include_archived):
            if exclude_org_id and item.get("id") == exclude_org_id:
                continue
            if _normalize_org_name(item.get("name", "")) == norm:
                result.append(item)
        return result

    def resolve_id_by_name_or_id(self, query: str) -> tuple[str | None, list[dict]]:
        """聊天/IM 端用：给一段用户输入，优先按 id 命中，否则按名字查找。

        返回 ``(org_id, candidates)``：

        - 用户输入恰好是已有 org 的 id（精确匹配）：``(id, [])``。
        - 名字精确匹配且只有一个：``(id, [])``。
        - 名字匹配多个：``(None, [候选 dict, ...])`` —— 由调用方让用户消歧。
        - 都不匹配：``(None, [])``。

        归一化规则与 :pyfunc:`find_by_name` 一致：去首尾空白、大小写不敏感。
        """
        q = (query or "").strip()
        if not q:
            return None, []
        if self.get(q) is not None:
            return q, []
        matches = self.find_by_name(q)
        if len(matches) == 1:
            return str(matches[0].get("id") or ""), []
        if len(matches) > 1:
            return None, matches
        return None, []

    def _ensure_name_unique(self, name: str, *, exclude_org_id: str | None = None) -> None:
        """重名校验入口；冲突直接抛 :class:`OrgNameConflictError`。

        在 :pyfunc:`create`、:pyfunc:`update`、:pyfunc:`duplicate` 三处统一调用，
        从根本上保证"用户在任何路径下都不可能把两个组织起成同一个名字"。
        """
        clean = (name or "").strip()
        if not clean:
            raise ValueError("Organization name is required")
        conflicts = self.find_by_name(clean, exclude_org_id=exclude_org_id)
        if conflicts:
            raise OrgNameConflictError(clean, str(conflicts[0].get("id") or ""))

    def create(self, data: dict) -> Organization:
        """Create a new organization from dict payload.

        Raises:
            OrgNameConflictError: 名字与已有组织重复（大小写/空白不敏感）。
        """
        self._ensure_name_unique(data.get("name", ""))
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
        """Update an existing organization. Merges provided fields.

        Raises:
            OrgNameConflictError: 改名时与其他组织重名。
        """
        org = self._load(org_id)
        nodes_raw = data.pop("nodes", None)
        edges_raw = data.pop("edges", None)

        if "name" in data:
            new_name = data.get("name")
            if isinstance(new_name, str) and _normalize_org_name(new_name) != _normalize_org_name(
                org.name
            ):
                self._ensure_name_unique(new_name, exclude_org_id=org_id)

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
            _RUNTIME_KEYS = {"status", "_runtime", "current_task"}
            _CONFIG_FIELDS = set(OrgNode.__dataclass_fields__) - _RUNTIME_KEYS
            existing = {n.id: n for n in org.nodes}
            updated: list[OrgNode] = []
            for nd in nodes_raw:
                node_id = nd.get("id")
                old = existing.get(node_id) if node_id else None
                if old is not None:
                    for key in _CONFIG_FIELDS:
                        if key in nd:
                            setattr(old, key, nd[key])
                    if not old.agent_profile_id:
                        old.agent_profile_id = infer_agent_profile_id_for_node(old.to_dict())
                    updated.append(old)
                else:
                    clean = {k: v for k, v in nd.items() if k not in _RUNTIME_KEYS}
                    updated.append(OrgNode.from_dict(clean))
            org.nodes = updated
        if edges_raw is not None:
            from .models import OrgEdge
            org.edges = [
                OrgEdge.from_dict(e) for e in edges_raw
                if e.get("source") != e.get("target")
            ]

        # 工作台节点（plugin_origin 非空）必须是叶子节点。把这一规则放在
        # 边/节点合并完成之后做最终校验，避免任意路径(直接 PATCH 节点 / 编辑
        # 边后保存)绕过限制。命中时抛 ValueError，由 API 层映射成 422。
        _violations: list[str] = []
        for n in org.nodes:
            if not getattr(n, "plugin_origin", None):
                continue
            if org.get_children(n.id):
                title = (n.role_title or n.id).strip()
                _violations.append(f"{title}({n.id})")
        if _violations:
            raise ValueError(
                "工作台节点必须是叶子节点，不允许挂下属节点："
                + "、".join(_violations)
                + "。请删除其下属节点或移除工作台标识后再保存。"
            )

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
        """Deep-copy an organization.

        如果 ``new_name`` 未指定，按 "<原名> (副本)" → "<原名> (副本 2)" 顺序
        自动寻找一个未被占用的名字，避免连续复制时撞名。
        显式传入的 ``new_name`` 不会再加后缀；若它已被占用，
        :pyfunc:`create` 会抛 :class:`OrgNameConflictError`。
        """
        src = self._load(org_id)
        data = src.to_dict()
        data["id"] = _new_id("org_")
        if new_name:
            data["name"] = new_name
        else:
            base = f"{src.name} (副本)"
            candidate = base
            n = 2
            while self.find_by_name(candidate):
                candidate = f"{base} {n}"
                n += 1
            data["name"] = candidate
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
        for old_n, new_n in zip(src.to_dict()["nodes"], data["nodes"], strict=False):
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
                    "tags": normalize_tags(data.get("tags")),
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
        """从模板创建组织。

        名字冲突策略：

        - **用户显式覆盖了 ``overrides["name"]``** → 严格使用该名字；撞名时由
          :pyfunc:`create` 抛 :class:`OrgNameConflictError`，路由层返回 409，
          让用户明确改个名字。
        - **未覆盖、沿用模板自带名** → 若已被占用，自动加 ``(2)/(3)`` 后缀，
          这样反复实例化同一个模板才不会被全局唯一约束卡住。
        """
        tpl = self.get_template(template_id)
        if tpl is None:
            raise FileNotFoundError(f"Template not found: {template_id}")
        tpl.pop("is_template", None)
        tpl["id"] = _new_id("org_")
        tpl["status"] = OrgStatus.DORMANT.value
        name_explicitly_overridden = bool(overrides and isinstance(overrides.get("name"), str))
        if overrides:
            tpl.update(overrides)
        if not name_explicitly_overridden:
            base_name = (tpl.get("name") or "").strip()
            if base_name and self.find_by_name(base_name):
                candidate = base_name
                n = 2
                while self.find_by_name(candidate):
                    candidate = f"{base_name} ({n})"
                    n += 1
                tpl["name"] = candidate
        for node in tpl.get("nodes", []) or []:
            if isinstance(node, dict) and not node.get("agent_profile_id"):
                node["agent_profile_id"] = infer_agent_profile_id_for_node(node)
        _apply_initial_tree_layout(tpl)
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
        tmp = p.with_suffix(".tmp")
        payload = json.dumps(org.to_dict(), ensure_ascii=False, indent=2)
        import os
        with self._write_lock:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(str(tmp), str(p))
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
