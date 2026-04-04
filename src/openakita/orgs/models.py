"""
AgentOrg 核心数据模型

定义组织编排所需的全部数据结构：Organization, OrgNode, OrgEdge,
OrgMessage, OrgMemoryEntry, NodeSchedule 等。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from openakita.memory.types import normalize_tags

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OrgStatus(str, Enum):
    DORMANT = "dormant"
    ACTIVE = "active"
    RUNNING = "running"
    PAUSED = "paused"
    ARCHIVED = "archived"


class NodeStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    WAITING = "waiting"
    ERROR = "error"
    OFFLINE = "offline"
    FROZEN = "frozen"


class EdgeType(str, Enum):
    HIERARCHY = "hierarchy"
    COLLABORATE = "collaborate"
    ESCALATE = "escalate"
    CONSULT = "consult"


class MsgType(str, Enum):
    TASK_ASSIGN = "task_assign"
    TASK_RESULT = "task_result"
    TASK_DELIVERED = "task_delivered"
    TASK_ACCEPTED = "task_accepted"
    TASK_REJECTED = "task_rejected"
    REPORT = "report"
    QUESTION = "question"
    ANSWER = "answer"
    ESCALATE = "escalate"
    BROADCAST = "broadcast"
    DEPT_BROADCAST = "dept_broadcast"
    FEEDBACK = "feedback"
    HANDSHAKE = "handshake"


class MemoryScope(str, Enum):
    ORG = "org"
    DEPARTMENT = "department"
    NODE = "node"


class MemoryType(str, Enum):
    FACT = "fact"
    DECISION = "decision"
    RULE = "rule"
    PROGRESS = "progress"
    LESSON = "lesson"
    RESOURCE = "resource"


class ScheduleType(str, Enum):
    CRON = "cron"
    INTERVAL = "interval"
    ONCE = "once"


class InboxPriority(str, Enum):
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ACTION = "action"
    APPROVAL = "approval"
    ALERT = "alert"


class ProjectType(str, Enum):
    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class ProjectStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}{short}" if prefix else short


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class OrgNode:
    id: str = field(default_factory=lambda: _new_id("node_"))
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
    frozen_by: str | None = None
    frozen_reason: str | None = None
    frozen_at: str | None = None
    status: NodeStatus = NodeStatus.IDLE

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
            "frozen_by": self.frozen_by,
            "frozen_reason": self.frozen_reason,
            "frozen_at": self.frozen_at,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> OrgNode:
        d = dict(d)
        if "status" in d and isinstance(d["status"], str):
            try:
                d["status"] = NodeStatus(d["status"])
            except ValueError:
                d["status"] = NodeStatus.IDLE
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class NodeSchedule:
    id: str = field(default_factory=lambda: _new_id("sched_"))
    name: str = ""
    schedule_type: ScheduleType = ScheduleType.INTERVAL
    cron: str | None = None
    interval_s: int | None = None
    run_at: str | None = None
    prompt: str = ""
    enabled: bool = True
    report_to: str | None = None
    report_condition: str = "on_issue"
    max_tokens_per_run: int = 2000
    last_run_at: str | None = None
    last_result_summary: str | None = None
    consecutive_clean: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "schedule_type": self.schedule_type.value,
            "cron": self.cron,
            "interval_s": self.interval_s,
            "run_at": self.run_at,
            "prompt": self.prompt,
            "enabled": self.enabled,
            "report_to": self.report_to,
            "report_condition": self.report_condition,
            "max_tokens_per_run": self.max_tokens_per_run,
            "last_run_at": self.last_run_at,
            "last_result_summary": self.last_result_summary,
            "consecutive_clean": self.consecutive_clean,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NodeSchedule:
        d = dict(d)
        if "schedule_type" in d and isinstance(d["schedule_type"], str):
            try:
                d["schedule_type"] = ScheduleType(d["schedule_type"])
            except ValueError:
                d["schedule_type"] = ScheduleType.INTERVAL
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class OrgEdge:
    id: str = field(default_factory=lambda: _new_id("edge_"))
    source: str = ""
    target: str = ""
    edge_type: EdgeType = EdgeType.HIERARCHY
    label: str = ""
    bidirectional: bool = True
    priority: int = 0
    bandwidth_limit: int = 60

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
        }

    @classmethod
    def from_dict(cls, d: dict) -> OrgEdge:
        d = dict(d)
        if "edge_type" in d and isinstance(d["edge_type"], str):
            try:
                d["edge_type"] = EdgeType(d["edge_type"])
            except ValueError:
                d["edge_type"] = EdgeType.HIERARCHY
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class UserPersona:
    """The human user's identity within an organization."""
    title: str = "负责人"
    display_name: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "display_name": self.display_name,
                "description": self.description}

    @classmethod
    def from_dict(cls, d: dict | None) -> UserPersona:
        if not d:
            return cls()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def label(self) -> str:
        return self.display_name or self.title


