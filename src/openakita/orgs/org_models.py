"""v2 ``org_models`` -- org-graph data shard (P-RC-9 P9.9γ-2b).

Absorbs 8 symbols deferred at P9.9γ-2 (charter §3 + P9.9γ-2 ledger row
"absorption-debt finding") plus 2 hidden enum dependencies pulled in by
the absorbed dataclasses. Sibling of ``command_models`` / ``memory_models``
/ ``project_models`` / ``scheduler_models``; completes the v2 model-shard
factoring so v1 ``openakita.orgs.models`` can ε-1-delete cleanly.

Absorbed (11 symbols total):

* Enums: :class:`OrgStatus` (8 inventoried), :class:`NodeStatus`
  (hidden -- ``OrgNode.status`` field type), :class:`EdgeType`
  (hidden -- ``OrgEdge.edge_type`` field type + ``Organization``
  hierarchy traversal sentinel).
* Helpers: :func:`now_iso` (renamed from v1 ``_now_iso`` per existing
  v2 shard convention -- see ``memory_models.now_iso`` /
  ``scheduler_models.now_iso`` / ``project_models.now_iso``);
  :func:`new_org_id` (renamed from v1 ``_new_id`` per existing v2
  shard convention -- see ``command_models.new_command_id`` /
  ``scheduler_models.new_schedule_id`` / ``project_models.new_project_id``;
  the polymorphic ``prefix`` argument is preserved so the same factory
  serves ``org_`` / ``node_`` / ``edge_`` id namespaces); v1
  ``_now_iso`` / ``_new_id`` aliases are re-exported below for
  byte-equal v2 manager.py internal use.
* :func:`infer_agent_profile_id_for_node` -- string-matching helper
  used by ``OrgNode.from_dict`` and v2 ``manager.py`` /
  ``_runtime_templates._auto_assign_agent_profiles`` to fill missing
  ``agent_profile_id`` from role-title keywords.
* Dataclasses: :class:`OrgNode` (~115 LOC; 38 fields + ``to_dict`` /
  ``from_dict``), :class:`OrgEdge` (~32 LOC; 8 fields + dict round-trip),
  :class:`UserPersona` (~28 LOC; 3 fields + ``label`` property +
  dict round-trip), :class:`Organization` (~329 LOC; 47 fields +
  ``to_dict`` / ``from_dict`` + ``get_node`` / ``resolve_reference``
  / ``get_root_nodes`` / ``get_children`` / ``get_parent`` /
  ``get_departments``).

Byte-equal port: v1 field names, defaults, dict-key spellings, and
method bodies are preserved verbatim; only two helper-name renames
(``_new_id`` -> ``new_org_id`` and ``_now_iso`` -> ``now_iso``) are
applied with underscore aliases re-exported to keep the v2
``runtime/orgs/manager.py`` caller unchanged at its internal use sites.

ADR refs: ADR-0011 (Protocol-typed subsystem decomposition; org-graph
shard sibling to the other 4); ADR-0012 (no shim under v1; this shard
is the v2-native home for the absorbed surface, not a re-export layer).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from openakita.memory.types import normalize_tags

__all__ = [
    "EdgeType",
    "NodeStatus",
    "OrgEdge",
    "OrgNode",
    "OrgStatus",
    "Organization",
    "UserPersona",
    "infer_agent_profile_id_for_node",
    "new_org_id",
    "now_iso",
    "_new_id",
    "_now_iso",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OrgStatus(StrEnum):
    DORMANT = "dormant"
    ACTIVE = "active"
    RUNNING = "running"
    PAUSED = "paused"
    ARCHIVED = "archived"


class NodeStatus(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    WAITING = "waiting"
    ERROR = "error"
    OFFLINE = "offline"
    FROZEN = "frozen"


class EdgeType(StrEnum):
    HIERARCHY = "hierarchy"
    COLLABORATE = "collaborate"
    ESCALATE = "escalate"
    CONSULT = "consult"
    ARTIFACT = "artifact"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_org_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}{short}" if prefix else short


# v1 ``_now_iso`` / ``_new_id`` aliases preserved verbatim so the v2
# ``runtime/orgs/manager.py`` caller can swap its v1 import to this
# shard with zero internal-use-site churn (manager.py uses both names
# inside method bodies; renaming there is out of scope for P9.9 γ-2b).
_now_iso = now_iso
_new_id = new_org_id


def infer_agent_profile_id_for_node(data: dict) -> str:
    """Infer a stable built-in AgentProfile id for legacy org nodes/templates."""
    node_id = str(data.get("id") or "").lower()
    title = str(data.get("role_title") or "").lower()
    dept = str(data.get("department") or "").lower()
    haystack = f"{node_id} {title} {dept}"

    if any(k in haystack for k in ("architect", "架构", "cto", "技术总监")):
        return "architect"
    if any(k in haystack for k in ("devops", "运维", "部署", "ci/cd")):
        return "devops-engineer"
    if any(
        k in haystack
        for k in ("dev", "engineer", "工程师", "开发", "全栈", "前端", "后端", "qa", "测试")
    ):
        return "code-assistant"
    if any(k in haystack for k in ("pm", "project", "项目", "产品", "cpo")):
        return "project-manager"
    if any(k in haystack for k in ("marketing", "market", "cmo", "seo", "社媒", "市场", "营销")):
        return "marketing-planner"
    if any(k in haystack for k in ("content", "writer", "文案", "内容", "运营", "编辑")):
        return "content-creator"
    if any(k in haystack for k in ("hr", "人力")):
        return "hr-assistant"
    if any(k in haystack for k in ("legal", "法务", "合规")):
        return "legal-advisor"
    if any(k in haystack for k in ("data", "分析", "数据")):
        return "data-analyst"
    if any(k in haystack for k in ("support", "客服", "客户")):
        return "customer-support"
    return "default"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class OrgNode:
    id: str = field(default_factory=lambda: new_org_id("node_"))
    role_title: str = ""
    role_goal: str = ""
    role_backstory: str = ""
    agent_source: str = "local"
    agent_profile_id: str | None = None
    position: dict = field(default_factory=lambda: {"x": 0.0, "y": 0.0})
    level: int = 0
    department: str = ""
    custom_prompt: str = ""
    identity_dir: str | None = None
    mcp_servers: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    skills_mode: str = "all"
    preferred_endpoint: str | None = None
    endpoint_policy: str = "prefer"
    max_concurrent_tasks: int = 1
    timeout_s: int = 0
    can_delegate: bool = True
    can_escalate: bool = True
    can_request_scaling: bool = True
    auto_clone_enabled: bool = False
    auto_clone_threshold: int = 3
    auto_clone_max: int = 3
    is_clone: bool = False
    clone_source: str | None = None
    ephemeral: bool = False
    avatar: str | None = None
    external_tools: list[str] = field(default_factory=list)
    # 节点是否拥有"基础文件工具"（write_file / read_file / edit_file /
    # list_directory）。默认 True，让没有显式勾选 filesystem 类目的角色（CPO、
    # 文案、运营等）也能在需要时把交付物落盘成附件，而不是只回一段长文。
    # 注意：刻意排除 run_shell / delete_file / grep / glob —— 那些归 filesystem
    # 类目自管，需要的话用户再去勾 external_tools=["filesystem"]。文件路径会被
    # agent.file_tool.base_path = <org_workspace> 隔离在组织 workspace 内，沿用
    # 现有沙盒，不引入新的逃逸面。
    enable_file_tools: bool = True
    # 工作台节点来源：当节点由"工作台模板"创建时填入，结构为
    # {"plugin_id": str, "template_id": str, "version": str}。
    # 该字段仅用于 UI 识别与提示词点睛，不参与运行时工具放行决策
    # （工具放行仍由 external_tools 决定）。约束：工作台节点必须是叶子节点，
    # 详见 OrgManager.update / OrgRuntime._create_node_agent。
    plugin_origin: dict | None = None
    frozen_by: str | None = None
    frozen_reason: str | None = None
    frozen_at: str | None = None
    status: NodeStatus = NodeStatus.IDLE
    # Per-node runtime overrides. Default empty dict means "no override —
    # use the org-level / global defaults". Recognised keys:
    #   - max_iterations: int — caps ReAct loops for this node
    #   - max_task_seconds: int — wall-clock timeout per delegated task
    #   - allowed_tools: list[str] — intersect with the node's tool grant
    #   - denied_tools: list[str] — subtract from the effective tool set
    # All keys are opt-in; unknown keys are ignored. Other organizations
    # leaving the dict empty get exactly the legacy behaviour.
    runtime_overrides: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role_title": self.role_title,
            "role_goal": self.role_goal,
            "role_backstory": self.role_backstory,
            "agent_source": self.agent_source,
            "agent_profile_id": self.agent_profile_id,
            "position": dict(self.position) if self.position else {"x": 0.0, "y": 0.0},
            "level": self.level,
            "department": self.department,
            "custom_prompt": self.custom_prompt,
            "identity_dir": self.identity_dir,
            "mcp_servers": list(self.mcp_servers) if self.mcp_servers else [],
            "skills": list(self.skills) if self.skills else [],
            "skills_mode": self.skills_mode,
            "preferred_endpoint": self.preferred_endpoint,
            "endpoint_policy": self.endpoint_policy,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "timeout_s": self.timeout_s,
            "can_delegate": self.can_delegate,
            "can_escalate": self.can_escalate,
            "can_request_scaling": self.can_request_scaling,
            "auto_clone_enabled": self.auto_clone_enabled,
            "auto_clone_threshold": self.auto_clone_threshold,
            "auto_clone_max": self.auto_clone_max,
            "is_clone": self.is_clone,
            "clone_source": self.clone_source,
            "ephemeral": self.ephemeral,
            "avatar": self.avatar,
            "external_tools": list(self.external_tools) if self.external_tools else [],
            "enable_file_tools": self.enable_file_tools,
            "plugin_origin": dict(self.plugin_origin) if self.plugin_origin else None,
            "frozen_by": self.frozen_by,
            "frozen_reason": self.frozen_reason,
            "frozen_at": self.frozen_at,
            "status": self.status.value,
            "runtime_overrides": (dict(self.runtime_overrides) if self.runtime_overrides else {}),
        }

    @classmethod
    def from_dict(cls, d: dict) -> OrgNode:
        d = dict(d)
        if not d.get("agent_profile_id"):
            d["agent_profile_id"] = infer_agent_profile_id_for_node(d)
        if "status" in d and isinstance(d["status"], str):
            try:
                d["status"] = NodeStatus(d["status"])
            except ValueError:
                d["status"] = NodeStatus.IDLE
        if d.get("endpoint_policy") not in {"prefer", "require"}:
            d["endpoint_policy"] = "prefer"
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class OrgEdge:
    id: str = field(default_factory=lambda: new_org_id("edge_"))
    source: str = ""
    target: str = ""
    edge_type: EdgeType = EdgeType.HIERARCHY
    label: str = ""
    bidirectional: bool = True
    priority: int = 0
    bandwidth_limit: int = 60
    binding: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type.value,
            "label": self.label,
            "bidirectional": self.bidirectional,
            "priority": self.priority,
            "bandwidth_limit": self.bandwidth_limit,
            "binding": dict(self.binding) if self.binding else {},
        }

    @classmethod
    def from_dict(cls, d: dict) -> OrgEdge:
        d = dict(d)
        if "edge_type" in d and isinstance(d["edge_type"], str):
            try:
                d["edge_type"] = EdgeType(d["edge_type"])
            except ValueError:
                d["edge_type"] = EdgeType.HIERARCHY
        edge = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        edge.validate_binding()
        return edge

    def validate_binding(self) -> None:
        """Validate deterministic asset-flow metadata on artifact edges."""
        if self.edge_type != EdgeType.ARTIFACT:
            return
        binding = self.binding
        if not isinstance(binding, dict):
            raise ValueError("artifact edge binding must be an object")
        target_param = binding.get("target_param")
        if not isinstance(target_param, str) or not target_param.strip():
            raise ValueError("artifact edge binding requires target_param")
        value_field = binding.get("value_field")
        if value_field not in {"asset_ids", "task_ids", "segments"}:
            raise ValueError(
                "artifact edge binding value_field must be asset_ids, task_ids, or segments"
            )
        target_tools = binding.get("target_tools")
        if (
            not isinstance(target_tools, list)
            or not target_tools
            or not all(isinstance(tool, str) and tool.strip() for tool in target_tools)
        ):
            raise ValueError("artifact edge binding target_tools must be a non-empty string list")
        if binding.get("cardinality", "many") not in {"one", "many"}:
            raise ValueError("artifact edge binding cardinality must be one or many")
        if "required" in binding and not isinstance(binding["required"], bool):
            raise ValueError("artifact edge binding required must be a boolean")
        accepts = binding.get("accepts", [])
        if not isinstance(accepts, list) or not all(
            isinstance(kind, str) and kind.strip() for kind in accepts
        ):
            raise ValueError("artifact edge binding accepts must be a string list")
        if binding.get("activation", "manual") not in {"manual", "when_ready"}:
            raise ValueError("artifact edge binding activation must be manual or when_ready")
        if binding.get("dispatch_mode", "per_join_key") not in {
            "per_join_key",
            "join_all",
        }:
            raise ValueError("artifact edge binding dispatch_mode must be per_join_key or join_all")
        for key in ("min_count", "max_attempts"):
            value = binding.get(key)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise ValueError(f"artifact edge binding {key} must be a positive integer")
        if int(binding.get("max_attempts", 1) or 1) > 5:
            raise ValueError("artifact edge binding max_attempts must not exceed 5")
        join_scope = binding.get("join_scope")
        if join_scope is not None:
            if not isinstance(join_scope, dict):
                raise ValueError("artifact edge binding join_scope must be an object")
            if not isinstance(join_scope.get("source"), str) or not join_scope["source"].strip():
                raise ValueError("artifact edge binding join_scope requires source")
            if join_scope.get("value_field", "segments") not in {
                "asset_ids",
                "task_ids",
                "segments",
            }:
                raise ValueError("artifact edge binding join_scope value_field is invalid")
            if not isinstance(join_scope.get("key_field", "segment_id"), str):
                raise ValueError("artifact edge binding join_scope key_field must be a string")


@dataclass
class UserPersona:
    """The human user's identity within an organization."""

    title: str = "负责人"
    display_name: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "display_name": self.display_name,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> UserPersona:
        if not d:
            return cls()
        cleaned = {}
        for k, v in d.items():
            if k not in cls.__dataclass_fields__:
                continue
            if isinstance(v, str) and "\ufffd" in v:
                v = v.replace("\ufffd", "")
            cleaned[k] = v
        return cls(**cleaned)

    @property
    def label(self) -> str:
        return self.display_name or self.title


