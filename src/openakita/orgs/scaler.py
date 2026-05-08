"""
OrgScaler — 动态扩编/缩编管理

支持克隆（加人手）、招募（新岗位）、裁撤（临时节点），
含审批链和防失控机制。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import OrgRuntime

from .models import (
    EdgeType,
    NodeStatus,
    Organization,
    OrgEdge,
    OrgNode,
    _new_id,
    _now_iso,
)

logger = logging.getLogger(__name__)


@dataclass
class ScalingRequest:
    id: str = field(default_factory=lambda: _new_id("scale_"))
    org_id: str = ""
    request_type: str = "clone"
    requester_node_id: str = ""
    source_node_id: str | None = None
    role_title: str | None = None
    role_goal: str | None = None
    department: str | None = None
    parent_node_id: str | None = None
    reason: str = ""
    ephemeral: bool = True
    status: str = "pending"
    created_at: str = field(default_factory=_now_iso)
    resolved_at: str | None = None
    resolved_by: str | None = None
    result_node_id: str | None = None


class OrgScaler:
    """Dynamic scaling (clone/recruit/dismiss) for organizations."""

    def __init__(self, runtime: OrgRuntime) -> None:
        self._runtime = runtime
        self._pending: dict[str, list[ScalingRequest]] = {}

    def get_pending_requests(self, org_id: str) -> list[ScalingRequest]:
        return self._pending.get(org_id, [])

    # ------------------------------------------------------------------
    # Auto-clone — triggered when a node's mailbox exceeds threshold
    # ------------------------------------------------------------------

    async def maybe_auto_clone(self, org_id: str, node_id: str, pending_count: int) -> OrgNode | None:
        """Check if auto-clone should trigger, and if so, create a clone immediately."""
        org = self._runtime.get_org(org_id)
        if not org or not org.scaling_enabled:
            return None
        node = org.get_node(node_id)
        if not node or not node.auto_clone_enabled:
            return None
        if node.is_clone:
            return None
        if pending_count < node.auto_clone_threshold:
            return None
        if len(org.nodes) >= org.max_nodes:
            return None

        existing_clones = [n for n in org.nodes if n.clone_source == node_id and n.status != NodeStatus.OFFLINE]
        if len(existing_clones) >= node.auto_clone_max:
            return None

        clone_count = len(existing_clones)
        new_node = OrgNode(
            id=_new_id("node_"),
            role_title=f"{node.role_title} (#{clone_count + 2})",
            role_goal=node.role_goal,
            role_backstory=node.role_backstory,
            agent_source=node.agent_source,
            agent_profile_id=node.agent_profile_id,
            position={
                "x": node.position.get("x", 0) + 80 * (clone_count + 1),
                "y": node.position.get("y", 0) + 50,
            },
            level=node.level,
            department=node.department,
            custom_prompt=node.custom_prompt,
            mcp_servers=list(node.mcp_servers),
            skills=list(node.skills),
            skills_mode=node.skills_mode,
            preferred_endpoint=node.preferred_endpoint,
            endpoint_policy=node.endpoint_policy,
            max_concurrent_tasks=node.max_concurrent_tasks,
            timeout_s=node.timeout_s,
            can_delegate=node.can_delegate,
            can_escalate=node.can_escalate,
            can_request_scaling=node.can_request_scaling,
            external_tools=list(node.external_tools),
            is_clone=True,
            clone_source=node_id,
            ephemeral=True,
        )

        org.nodes.append(new_node)

        parent = org.get_parent(node_id)
        if parent:
            org.edges.append(OrgEdge(
                source=parent.id,
                target=new_node.id,
                edge_type=EdgeType.HIERARCHY,
            ))

        org.edges.append(OrgEdge(
            source=node_id,
            target=new_node.id,
            edge_type=EdgeType.COLLABORATE,
            label="clone-of",
        ))

        await self._runtime._save_org(org)

        self._runtime.get_event_store(org_id).emit(
            "auto_clone_created", node_id,
            {"clone_id": new_node.id, "pending_count": pending_count,
             "threshold": node.auto_clone_threshold},
        )

        logger.info(
            f"[Scaler] Auto-cloned {node.role_title} -> {new_node.role_title} "
            f"(pending={pending_count}, threshold={node.auto_clone_threshold})"
        )

        return new_node

    # ------------------------------------------------------------------
    # Clone — add manpower to existing role
    # ------------------------------------------------------------------

    async def request_clone(
        self, org_id: str, requester: str, source_node_id: str,
        reason: str, ephemeral: bool = True,
    ) -> ScalingRequest:
        org = self._runtime.get_org(org_id)
        if not org:
            raise ValueError("Organization not found")
        if len(org.nodes) >= org.max_nodes:
            raise ValueError(f"Node limit reached ({org.max_nodes})")

        req = ScalingRequest(
            org_id=org_id,
            request_type="clone",
            requester_node_id=requester,
            source_node_id=source_node_id,
            reason=reason,
            ephemeral=ephemeral,
        )
        self._pending.setdefault(org_id, []).append(req)

        self._runtime.get_event_store(org_id).emit(
            "scaling_requested", requester,
            {"type": "clone", "source": source_node_id, "reason": reason},
        )

        if org.scaling_approval == "auto" and org.auto_scale_enabled:
            return await self.approve_request(org_id, req.id, "auto")

        return req

    def request_recruit(
        self, org_id: str, requester: str,
        role_title: str, role_goal: str,
        department: str, parent_node_id: str,
        reason: str,
    ) -> ScalingRequest:
        org = self._runtime.get_org(org_id)
        if not org:
            raise ValueError("Organization not found")
        if len(org.nodes) >= org.max_nodes:
            raise ValueError(f"Node limit reached ({org.max_nodes})")

        req = ScalingRequest(
            org_id=org_id,
            request_type="recruit",
            requester_node_id=requester,
            role_title=role_title,
            role_goal=role_goal,
            department=department,
            parent_node_id=parent_node_id,
            reason=reason,
            ephemeral=False,
        )
        self._pending.setdefault(org_id, []).append(req)

        self._runtime.get_event_store(org_id).emit(
            "scaling_requested", requester,
            {"type": "recruit", "role_title": role_title, "reason": reason},
        )

        return req

    # ------------------------------------------------------------------
    # Approve / Reject
    # ------------------------------------------------------------------

    async def approve_request(
        self, org_id: str, request_id: str, approved_by: str = "user"
    ) -> ScalingRequest:
        req = self._find_request(org_id, request_id)
        if not req:
            raise ValueError(f"Request not found: {request_id}")
        if req.status != "pending":
            raise ValueError(f"Request already resolved: {req.status}")

        org = self._runtime.get_org(org_id)
        if not org:
            raise ValueError("Organization not found")

        if req.request_type == "clone":
            new_node = self._execute_clone(org, req)
        elif req.request_type == "recruit":
            new_node = self._execute_recruit(org, req)
        else:
            raise ValueError(f"Unknown request type: {req.request_type}")

        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            messenger.register_node(
                new_node.id,
                self._runtime._make_message_handler(org_id, new_node.id),
            )

        req.status = "approved"
        req.resolved_at = _now_iso()
        req.resolved_by = approved_by
        req.result_node_id = new_node.id

        await self._runtime._save_org(org)

        self._runtime.get_event_store(org_id).emit(
            "scaling_approved", approved_by,
            {"request_id": req.id, "new_node_id": new_node.id},
        )

        return req

    def reject_request(
        self, org_id: str, request_id: str, rejected_by: str = "user",
        reason: str = "",
    ) -> ScalingRequest:
        req = self._find_request(org_id, request_id)
        if not req:
            raise ValueError(f"Request not found: {request_id}")

        req.status = "rejected"
        req.resolved_at = _now_iso()
        req.resolved_by = rejected_by

        self._runtime.get_event_store(org_id).emit(
            "scaling_rejected", rejected_by,
            {"request_id": req.id, "reason": reason},
        )

        return req

    # ------------------------------------------------------------------
    # Dismiss — remove ephemeral nodes
    # ------------------------------------------------------------------

    async def try_reclaim_idle_clones(self, org_id: str) -> list[str]:
        """Dismiss idle ephemeral clones that have no pending messages."""
        org = self._runtime.get_org(org_id)
        if not org:
            return []
        messenger = self._runtime.get_messenger(org_id)
        dismissed: list[str] = []
        for node in list(org.nodes):
            if not node.is_clone or not node.ephemeral:
                continue
            if node.status not in (NodeStatus.IDLE, NodeStatus.OFFLINE):
                continue
            pending = messenger.get_pending_count(node.id) if messenger else 0
            if pending > 0:
                continue
            if await self.dismiss_node(org_id, node.id, by="auto_reclaim"):
                dismissed.append(node.id)
        return dismissed

    async def dismiss_node(self, org_id: str, node_id: str, by: str = "user") -> bool:
        org = self._runtime.get_org(org_id)
        if not org:
            return False
        node = org.get_node(node_id)
        if not node:
            return False
        if not node.ephemeral:
            logger.warning(f"[Scaler] Cannot dismiss non-ephemeral node: {node_id}")
            return False

        bb = self._runtime.get_blackboard(org_id)
        if bb:
            node_memories = bb.read_node(node_id, limit=50)
            for mem in node_memories:
                bb.write_department(
                    node.department, mem.content, node_id,
                    memory_type=mem.memory_type,
                    tags=mem.tags + ["dismissed_node"],
                )

        org.nodes = [n for n in org.nodes if n.id != node_id]
        org.edges = [e for e in org.edges if e.source != node_id and e.target != node_id]

        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            messenger.unregister_node(node_id)

        await self._runtime._save_org(org)

        self._runtime.get_event_store(org_id).emit(
            "node_dismissed", by, {"node_id": node_id, "role": node.role_title},
        )

        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _execute_clone(self, org: Organization, req: ScalingRequest) -> OrgNode:
        source = org.get_node(req.source_node_id or "")
        if not source:
            raise ValueError(f"Source node not found: {req.source_node_id}")

        clone_count = sum(1 for n in org.nodes if n.clone_source == source.id)

        new_node = OrgNode(
            id=_new_id("node_"),
            role_title=f"{source.role_title} (副本{clone_count + 1})",
            role_goal=source.role_goal,
            role_backstory=source.role_backstory,
            agent_source=source.agent_source,
            agent_profile_id=source.agent_profile_id,
            position={
                "x": source.position.get("x", 0) + 80 * (clone_count + 1),
                "y": source.position.get("y", 0) + 50,
            },
            level=source.level,
            department=source.department,
            custom_prompt=source.custom_prompt,
            mcp_servers=list(source.mcp_servers),
            skills=list(source.skills),
            skills_mode=source.skills_mode,
            preferred_endpoint=source.preferred_endpoint,
            endpoint_policy=source.endpoint_policy,
            max_concurrent_tasks=source.max_concurrent_tasks,
            timeout_s=source.timeout_s,
            can_delegate=source.can_delegate,
            can_escalate=source.can_escalate,
            can_request_scaling=source.can_request_scaling,
            external_tools=list(source.external_tools),
            is_clone=True,
            clone_source=source.id,
            ephemeral=req.ephemeral,
        )

        org.nodes.append(new_node)

        parent = org.get_parent(source.id)
        if parent:
            org.edges.append(OrgEdge(
                source=parent.id,
                target=new_node.id,
                edge_type=EdgeType.HIERARCHY,
            ))

        return new_node

    def _execute_recruit(self, org: Organization, req: ScalingRequest) -> OrgNode:
        parent = org.get_node(req.parent_node_id or "")

        new_node = OrgNode(
            id=_new_id("node_"),
            role_title=req.role_title or "新岗位",
            role_goal=req.role_goal or "",
            department=req.department or "",
            level=(parent.level + 1) if parent else 1,
            position={
                "x": (parent.position.get("x", 0) + 100) if parent else 300,
                "y": (parent.position.get("y", 0) + 150) if parent else 300,
            },
            ephemeral=req.ephemeral,
        )

        org.nodes.append(new_node)

        if parent:
            org.edges.append(OrgEdge(
                source=parent.id,
                target=new_node.id,
                edge_type=EdgeType.HIERARCHY,
            ))

        return new_node

    def _find_request(self, org_id: str, request_id: str) -> ScalingRequest | None:
        for req in self._pending.get(org_id, []):
            if req.id == request_id:
                return req
        return None