@dataclass
class Organization:
    id: str = field(default_factory=lambda: _new_id("org_"))
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
    notify_enabled: bool = True
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
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
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

    # Token budget (reserved, not enforced initially)
    token_budget: int | None = None  # TODO: not yet enforced
    token_budget_period: str | None = None  # TODO: not yet enforced

    # Operation mode
    operation_mode: str = "command"

    # Watchdog
    watchdog_enabled: bool = True
    watchdog_interval_s: int = 30
    watchdog_stuck_threshold_s: int = 1800
    watchdog_silence_threshold_s: int = 1800

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
            "token_budget": self.token_budget,
            "token_budget_period": self.token_budget_period,
            "operation_mode": self.operation_mode,
            "watchdog_enabled": self.watchdog_enabled,
            "watchdog_interval_s": self.watchdog_interval_s,
            "watchdog_stuck_threshold_s": self.watchdog_stuck_threshold_s,
            "watchdog_silence_threshold_s": self.watchdog_silence_threshold_s,
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
        org.edges = [
            OrgEdge.from_dict(e) for e in raw_edges
            if e.get("source") != e.get("target")
        ]
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
            if query_norm and (query_norm == title_norm or query_norm in title_norm
                              or title_norm in query_norm):
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

    def get_root_nodes(self) -> list[OrgNode]:
        return [n for n in self.nodes if n.level == 0]

    def get_children(self, node_id: str) -> list[OrgNode]:
        child_ids: set[str] = set()
        for e in self.edges:
            if (e.edge_type == EdgeType.HIERARCHY
                    and e.source == node_id and e.target != node_id):
                child_ids.add(e.target)
        return [n for n in self.nodes if n.id in child_ids]

    def get_parent(self, node_id: str) -> OrgNode | None:
        for e in self.edges:
            if (e.edge_type == EdgeType.HIERARCHY
                    and e.target == node_id and e.source != node_id):
                return self.get_node(e.source)
        return None

    def get_departments(self) -> list[str]:
        return sorted({n.department for n in self.nodes if n.department})


@dataclass
class OrgMessage:
    id: str = field(default_factory=lambda: _new_id("msg_"))
    org_id: str = ""
    from_node: str = ""
    to_node: str | None = None
    msg_type: MsgType = MsgType.TASK_ASSIGN
    content: str = ""
    edge_id: str | None = None
    reply_to: str | None = None
    thread_id: str | None = None
    priority: int = 0
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    status: str = "sent"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "msg_type": self.msg_type.value,
            "content": self.content,
            "edge_id": self.edge_id,
            "reply_to": self.reply_to,
            "thread_id": self.thread_id,
            "priority": self.priority,
            "metadata": dict(self.metadata) if self.metadata else {},
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> OrgMessage:
        d = dict(d)
        if "msg_type" in d and isinstance(d["msg_type"], str):
            try:
                d["msg_type"] = MsgType(d["msg_type"])
            except ValueError:
                d["msg_type"] = MsgType.TASK_ASSIGN
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class OrgMemoryEntry:
    id: str = field(default_factory=lambda: _new_id("mem_"))
    org_id: str = ""
    scope: MemoryScope = MemoryScope.ORG
    scope_owner: str = ""
    memory_type: MemoryType = MemoryType.FACT
    content: str = ""
    source_node: str = ""
    source_message_id: str | None = None
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    ttl_hours: int | None = None
    created_at: str = field(default_factory=_now_iso)
    last_accessed_at: str = field(default_factory=_now_iso)
    access_count: int = 0

    def __post_init__(self):
        self.tags = normalize_tags(self.tags)
        try:
            self.importance = float(self.importance)
        except (ValueError, TypeError):
            self.importance = 0.5
        self.importance = max(0.0, min(1.0, self.importance))
        if self.ttl_hours is not None:
            try:
                self.ttl_hours = int(self.ttl_hours)
            except (ValueError, TypeError):
                self.ttl_hours = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "scope": self.scope.value,
            "scope_owner": self.scope_owner,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "source_node": self.source_node,
            "source_message_id": self.source_message_id,
            "tags": self.tags,
            "importance": self.importance,
            "ttl_hours": self.ttl_hours,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> OrgMemoryEntry:
        d = dict(d)
        if "scope" in d and isinstance(d["scope"], str):
            try:
                d["scope"] = MemoryScope(d["scope"])
            except ValueError:
                d["scope"] = MemoryScope.ORG
        if "memory_type" in d and isinstance(d["memory_type"], str):
            try:
                d["memory_type"] = MemoryType(d["memory_type"])
            except ValueError:
                d["memory_type"] = MemoryType.FACT
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class InboxMessage:
    id: str = field(default_factory=lambda: _new_id("inbox_"))
    org_id: str = ""
    org_name: str = ""
    priority: InboxPriority = InboxPriority.INFO
    title: str = ""
    body: str = ""
    source_node: str | None = None
    source_event_id: str = ""
    category: str = "general"
    requires_approval: bool = False
    approval_options: list[str] = field(default_factory=list)
    approval_id: str | None = None
    action_type: str | None = None
    action_payload: dict | None = None
    metadata: dict = field(default_factory=dict)
    status: str = "unread"
    created_at: str = field(default_factory=_now_iso)
    acted_at: str | None = None
    acted_result: str | None = None
    acted_by: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "org_id": self.org_id,
            "org_name": self.org_name,
            "priority": self.priority.value,
            "title": self.title,
            "body": self.body,
            "source_node": self.source_node,
            "source_event_id": self.source_event_id,
            "category": self.category,
            "requires_approval": self.requires_approval,
            "approval_options": self.approval_options,
            "approval_id": self.approval_id,
            "action_type": self.action_type,
            "action_payload": self.action_payload,
            "metadata": dict(self.metadata) if self.metadata else {},
            "status": self.status,
            "created_at": self.created_at,
            "acted_at": self.acted_at,
            "acted_result": self.acted_result,
            "acted_by": self.acted_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> InboxMessage:
        d = dict(d)
        if "priority" in d and isinstance(d["priority"], str):
            try:
                d["priority"] = InboxPriority(d["priority"])
            except ValueError:
                d["priority"] = InboxPriority.INFO
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Project / Task tracking
# ---------------------------------------------------------------------------

