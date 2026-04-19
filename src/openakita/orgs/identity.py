"""
OrgIdentity — node identity resolution and MCP configuration management.

Four-level identity inheritance:
  Level 0: Zero-config reference (global SOUL + AGENT + AgentProfile.custom_prompt)
  Level 1: Has ROLE.md (global SOUL + AGENT + ROLE.md)
  Level 2: ROLE.md + overriding AGENT.md
  Level 3: Fully independent identity (SOUL + AGENT + ROLE)

MCP overlay inheritance:
  Final MCP = globally enabled + AgentProfile associations + node extras - node exclusions
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .models import EdgeType, Organization, OrgNode

logger = logging.getLogger(__name__)


@dataclass
class ResolvedIdentity:
    soul: str
    agent: str
    role: str
    level: int


class OrgIdentity:
    """Resolve per-node identity files with layered inheritance."""

    def __init__(self, org_dir: Path, global_identity_dir: Path | None = None) -> None:
        self._org_dir = org_dir
        self._nodes_dir = org_dir / "nodes"
        self._global_identity_dir = global_identity_dir

    def resolve(self, node: OrgNode, org: Organization) -> ResolvedIdentity:
        """Resolve the full identity for a node using 4-level inheritance."""
        node_identity_dir = self._nodes_dir / node.id / "identity"

        soul = self._read_file(node_identity_dir / "SOUL.md") or self._global_soul()
        agent = self._read_file(node_identity_dir / "AGENT.md") or self._global_agent()
        role = self._read_file(node_identity_dir / "ROLE.md")

        level = 3
        if role:
            if self._read_file(node_identity_dir / "AGENT.md"):
                level = 3 if self._read_file(node_identity_dir / "SOUL.md") else 2
            else:
                level = 1
        else:
            level = 0
            if node.agent_profile_id:
                role = self._get_profile_prompt(node.agent_profile_id) or ""
            if not role and node.custom_prompt:
                role = node.custom_prompt
            if not role:
                role = self._auto_generate_role(node)

        return ResolvedIdentity(soul=soul, agent=agent, role=role, level=level)

    def build_org_context_prompt(
        self, node: OrgNode, org: Organization, identity: ResolvedIdentity,
        blackboard_summary: str = "",
        dept_summary: str = "",
        node_summary: str = "",
        pending_messages: str = "",
        policy_index: str = "",
        project_tasks_summary: str = "",
    ) -> str:
        """Build the full organization context prompt for a node agent.

        Does NOT include identity.soul or identity.agent — those contain
        generic solo-agent philosophies (Ralph Wiggum "never give up",
        "solve everything yourself") that directly conflict with the
        organizational delegation model.  A minimal identity declaration
        is generated instead.
        """
        parent = org.get_parent(node.id)
        children = org.get_children(node.id)
        is_root = (node.level == 0 or not parent)

        connected_peers: list[str] = []
        for e in org.edges:
            if e.edge_type != EdgeType.HIERARCHY:
                if e.source == node.id:
                    peer = org.get_node(e.target)
                    if peer:
                        connected_peers.append(f"**{peer.role_title}** (id: `{peer.id}`)")
                elif e.target == node.id:
                    peer = org.get_node(e.source)
                    if peer:
                        connected_peers.append(f"**{peer.role_title}** (id: `{peer.id}`)")

        org_chart = self._build_brief_org_chart(org)

        parts: list[str] = []

        # Compact identity declaration (replaces full SOUL.md + AGENT.md)
        parts.append(
            f"# OpenAkita Organization Agent\n\n"
            f"You are the **{node.role_title}** in \"{org.name}\" (your node id: `{node.id}`). "
            f"You are an AI Agent powered by OpenAkita.\n\n"
            f"**Important: whenever a tool parameter requires a target node (`to_node` / `node_id` / `target_node_id`), "
            f"you must pass the exact node id enclosed in backticks in the org chart below (e.g. `{node.id}`), "
            f"not a role name and not your own id. When unsure, first use `org_get_org_chart` or "
            f"`org_find_colleague` to look it up.**\n\n"
            f"## Core principles\n"
            f"- Honesty: do not fabricate information; state clearly when you're unsure.\n"
            f"- Safety: do not perform operations that could cause harm.\n"
            f"- Collaboration: you are part of an organization and achieve goals through teamwork, not by going it alone."
        )

        # Role description
        dept_label = f" ({node.department})" if node.department else ""
        role_section = f"## Your organizational role\nYou serve as **{node.role_title}**{dept_label} in \"{org.name}\"."
        if identity.role:
            role_section += f"\n{identity.role}"
        parts.append(role_section)

        if org.core_business:
            persona_label = org.user_persona.label if org.user_persona else "Principal"
            biz_section = f"## Core business\n{org.core_business}"
            if is_root:
                biz_section += (
                    f"\n\n### Continuous work responsibilities\n"
                    f"You are the top leader of this organization and bear **ongoing responsibility** for driving the core business above:\n"
                    f"- Autonomously define work strategy and milestone goals; do not wait for the {persona_label} to hand down instructions step by step.\n"
                    f"- Proactively decompose tasks, delegate to subordinates, track progress, and review and adjust.\n"
                    f"- When blocked or facing major decisions, record them on the blackboard, but do not stop to wait for approval—act on your best judgment first.\n"
                    f"- The {persona_label}'s instructions are course corrections and supplements; you are fully in charge of day-to-day work.\n"
                    f"- Each time you are activated, first review progress on the blackboard, then advance the next step of work."
                )
            else:
                biz_section += (
                    "\n\nThe above is the organization's core business direction; your work should revolve around it. "
                    "Proactively support your superior's task assignments, report promptly after completion, and ask about next steps."
                )
            parts.append(biz_section)

        parts.append(
            "## AI efficiency mindset\n"
            "You are an AI Agent, **not bound by human working hours or stamina**. Core efficiency rules:\n"
            "- Tasks like information gathering and research analysis should be completed on a **minute-level** timescale; do not plan in days or weeks.\n"
            "- Use **minutes and hours** as the time unit when drafting work plans.\n"
            "- When delegating, set reasonable AI-scale deadlines (typically 5-30 minutes).\n"
            "- Don't wait, don't schedule \"do it tomorrow\"—execute immediately.\n"
            "- Start the next task as soon as one finishes, keeping a continuous rhythm."
        )

        parts.append(f"## Organization overview\n{org_chart}\n"
                     f"Use org_get_org_chart for the full structure when needed, or org_find_colleague to search when unsure whom to contact.")

        # Relationships with enhanced delegation guidance
        rel_parts = []
        persona = org.user_persona
        # Always surface the caller's own identity first so the LLM can never
        # delegate/send to itself by mistake — pairs with the strict
        # resolve_reference guard in OrgToolHandler._resolve_node_refs.
        rel_parts.append(
            f"- Yourself: **{node.role_title}** (id: `{node.id}`) ← do not send messages or tasks to this id"
        )
        if parent:
            rel_parts.append(f"- Direct superior: **{parent.role_title}** (id: `{parent.id}`)")
        elif persona and persona.label:
            desc = f" ({persona.description})" if persona.description else " (user)"
            rel_parts.append(
                f"- Commander: {persona.label}{desc} (issues instructions from the command console; not a node inside the organization)"
            )
        if children:
            child_lines = []
            for c in children:
                goal_hint = f" — {c.role_goal}" if c.role_goal else ""
                child_lines.append(f"  - **{c.role_title}** (id: `{c.id}`){goal_hint}")
            rel_parts.append("- Direct subordinates:\n" + "\n".join(child_lines))
            rel_parts.append(
                "\n**Important: you are a manager. When you receive a complex task, first decompose it and use org_delegate_task "
                "to hand it to the right subordinate, rather than doing it yourself. Only handle it yourself for simple coordination or communication.**"
            )
        else:
            if is_root:
                rel_parts.append(
                    "\nYou are a standalone executor (no superior, no subordinates). Once you receive a task, **complete it yourself**; "
                    "after finishing, summarize the results directly in your reply and they will be returned to the commander automatically. "
                    "When you need help from colleagues, use org_send_message to talk to them."
                )
            else:
                rel_parts.append(
                    "\nYou are an executor (no subordinates). Once you receive a task, **complete it yourself**; "
                    "after finishing, submit the deliverable with org_submit_deliverable. "
                    "When you need help from colleagues, use org_send_message to talk to them (don't use org_delegate_task; that's for managers with subordinates)."
                )
        if connected_peers:
            rel_parts.append(f"- Collaboration partners: {', '.join(connected_peers)}")
        if rel_parts:
            parts.append("## Your direct relationships\n" + "\n".join(rel_parts))

        perm_parts = [
            f"- Delegate tasks: {'allowed' if node.can_delegate else 'not allowed'}",
            f"- Escalate issues: {'allowed' if node.can_escalate else 'not allowed'}",
            f"- Request scaling: {'allowed' if node.can_request_scaling else 'not allowed'}",
            f"- Broadcast messages: {'allowed (entire organization)' if node.level == 0 else 'allowed (department only)'}",
        ]
        parts.append("## Your permissions\n" + "\n".join(perm_parts))

        parts.append(
            "## Policies and procedures\n"
            "The organization has a complete policy system. When you're unsure how to execute a process:\n"
            "1. First use org_search_policy to find the relevant policy.\n"
            "2. Use org_read_policy to read the full policy text.\n"
            "3. Execute according to the policy.\n"
            "Don't guess at processes—look up the policy. Check the relevant policy before any important decision."
        )
        if policy_index:
            parts.append(f"Policy index:\n{policy_index}")

        if is_root:
            delivery_flow = (
                "Task completion flow:\n"
                "1. Start work after receiving instructions from the commander (you may delegate to subordinates or execute yourself).\n"
                "2. Once finished, summarize the results directly in your reply; they will be returned to the commander automatically.\n"
                "3. Also write important results to org_write_blackboard so the team can reference them.\n"
                "4. **Do not** use org_submit_deliverable; you have no superior node to submit to.\n\n"
                "When reviewing subordinates' deliverables, use org_accept_deliverable (approve) or org_reject_deliverable (reject).\n\n"
                "⚠️ Reporting timing after delegation (very important):\n"
                "- After delegating a task to a subordinate with org_delegate_task, **do not** immediately send the commander "
                "intermediate replies like \"delegated\" or \"in progress\", and do not end this turn right away.\n"
                "- You must wait until all relevant subordinates have submitted via org_submit_deliverable and you have approved "
                "them with org_accept_deliverable, then send the commander **one** consolidated reply with the final conclusion.\n"
                "- If you need to check progress during review, use org_list_delegated_tasks / org_get_task_progress; "
                "do not send the commander interim status updates.\n"
                "- The \"done\" message the commander sees should contain the full conclusion, not process updates like "
                "\"assigned to XXX, waiting\".\n\n"
                "⚠️ Strict constraints:\n"
                "- Only execute instructions the commander explicitly issues; do not expand scope on your own.\n"
                "- Stop after the instruction is completed; do not proactively start new projects or tasks.\n"
                "- If you think follow-up work is needed, suggest it in your reply and act only after the commander confirms."
            )
        else:
            delivery_flow = (
                "Task delivery flow:\n"
                "1. Start work after receiving a task.\n"
                "2. When finished, submit the deliverable with **org_submit_deliverable** (to_node is optional; the system routes it to your direct superior).\n"
                "3. The delegator reviews via org_accept_deliverable (approve) or org_reject_deliverable (reject).\n"
                "4. If rejected, revise based on the feedback and resubmit.\n"
                "5. The task is complete once approved.\n\n"
                "When missing tools, use org_request_tools to request them from your superior.\n\n"
                "⚠️ Scope constraints:\n"
                "- Only complete the tasks your superior assigns; do not start new projects or expand scope on your own.\n"
                "- Stop after the task is completed and approved; wait for new instructions from your superior.\n"
                "- If you think follow-up work is needed, suggest it in the deliverable and let your superior decide."
            )

        has_external = bool(node.external_tools)
        if has_external:
            from .tool_categories import TOOL_CATEGORIES, expand_tool_categories
            ext_names = expand_tool_categories(node.external_tools)
            cat_labels = [c for c in node.external_tools if c in TOOL_CATEGORIES]
            ext_desc = ", ".join(cat_labels) if cat_labels else ", ".join(sorted(ext_names)[:5])
            parts.append(
                "## Organization tools and behavior constraints\n"
                f"You have org_* organizational collaboration tools and external execution tools ({ext_desc}).\n"
                "Collaboration rules:\n"
                "- Use org_* tools to communicate with colleagues, delegate, and report; use external tools for actual execution such as searching, writing files, and planning.\n"
                "- Share important results from external tools with colleagues by writing to org_write_blackboard.\n"
                "- Prefer communicating through direct links (superior/subordinate, collaboration partners).\n"
                "- Avoid cross-level communication unless necessary.\n"
                "- Keep replies concise; 1-3 sentences summarizing the action and the result is enough.\n\n"
                + delivery_flow
            )
        else:
            parts.append(
                "## Organization tools and behavior constraints\n"
                "You **may only** use the org_* family of tools. Do not call non-organizational tools such as write_file, read_file, "
                "run_shell, or call_mcp_tool—they are unavailable.\n"
                "Collaboration rules:\n"
                "- Prefer communicating through direct links (superior/subordinate, collaboration partners).\n"
                "- Avoid cross-level communication unless necessary.\n"
                "- Write important decisions and plans to org_write_blackboard; before writing, call org_read_blackboard to avoid duplication.\n"
                "- Keep replies concise; 1-3 sentences summarizing the action and the result is enough.\n\n"
                + delivery_flow
            )

        if getattr(org, "operation_mode", "") == "command" and not project_tasks_summary:
            project_tasks_summary = self._get_project_tasks_summary(org, node)

        if project_tasks_summary:
            parts.append(f"## Project tasks currently assigned to you\n{project_tasks_summary}")

        if blackboard_summary:
            parts.append(f"## Current organization brief\n{blackboard_summary}")
        if dept_summary:
            parts.append(f"## Department updates\n{dept_summary}")
        if node_summary:
            parts.append(f"## Your work notes\n{node_summary}")
        if pending_messages:
            parts.append(f"## Pending messages\n{pending_messages}")

        return "\n\n".join(parts)

    def resolve_mcp_config(self, node: OrgNode) -> dict:
        """Resolve MCP configuration with overlay inheritance."""
        mcp_path = self._nodes_dir / node.id / "mcp_config.json"
        if not mcp_path.is_file():
            return {"mode": "inherit"}
        try:
            return json.loads(mcp_path.read_text(encoding="utf-8"))
        except Exception:
            return {"mode": "inherit"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_brief_org_chart(self, org: Organization) -> str:
        """Build a compact org chart for prompt injection (~200-500 tokens).

        Format includes node IDs so agents can reference colleagues directly.
        """
        departments: dict[str, list[OrgNode]] = {}
        roots: list[OrgNode] = []
        root_ids: set[str] = set()
        for n in org.nodes:
            if n.level == 0:
                roots.append(n)
                root_ids.add(n.id)
            dept = n.department or "Unassigned"
            departments.setdefault(dept, []).append(n)

        lines: list[str] = []
        for root in roots:
            goal = f" -- {root.role_goal[:30]}" if root.role_goal else ""
            lines.append(f"- {root.role_title}(`{root.id}`){goal}")

        for dept_name, members in sorted(departments.items()):
            dept_members = [m for m in members if m.id not in root_ids]
            if not dept_members:
                continue
            member_str = ", ".join(
                f"{m.role_title}(`{m.id}`)" for m in dept_members[:6]
            )
            if len(dept_members) > 6:
                member_str += f" and {len(dept_members)} others"
            lines.append(f"  - {dept_name}: {member_str}")

        return "\n".join(lines) if lines else "(Organization chart is empty)"

    def _global_soul(self) -> str:
        if self._global_identity_dir:
            return self._read_file(self._global_identity_dir / "SOUL.md") or ""
        return ""

    def _global_agent(self) -> str:
        if self._global_identity_dir:
            core = self._read_file(self._global_identity_dir / "agent.core.md")
            if core:
                return core
            return self._read_file(self._global_identity_dir / "AGENT.md") or ""
        return ""

    def _get_profile_prompt(self, profile_id: str) -> str | None:
        try:
            from openakita.main import _orchestrator
            if _orchestrator and hasattr(_orchestrator, "_profile_store"):
                profile = _orchestrator._profile_store.get(profile_id)
                return profile.custom_prompt if profile else None
        except (ImportError, AttributeError):
            pass
        try:
            from openakita.agents.profile import get_profile_store
            store = get_profile_store()
            profile = store.get(profile_id)
            return profile.custom_prompt if profile else None
        except Exception:
            return None

    def _auto_generate_role(self, node: OrgNode) -> str:
        parts = [f"You are the {node.role_title}."]
        if node.role_goal:
            parts.append(f" Goal: {node.role_goal}.")
        if node.role_backstory:
            parts.append(f" Background: {node.role_backstory}.")
        return "".join(parts)

    def _get_project_tasks_summary(self, org: Organization, node: OrgNode) -> str:
        """Get summary of project tasks assigned to this node (for command mode)."""
        if getattr(org, "operation_mode", "") != "command":
            return ""
        try:
            from openakita.orgs.project_store import ProjectStore

            store = ProjectStore(self._org_dir)
            tasks = store.all_tasks(
                assignee=node.id,
                status=None,
            )
            in_progress = [t for t in tasks if t.get("status") == "in_progress"]
            todo = [t for t in tasks if t.get("status") == "todo"]
            if not in_progress and not todo:
                return "(No project tasks currently assigned to you)"
            lines: list[str] = []
            for t in (in_progress + todo)[:5]:
                title = t.get("title", "")[:60]
                status = t.get("status", "")
                pct = t.get("progress_pct", 0)
                proj = t.get("project_name", "")
                lines.append(f"- [{status}] {title} ({proj}) {pct}%")
            return "\n".join(lines) if lines else "(None)"
        except Exception:
            return ""

    @staticmethod
    def _read_file(path: Path) -> str | None:
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8").strip()
                return content if content else None
            except Exception:
                return None
        return None