@dataclass
class Organization:
    id: str = field(default_factory=lambda: new_org_id("org_"))
    name: str = ""
    description: str = ""
    icon: str = "🏢"
    status: OrgStatus = OrgStatus.DORMANT
    nodes: list[OrgNode] = field(default_factory=list)
    edges: list[OrgEdge] = field(default_factory=list)

    # Heartbeat
    heartbeat_enabled: bool = False
    heartbeat_interval_s: int = 1800
    heartbeat_prompt: str = "审视组织当前状态，决定是否需要采取行动。"
    heartbeat_max_cascade_depth: int = 3

    # Standup
    standup_enabled: bool = False
    standup_cron: str = "0 9 * * 1-5"
    standup_agenda: str = "各节点汇报进展、阻塞和计划。"

    # Policies
    allow_cross_level: bool = False  # TODO: not yet enforced
    max_delegation_depth: int = 5
    conflict_resolution: str = "manager"  # TODO: not yet enforced

    # Scaling
    scaling_enabled: bool = True
    max_nodes: int = 20
    auto_scale_enabled: bool = False
    auto_scale_max_per_heartbeat: int = 2
    scaling_approval: str = "user"

    # Notifications
    notify_enabled: bool = False
    notify_channel: str | None = None
    notify_webhook_url: str | None = None
    notify_im_channel: str | None = None
    notify_im_bot_id: str | None = None
    notify_push_levels: list[str] = field(default_factory=lambda: ["action", "alert"])
    notify_quiet_hours: str | None = None
    notify_im_approval: bool = True

    # Memory
    shared_memory_enabled: bool = True
    department_memory_enabled: bool = True

    # Metadata
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    is_template: bool = False
    tags: list[str] = field(default_factory=list)

    # Stats
    total_tasks_completed: int = 0
    total_messages_exchanged: int = 0
    total_tokens_used: int = 0

    # User identity within the organization
    user_persona: UserPersona = field(default_factory=UserPersona)

    # Core business mission — drives proactive operations
    core_business: str = ""

    # Canvas layout
    layout_locked: bool = False

    # Token budget (reserved, not enforced initially)
    token_budget: int | None = None  # TODO: not yet enforced
    token_budget_period: str | None = None  # TODO: not yet enforced

    # Operation mode
    operation_mode: str = "command"

    # Workspace — custom output directory for file-producing tools
    workspace_dir: str = ""

    # Watchdog (Sprint-9 supervisor takeover: deprecated no-op fields).
    # 历史字段。Sprint-9 起 supervisor 的 StallDetector + max_turns 已经
    # 取代 wall-clock watchdog，这些字段不再控制任何运行时行为。但仍保留
    # 在 dataclass 上以便已存在的 org spec JSON 文件能向后兼容反序列化
    # （`from_dict` 会按字段名过滤未知键，旧 JSON 里写过的值仍会被读入
    # 并按原值回写）。前端 UI 仍展示这些字段，但开关不会触发任何逻辑。
    # TODO(post-Sprint-9): 删除这些字段 + 前端 UI 板块。
    watchdog_enabled: bool = True
    watchdog_interval_s: int = 30
    watchdog_stuck_threshold_s: int = 1800
    watchdog_silence_threshold_s: int = 1800

    # 交付兜底：当用户原始 prompt 明显需要附件交付，但本任务内 LLM 一个文件
    # 都没产出且最终答复是 ≥200 字的长文时，OrgRuntime 会把该长文自动落盘为
    # ``<workspace>/deliverables/*.md``（走唯一登记入口 _register_file_output），
    # 并给非 root 节点合成一条 TASK_DELIVERED 给上级。默认 True；从组织设置
    # 页可关。该开关只影响"兜底"，关掉不影响 LLM 自己显式调 write_file /
    # org_submit_deliverable 的行为。
    auto_persist_final_answer: bool = True

    # Per-org runtime overrides. Default empty dict — other orgs keep
    # the global defaults. Recognised keys (all opt-in, unknown keys
    # ignored):
    #   - max_iterations: int — cap on ReAct loops at the org level
    #     (a node-level override takes priority when present)
    #   - supervisor_hard_ceiling_s: int — absolute wall-clock ceiling
    #     for one command (60..86400; 0 disables). ``command_timeout_secs``
    #     remains a compatibility alias.
    #   - supervisor_soft_ceiling_ratio: float — cooperative soft budget
    #     as a fraction of the hard ceiling (0 disables; max 0.95)
    #   - supervisor_soft_watchdog_grace_ratio: float — where the forced
    #     soft watchdog fires inside the soft-to-hard interval (0..1)
    #   - command_stuck_warn_secs: int — emit org:command_stuck_warning
    #     after this many seconds of no progress
    #   - inflight_window_secs: float — duplicate-tool-call coalescing
    #     window for the executor
    runtime_overrides: dict = field(default_factory=dict)

    def __post_init__(self):
        self.tags = normalize_tags(self.tags)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "status": self.status.value,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "heartbeat_enabled": self.heartbeat_enabled,
            "heartbeat_interval_s": self.heartbeat_interval_s,
            "heartbeat_prompt": self.heartbeat_prompt,
            "heartbeat_max_cascade_depth": self.heartbeat_max_cascade_depth,
            "standup_enabled": self.standup_enabled,
            "standup_cron": self.standup_cron,
            "standup_agenda": self.standup_agenda,
            "allow_cross_level": self.allow_cross_level,
            "max_delegation_depth": self.max_delegation_depth,
            "conflict_resolution": self.conflict_resolution,
            "scaling_enabled": self.scaling_enabled,
            "max_nodes": self.max_nodes,
            "auto_scale_enabled": self.auto_scale_enabled,
            "auto_scale_max_per_heartbeat": self.auto_scale_max_per_heartbeat,
            "scaling_approval": self.scaling_approval,
            "notify_enabled": self.notify_enabled,
            "notify_channel": self.notify_channel,
            "notify_webhook_url": self.notify_webhook_url,
            "notify_im_channel": self.notify_im_channel,
            "notify_im_bot_id": self.notify_im_bot_id,
            "notify_push_levels": list(self.notify_push_levels) if self.notify_push_levels else [],
            "notify_quiet_hours": self.notify_quiet_hours,
            "notify_im_approval": self.notify_im_approval,
            "shared_memory_enabled": self.shared_memory_enabled,
            "department_memory_enabled": self.department_memory_enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_template": self.is_template,
            "tags": self.tags,
            "total_tasks_completed": self.total_tasks_completed,
            "total_messages_exchanged": self.total_messages_exchanged,
            "total_tokens_used": self.total_tokens_used,
            "user_persona": self.user_persona.to_dict(),
            "core_business": self.core_business,
            "layout_locked": self.layout_locked,
            "token_budget": self.token_budget,
            "token_budget_period": self.token_budget_period,
            "operation_mode": self.operation_mode,
            "workspace_dir": self.workspace_dir,
            "watchdog_enabled": self.watchdog_enabled,
            "watchdog_interval_s": self.watchdog_interval_s,
            "watchdog_stuck_threshold_s": self.watchdog_stuck_threshold_s,
            "watchdog_silence_threshold_s": self.watchdog_silence_threshold_s,
            "auto_persist_final_answer": self.auto_persist_final_answer,
            "runtime_overrides": (dict(self.runtime_overrides) if self.runtime_overrides else {}),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Organization:
        d = dict(d)
        if "status" in d and isinstance(d["status"], str):
            try:
                d["status"] = OrgStatus(d["status"])
            except ValueError:
                d["status"] = OrgStatus.DORMANT
        raw_nodes = d.get("nodes", [])
        raw_edges = d.get("edges", [])
        raw_persona = d.pop("user_persona", None)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known and k not in ("nodes", "edges")}
        org = cls(**filtered)
        org.nodes = [OrgNode.from_dict(n) for n in raw_nodes]
        org.edges = [OrgEdge.from_dict(e) for e in raw_edges if e.get("source") != e.get("target")]
        if isinstance(raw_persona, dict):
            org.user_persona = UserPersona.from_dict(raw_persona)
        return org

    def get_node(self, node_id: str) -> OrgNode | None:
        if not node_id:
            return None
        for n in self.nodes:
            if n.id == node_id:
                return n
        node_id_lower = node_id.lower().replace(" ", "").replace("-", "")
        for n in self.nodes:
            if n.id.lower().replace("-", "") == node_id_lower:
                return n
        query = node_id.strip()
        query_norm = query.replace(" ", "").replace("　", "").lower()
        for n in self.nodes:
            title = n.role_title or ""
            title_norm = title.replace(" ", "").replace("　", "").lower()
            if query == title or query in title or title in query:
                return n
            if query_norm and (
                query_norm == title_norm or query_norm in title_norm or title_norm in query_norm
            ):
                return n
        if len(query_norm) >= 3:
            for n in self.nodes:
                nid = n.id.lower().replace("-", "")
                title = (n.role_title or "").lower().replace(" ", "")
                goal = (getattr(n, "role_goal", "") or "").lower()
                haystack = f"{nid} {title} {goal}"
                parts = [p for p in query_norm.replace("_", "-").split("-") if len(p) >= 2]
                if parts and all(p in haystack for p in parts):
                    return n
        return None

    # ------------------------------------------------------------------
    # Strict reference resolution
    # ------------------------------------------------------------------
    #
    # ``get_node`` above is intentionally lenient — it is the backbone of
    # search-style callers (org_find_colleague, UI reference rendering,
    # historical log reconstruction) where "resolve whatever the user might
    # have typed" is the correct behaviour.
    #
    # For *write-effect* tool parameters (``to_node`` on delegate /
    # send_message / reply_message etc.) the same leniency is actively
    # harmful: if role_titles share a prefix — e.g. "产品总监" vs "产品经理"
    # — the substring branches ``title in query`` / ``query in title`` on
    # L496 can silently resolve the caller's own title to itself or to the
    # wrong sibling, which then triggers the self-delegation guard and
    # spins the ReAct loop until Supervisor terminates it.
    #
    # ``resolve_reference`` is the strict counterpart: it returns a 3-tuple
    #
    #   (exact_match_or_none, candidates, status)
    #
    # where *status* is one of:
    #
    #   "exact_id"         – matched by node id (literal or normalized)
    #   "exact_title"      – exactly one node has this role_title (strict)
    #   "ambiguous_title"  – ≥ 2 nodes share this role_title
    #   "fuzzy"            – no exact match; legacy lenient matching found
    #                        one-or-more candidates (caller decides what
    #                        to do — delegate handler rejects; search
    #                        handler may accept the first candidate)
    #   "not_found"        – nothing matched at all
    #
    # Callers that need the old "any hit wins" behaviour should keep using
    # ``get_node``. Callers that need the strict contract (delegate,
    # send_message, reply_message) should consume ``resolve_reference``
    # directly and surface the candidate list in their error messages.
    def resolve_reference(self, query: str) -> tuple[OrgNode | None, list[OrgNode], str]:
        if not query:
            return None, [], "not_found"

        for n in self.nodes:
            if n.id == query:
                return n, [], "exact_id"

        q_lower = query.lower().replace(" ", "").replace("-", "")
        if q_lower:
            for n in self.nodes:
                if n.id.lower().replace("-", "") == q_lower:
                    return n, [], "exact_id"

        q_title = query.strip()
        if q_title:
            # Case-sensitive exact title wins first; if that also yields
            # multiple hits we still have to report ambiguity (e.g. two
            # nodes literally named "产品经理" across departments).
            exact_title_hits = [n for n in self.nodes if (n.role_title or "").strip() == q_title]
            if len(exact_title_hits) == 1:
                return exact_title_hits[0], [], "exact_title"
            if len(exact_title_hits) >= 2:
                return None, exact_title_hits, "ambiguous_title"

            # Case-insensitive exact title is still considered an exact
            # match (safe — not a substring collision) so that legitimate
            # shorthands like "cto" → role_title "CTO" keep working without
            # being downgraded to fuzzy. Still gate on strict equality after
            # normalizing case + whitespace only (no substring).
            q_norm = q_title.lower().replace(" ", "").replace("　", "")
            if q_norm:
                ci_hits = [
                    n
                    for n in self.nodes
                    if (n.role_title or "").strip().lower().replace(" ", "").replace("　", "")
                    == q_norm
                ]
                if len(ci_hits) == 1:
                    return ci_hits[0], [], "exact_title"
                if len(ci_hits) >= 2:
                    return None, ci_hits, "ambiguous_title"

        fuzzy_hit = self.get_node(query)
        if fuzzy_hit is not None:
            return None, [fuzzy_hit], "fuzzy"

        return None, [], "not_found"

    def get_root_nodes(self) -> list[OrgNode]:
        return [n for n in self.nodes if n.level == 0]

    def get_children(self, node_id: str) -> list[OrgNode]:
        child_ids: set[str] = set()
        for e in self.edges:
            if e.edge_type == EdgeType.HIERARCHY and e.source == node_id and e.target != node_id:
                child_ids.add(e.target)
        return [n for n in self.nodes if n.id in child_ids]

    def get_parent(self, node_id: str) -> OrgNode | None:
        for e in self.edges:
            if e.edge_type == EdgeType.HIERARCHY and e.target == node_id and e.source != node_id:
                return self.get_node(e.source)
        return None

    def get_departments(self) -> list[str]:
        return sorted({n.department for n in self.nodes if n.department})