@dataclass
class ProjectTask:
    id: str = field(default_factory=lambda: _new_id("task_"))
    project_id: str = ""
    title: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    assignee_node_id: str | None = None
    delegated_by: str | None = None
    chain_id: str | None = None
    parent_task_id: str | None = None
    depth: int = 0
    plan_steps: list = field(default_factory=list)
    execution_log: list = field(default_factory=list)
    priority: int = 0
    progress_pct: int = 0
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    delivered_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict:
        if hasattr(self.status, "value"):
            st = self.status.value
        elif isinstance(self.status, str) and "." in self.status:
            st = self.status.rsplit(".", 1)[-1].lower()
        else:
            st = str(self.status)
        return {
            "id": self.id,
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "status": st,
            "assignee_node_id": self.assignee_node_id,
            "delegated_by": self.delegated_by,
            "chain_id": self.chain_id,
            "parent_task_id": self.parent_task_id,
            "depth": self.depth,
            "plan_steps": list(self.plan_steps) if self.plan_steps else [],
            "execution_log": list(self.execution_log) if self.execution_log else [],
            "priority": self.priority,
            "progress_pct": self.progress_pct,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "delivered_at": self.delivered_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProjectTask:
        d = dict(d)
        if "status" in d and isinstance(d["status"], str):
            raw = d["status"]
            if "." in raw:
                raw = raw.rsplit(".", 1)[-1].lower()
            try:
                d["status"] = TaskStatus(raw)
            except ValueError:
                d["status"] = TaskStatus.TODO
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class OrgProject:
    id: str = field(default_factory=lambda: _new_id("proj_"))
    org_id: str = ""
    name: str = ""
    description: str = ""
    project_type: ProjectType = ProjectType.TEMPORARY
    status: ProjectStatus = ProjectStatus.PLANNING
    owner_node_id: str | None = None
    tasks: list[ProjectTask] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    completed_at: str | None = None

    def to_dict(self) -> dict:
        def _enum_val(v):
            if hasattr(v, "value"):
                return v.value
            if isinstance(v, str) and "." in v:
                return v.rsplit(".", 1)[-1].lower()
            return str(v)

        return {
            "id": self.id,
            "org_id": self.org_id,
            "name": self.name,
            "description": self.description,
            "project_type": _enum_val(self.project_type),
            "status": _enum_val(self.status),
            "owner_node_id": self.owner_node_id,
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> OrgProject:
        d = dict(d)
        if "project_type" in d and isinstance(d["project_type"], str):
            raw_pt = d["project_type"]
            if "." in raw_pt:
                raw_pt = raw_pt.rsplit(".", 1)[-1].lower()
            try:
                d["project_type"] = ProjectType(raw_pt)
            except ValueError:
                d["project_type"] = ProjectType.TEMPORARY
        if "status" in d and isinstance(d["status"], str):
            raw_st = d["status"]
            if "." in raw_st:
                raw_st = raw_st.rsplit(".", 1)[-1].lower()
            try:
                d["status"] = ProjectStatus(raw_st)
            except ValueError:
                d["status"] = ProjectStatus.PLANNING
        raw_tasks = d.pop("tasks", [])
        proj = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        proj.tasks = [ProjectTask.from_dict(t) for t in raw_tasks]
        return proj
