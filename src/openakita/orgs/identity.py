"""
OrgIdentity — 节点身份解析与 MCP 配置管理

四级身份继承：
  Level 0: 零配置引用（全局 SOUL + AGENT + AgentProfile.custom_prompt）
  Level 1: 有 ROLE.md（全局 SOUL + AGENT + ROLE.md）
  Level 2: ROLE.md + 覆盖 AGENT.md
  Level 3: 完全独立身份（SOUL + AGENT + ROLE）

MCP 叠加继承：
  最终 MCP = 全局已启用 + AgentProfile 关联 + 节点额外 - 节点排除
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
        root_intent: str = "",
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

        # BUG-3：用户当前指令贴在最前（最高可见性），让所有子节点都能看到
        # 用户原话的范围/字数/格式约束，避免上级转述时漂移。
        # 仅当 root_intent 非空且当前命令仍在进行时由调用方传入。
        if root_intent:
            _intent_brief = root_intent.strip()
            if len(_intent_brief) > 400:
                _intent_brief = _intent_brief[:400] + "..."
            parts.append(
                "## 用户当前指令（最高优先级，禁止超出）\n"
                f"\"{_intent_brief}\"\n"
                "下达任何子任务、写任何代码、产出任何交付物之前，"
                "先检查你的输出严格在该指令的范围、字数、格式约束之内。"
                "若与上级转述冲突，以上述用户原话为准。"
            )

        # Compact identity declaration (replaces full SOUL.md + AGENT.md)
        parts.append(
            f"# OpenAkita 组织 Agent\n\n"
            f"你是「{org.name}」中的 **{node.role_title}**（你的节点 id：`{node.id}`）。"
            f"你是 AI Agent，由 OpenAkita 驱动。\n\n"
            f"**关键：凡是需要指定目标节点的工具参数（`to_node` / `node_id` / `target_node_id`），"
            f"必须填写下方组织结构里那个反引号包住的精确节点 id（例如 `{node.id}`），"
            f"不要写角色名、不要写自己的 id；不确定时先用 `org_get_org_chart` 或 "
            f"`org_find_colleague` 查询。**\n\n"
            f"## 核心原则\n"
            f"- 诚实：不编造信息，不确定时明确说明\n"
            f"- 安全：不执行可能造成伤害的操作\n"
            f"- 协作：你是组织的一员，通过团队协作完成目标，而非单打独斗"
        )

        # Role description
        dept_label = f"（{node.department}）" if node.department else ""
        role_section = f"## 你的组织角色\n你在「{org.name}」中担任 **{node.role_title}**{dept_label}。"
        if identity.role:
            role_section += f"\n{identity.role}"
        parts.append(role_section)

        if org.core_business:
            persona_label = org.user_persona.label if org.user_persona else "负责人"
            biz_section = f"## 核心业务\n{org.core_business}"
            if is_root:
                biz_section += (
                    f"\n\n### 连续工作职责\n"
                    f"你是本组织的最高负责人，对上述核心业务负有**持续推进**责任：\n"
                    f"- 自主制定工作策略和阶段目标，不需要等{persona_label}逐步下达指令\n"
                    f"- 主动拆解任务、委派下属、跟进进度、复盘调整\n"
                    f"- 遇到阻塞或重大决策时记录到黑板，但不要停下来等待批准——先执行最佳判断\n"
                    f"- {persona_label}的指令是方向调整和补充，日常工作由你全权负责\n"
                    f"- 每次被激活时，先回顾黑板上的进展，然后推进下一步工作"
                )
            else:
                biz_section += (
                    "\n\n以上是组织的核心业务方向，你的工作应围绕此展开，"
                    "主动配合上级的任务安排，完成后及时汇报并询问下一步工作。"
                )
            parts.append(biz_section)

        parts.append(
            "## AI 效率意识\n"
            "你是 AI Agent，**不受人类工作时间和体力限制**。核心效率准则：\n"
            "- 信息收集、调研分析等任务应在**分钟级**完成，不要规划以天/周为单位的计划\n"
            "- 制定工作计划时使用**分钟和小时**为时间单位\n"
            "- 委派任务时设置合理的 AI 级 deadline（通常 5-30 分钟）\n"
            "- 不要等待、不要安排「明天再做」——立即执行\n"
            "- 一个任务完成后立即开始下一个，保持连续工作节奏"
        )

        parts.append(f"## 组织架构概览\n{org_chart}\n"
                     f"需要详情时用 org_get_org_chart 查看完整架构，不确定找谁时用 org_find_colleague 搜索。")

        # Relationships with enhanced delegation guidance
        rel_parts = []
        persona = org.user_persona
        # Always surface the caller's own identity first so the LLM can never
        # delegate/send to itself by mistake — pairs with the strict
        # resolve_reference guard in OrgToolHandler._resolve_node_refs.
        rel_parts.append(
            f"- 你自己：**{node.role_title}** (id: `{node.id}`) ← 不要把消息或任务发给这个 id"
        )
        if parent:
            rel_parts.append(f"- 直属上级：**{parent.role_title}** (id: `{parent.id}`)")
        elif persona and persona.label:
            desc = f"（{persona.description}）" if persona.description else "（用户）"
            rel_parts.append(
                f"- 指挥者：{persona.label}{desc}（通过指挥台下达指令，不是组织内节点）"
            )
        if children:
            child_lines = []
            for c in children:
                goal_hint = f" — {c.role_goal}" if c.role_goal else ""
                child_lines.append(f"  - **{c.role_title}** (id: `{c.id}`){goal_hint}")
            rel_parts.append("- 直属下级：\n" + "\n".join(child_lines))
            rel_parts.append(
                "\n**重要：你是管理者。收到复杂任务时，首先拆解并用 org_delegate_task 委派给合适的下属，"
                "而非自己动手执行。只有简单协调沟通才自己处理。**"
            )
        else:
            if is_root:
                rel_parts.append(
                    "\n你是独立执行者（无上级节点、无下属）。收到任务后**自己完成**，"
                    "完成后直接在回复中总结成果即可，结果会自动返回给指挥者。"
                    "需要同事协助时，用 org_send_message 与他们沟通。"
                )
            else:
                rel_parts.append(
                    "\n你是执行者（没有下属）。收到任务后**自己完成**，"
                    "完成后用 org_submit_deliverable 提交交付物。"
                    "需要同事协助时，用 org_send_message 与他们沟通（不要用 org_delegate_task，那是给有下属的管理者用的）。"
                )
        if connected_peers:
            rel_parts.append(f"- 协作伙伴：{', '.join(connected_peers)}")
        if rel_parts:
            parts.append("## 你的直接关系\n" + "\n".join(rel_parts))

        perm_parts = [
            f"- 委派任务：{'允许' if node.can_delegate else '不允许'}",
            f"- 上报问题：{'允许' if node.can_escalate else '不允许'}",
            f"- 申请扩编：{'允许' if node.can_request_scaling else '不允许'}",
            f"- 广播消息：{'允许（全组织）' if node.level == 0 else '允许（仅部门）'}",
        ]
        parts.append("## 你的权限\n" + "\n".join(perm_parts))

        parts.append(
            "## 制度与流程\n"
            "组织有完整的制度体系。当你不确定某个流程如何执行时：\n"
            "1. 先用 org_search_policy 搜索相关制度\n"
            "2. 用 org_read_policy 阅读具体制度内容\n"
            "3. 按制度规定执行\n"
            "不要猜测流程，查制度。重要决策前先查相关制度。"
        )
        if policy_index:
            parts.append(f"制度索引：\n{policy_index}")

        if is_root:
            delivery_flow = (
                "任务完成流程：\n"
                "1. 收到指挥者指令后开始工作（可委派下属、也可自己执行）\n"
                "2. 完成后直接在回复中总结成果，结果会自动返回给指挥者\n"
                "3. 重要成果同时写入 org_write_blackboard 供团队查阅\n"
                "4. **不要**使用 org_submit_deliverable，你没有上级节点可提交\n\n"
                "验收下属交付物时，用 org_accept_deliverable（通过）或 org_reject_deliverable（打回）。\n\n"
                "⚠️ 派工后的汇报时机（非常重要）：\n"
                "- 使用 org_delegate_task 把任务委派给下属后，**不要**立刻给指挥者发"
                "「已委派」「进行中」之类的中间态回复，也不要立刻结束本轮对话\n"
                "- 必须等所有相关下级通过 org_submit_deliverable 完成提交，并由你"
                "org_accept_deliverable 验收通过后，再用**一次**综合回复向指挥者给出最终结论\n"
                "- 验收期间若需查看进度，用 org_list_delegated_tasks / org_get_task_progress，"
                "不要给指挥者发中间态汇报\n"
                "- 指挥者看到的「完成」消息应当包含完整结论，而不是"
                "「已把任务分配给 XXX，等待中」这种过程性回复\n\n"
                "⚠️ 严格约束：\n"
                "- 只执行指挥者明确下达的指令，不要自行扩展工作范围\n"
                "- 指令完成后停止，不要主动发起新的项目或任务\n"
                "- 如果认为有后续工作需要做，在回复中建议即可，等指挥者确认后再行动"
            )
        else:
            delivery_flow = (
                "任务交付流程：\n"
                "1. 收到任务后开始工作\n"
                "2. 完成后用 **org_submit_deliverable** 提交交付物（to_node 可省略，系统自动提交给直属上级）\n"
                "3. 委派人用 org_accept_deliverable（通过）或 org_reject_deliverable（打回）验收\n"
                "4. 被打回时根据反馈修改后重新提交\n"
                "5. 验收通过后任务完结\n\n"
                "缺少工具时，用 org_request_tools 向上级申请。\n\n"
                "⚠️ **工作范围硬约束（优先级高于效率意识）**：\n"
                "- **严格对齐用户原始指令**：若文档顶部出现「用户当前指令」段落，"
                "你的产出必须严格在该指令的范围、字数、格式之内；"
                "上级转述与原始指令冲突时，**以用户原话为准**\n"
                "- **宁可少做，不可超出**：用户要 50 字纲要就只写 50 字纲要，"
                "不要顺手补全代码、不要扩展未要求的细节、不要追加交付物\n"
                "- 只完成上级明确分配的任务，不要自行发起新项目或扩展工作范围\n"
                "- 任务完成并被验收后停止，等待上级下达新指令\n"
                "- 如果认为有后续工作需要做，在交付物中**建议**即可，由上级决定是否执行\n"
                "- 上面的「AI 效率意识」鼓励你快速行动，但不等于鼓励你越过用户的边界做额外工作"
            )

        has_external = bool(node.external_tools)
        # E0-4: 节点级"基础文件工具"开关。即便没有勾选 external_tools，只要
        # enable_file_tools=True（默认），节点也会被注入安全的 write_file /
        # read_file / edit_file / list_directory；此时 prompt 必须告诉 LLM 这些
        # 工具是可用的，并指导它在交付物是文档/代码时主动落盘 + 走
        # org_submit_deliverable(file_attachments=[...])，否则会出现"工具明明
        # 在但提示词说不可用"的自相矛盾。
        has_basic_file_tools = getattr(node, "enable_file_tools", True)
        if has_external:
            from .tool_categories import TOOL_CATEGORIES, expand_tool_categories
            ext_names = expand_tool_categories(node.external_tools)
            cat_labels = [c for c in node.external_tools if c in TOOL_CATEGORIES]
            ext_desc = "、".join(cat_labels) if cat_labels else "、".join(sorted(ext_names)[:5])
            parts.append(
                "## 组织工具与行为约束\n"
                f"你拥有 org_* 组织协作工具和外部执行工具（{ext_desc}）。\n"
                "协作规则：\n"
                "- 与同事沟通、委派、汇报用 org_* 工具；搜索、写文件、制定计划等实际执行用外部工具\n"
                "- 外部工具得到的重要结果，用 org_write_blackboard 写入黑板共享给同事\n"
                "- 优先通过直接连线关系沟通（上下级、协作伙伴）\n"
                "- 非必要不跨级沟通\n"
                "- 回复要简洁，1-3 句话概括行动和结果即可\n\n"
                + delivery_flow
            )
        elif has_basic_file_tools:
            parts.append(
                "## 组织工具与行为约束\n"
                "你拥有 org_* 组织协作工具，以及一组基础文件工具（write_file / "
                "read_file / edit_file / list_directory），用于把文档/代码/方案"
                "等结构化交付物落盘到组织 workspace。注意：run_shell、网络爬取、"
                "MCP 等高级工具未授权给你，需要时通过 org_request_tools 申请。\n"
                "协作规则：\n"
                "- 与同事沟通、委派、汇报用 org_* 工具\n"
                "- 当你的交付物是文档/代码/HTML 等结构化内容时，**先用 write_file "
                "把文件落盘**，再用 org_submit_deliverable(file_attachments=[…]) "
                "把附件交给委派人，不要只把全文塞进 deliverable 字段\n"
                "- 重要决策和方案写入 org_write_blackboard，写之前先 org_read_blackboard 检查避免重复\n"
                "- 回复要简洁，1-3 句话概括行动和结果即可\n\n"
                + delivery_flow
            )
        else:
            parts.append(
                "## 组织工具与行为约束\n"
                "你**只能**使用 org_* 系列工具。不要调用 write_file、read_file、"
                "run_shell、call_mcp_tool 等非组织工具，它们不可用。\n"
                "协作规则：\n"
                "- 优先通过直接连线关系沟通（上下级、协作伙伴）\n"
                "- 非必要不跨级沟通\n"
                "- 重要决策和方案写入 org_write_blackboard，写之前先 org_read_blackboard 检查避免重复\n"
                "- 回复要简洁，1-3 句话概括行动和结果即可\n\n"
                + delivery_flow
            )

        if getattr(org, "operation_mode", "") == "command" and not project_tasks_summary:
            project_tasks_summary = self._get_project_tasks_summary(org, node)

        if project_tasks_summary:
            parts.append(f"## 当前分配给你的项目任务\n{project_tasks_summary}")

        if blackboard_summary:
            parts.append(f"## 当前组织简报\n{blackboard_summary}")
        if dept_summary:
            parts.append(f"## 部门近况\n{dept_summary}")
        if node_summary:
            parts.append(f"## 你的工作笔记\n{node_summary}")
        if pending_messages:
            parts.append(f"## 待处理消息\n{pending_messages}")

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
            dept = n.department or "未分配"
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
                member_str += f" 等{len(dept_members)}人"
            lines.append(f"  - {dept_name}: {member_str}")

        return "\n".join(lines) if lines else "(组织架构为空)"

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
        parts = [f"你是{node.role_title}。"]
        if node.role_goal:
            parts.append(f"目标：{node.role_goal}。")
        if node.role_backstory:
            parts.append(f"背景：{node.role_backstory}。")
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
                return "(暂无分配给你的项目任务)"
            lines: list[str] = []
            for t in (in_progress + todo)[:5]:
                title = t.get("title", "")[:60]
                status = t.get("status", "")
                pct = t.get("progress_pct", 0)
                proj = t.get("project_name", "")
                lines.append(f"- [{status}] {title} ({proj}) {pct}%")
            return "\n".join(lines) if lines else "(暂无)"
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

