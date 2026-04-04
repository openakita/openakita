"""
Organization setup handler — create and manage organizations through natural language.

Only registered when settings.multi_agent_enabled is True.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

_VALID_ACTIONS = (
    "get_resources", "list_orgs", "get_org",
    "preview", "create", "create_from_template",
    "update_org", "delete_org",
)


class OrgSetupHandler:
    """Handles the setup_organization tool with sub-actions."""

    TOOLS = ["setup_organization"]

    def __init__(self, agent: Agent):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        if tool_name != "setup_organization":
            return f"❌ Unknown tool: {tool_name}"

        action = params.get("action", "")
        if action == "get_resources":
            return self._get_resources()
        elif action == "list_orgs":
            return self._list_orgs()
        elif action == "get_org":
            return self._get_org(params)
        elif action == "preview":
            return self._preview(params)
        elif action == "create":
            return await self._create(params)
        elif action == "create_from_template":
            return await self._create_from_template(params)
        elif action == "update_org":
            return await self._update_org(params)
        elif action == "delete_org":
            return await self._delete_org(params)
        return (
            f"❌ Unknown action: {action}. "
            f"Valid: {', '.join(_VALID_ACTIONS)}"
        )

    # ------------------------------------------------------------------
    # get_resources
    # ------------------------------------------------------------------

    def _get_resources(self) -> str:
        result: dict[str, Any] = {}

        preset_ids: set[str] = set()
        try:
            from ...agents.presets import SYSTEM_PRESETS
            agents = []
            for p in SYSTEM_PRESETS:
                if getattr(p, "hidden", False):
                    continue
                agents.append({
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "category": getattr(p, "category", "general"),
                    "skills_summary": p.skills[:5] if p.skills else ["all (全能)"],
                })
                preset_ids.add(p.id)
            result["agents"] = agents
        except Exception as e:
            logger.warning(f"[OrgSetup] Failed to load agent presets: {e}")
            result["agents"] = []

        try:
            from ...agents.profile import get_profile_store
            store = get_profile_store()
            custom_agents = []
            for p in store.list_all(include_ephemeral=False, include_hidden=False):
                if p.id in preset_ids or p.is_system:
                    continue
                custom_agents.append({
                    "id": p.id,
                    "name": p.name,
                    "description": p.description or "",
                    "category": "custom",
                    "skills_summary": p.skills[:5] if p.skills else ["all (全能)"],
                })
            if custom_agents:
                result["custom_agents"] = custom_agents
        except Exception as e:
            logger.warning(f"[OrgSetup] Failed to load custom profiles: {e}")

        try:
            manager = self._get_org_manager()
            if manager:
                result["templates"] = manager.list_templates()
            else:
                result["templates"] = []
        except Exception as e:
            logger.warning(f"[OrgSetup] Failed to load templates: {e}")
            result["templates"] = []

        try:
            from ...orgs.tool_categories import TOOL_CATEGORIES
            result["tool_categories"] = {
                name: tools for name, tools in TOOL_CATEGORIES.items()
            }
        except Exception:
            result["tool_categories"] = {}

        result["usage_hint"] = (
            "请根据以上信息为用户设计组织架构。"
            "为每个节点选择最合适的 agent（agent_profile_id），"
            "并配置合适的工具类目（external_tools）。"
            "信息不足时请向用户询问。"
        )

        return json.dumps(result, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # list_orgs
    # ------------------------------------------------------------------

    def _list_orgs(self) -> str:
        manager = self._get_org_manager()
        if manager is None:
            return "❌ 组织管理器未初始化"

        try:
            orgs = manager.list_orgs(include_archived=True)
        except Exception as e:
            logger.error(f"[OrgSetup] list_orgs failed: {e}", exc_info=True)
            return f"❌ 获取组织列表失败: {e}"

        if not orgs:
            return "当前没有任何组织。可以使用 create 或 create_from_template 创建。"

        lines = [f"现有组织共 {len(orgs)} 个：\n"]
        for o in orgs:
            status = o.get("status", "unknown")
            lines.append(
                f"- **{o.get('name', '')}** (ID: {o.get('id', '')})\n"
                f"  状态: {status} | 节点: {o.get('node_count', 0)} | "
                f"边: {o.get('edge_count', 0)}"
            )
        lines.append(
            "\n使用 get_org 查看某个组织的完整结构，"
            "使用 update_org 修改，使用 delete_org 删除。"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # get_org
    # ------------------------------------------------------------------

    def _get_org(self, params: dict[str, Any]) -> str:
        org_id = params.get("org_id", "")
        if not org_id:
            return "❌ get_org 需要提供 org_id"

        manager = self._get_org_manager()
        if manager is None:
            return "❌ 组织管理器未初始化"

        org = manager.get(org_id)
        if org is None:
            return f"❌ 组织 '{org_id}' 不存在"

        lines = [
            f"## 组织：{org.name}",
            f"- ID: {org.id}",
            f"- 状态: {org.status.value if hasattr(org.status, 'value') else org.status}",
            f"- 描述: {org.description or '(无)'}",
            f"- 核心业务: {org.core_business or '(无)'}",
            "",
            f"### 节点 ({len(org.nodes)} 个)\n",
        ]

        for n in sorted(org.nodes, key=lambda x: (x.level, x.department)):
            indent = "  " * n.level
            agent_label = self._get_agent_label(n.agent_profile_id)
            dept = f" [{n.department}]" if n.department else ""
            tools = n.external_tools or []
            tools_str = f" | 工具: {', '.join(tools)}" if tools else ""

            lines.append(
                f"{indent}- **{n.role_title}**{dept}\n"
                f"{indent}  ID: `{n.id}` | Agent: {agent_label}{tools_str}"
            )
            if n.role_goal:
                lines.append(f"{indent}  目标: {n.role_goal}")

        hierarchy_edges = []
        other_edges = []
        for e in org.edges:
            etype = e.edge_type.value if hasattr(e.edge_type, "value") else e.edge_type
            if etype == "hierarchy":
                hierarchy_edges.append(e)
            else:
                other_edges.append(e)

        lines.append(f"\n### 连线关系 ({len(org.edges)} 条)\n")

        if hierarchy_edges:
            lines.append("**层级（hierarchy）：**")
            for e in hierarchy_edges:
                src = self._find_title_by_node_id(org.nodes, e.source)
                tgt = self._find_title_by_node_id(org.nodes, e.target)
                lines.append(f"- {src} → {tgt}")

        if other_edges:
            lines.append("\n**协作/咨询/上报：**")
            for e in other_edges:
                src = self._find_title_by_node_id(org.nodes, e.source)
                tgt = self._find_title_by_node_id(org.nodes, e.target)
                etype = e.edge_type.value if hasattr(e.edge_type, "value") else e.edge_type
                label = f" \"{e.label}\"" if getattr(e, "label", "") else ""
                bidir = "↔" if getattr(e, "bidirectional", True) else "→"
                lines.append(
                    f"- `{e.id}` {src} {bidir} {tgt} ({etype}{label})"
                )

        lines.append(
            "\n---\n"
            "使用 update_org 修改此组织。修改节点时请提供 node_id 以精确匹配。\n"
            "添加协作连线用 add_edges，删除连线用 remove_edges（传 edge ID）。"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # preview
    # ------------------------------------------------------------------

    def _preview(self, params: dict[str, Any]) -> str:
        name = params.get("name", "")
        if not name:
            return "❌ preview 需要提供 name（组织名称）"

        nodes_raw = params.get("nodes", [])
        if not nodes_raw:
            return "❌ preview 需要提供 nodes（节点列表）"

        nodes, edges, errors = self._build_org_structure(params)
        if errors:
            return "⚠️ 结构校验发现问题：\n" + "\n".join(f"- {e}" for e in errors)

        lines = [f"## 组织架构预览：{name}\n"]
        if params.get("core_business"):
            lines.append(f"核心业务：{params['core_business']}\n")

        lines.append(f"节点数：{len(nodes)}，连线：{len(edges)} 条\n")
        lines.append("### 节点明细\n")

        for n in sorted(nodes, key=lambda x: (x.get("level", 0), x.get("department", ""))):
            indent = "  " * n.get("level", 0)
            agent_id = n.get("agent_profile_id", "default")
            agent_label = self._get_agent_label(agent_id)
            dept = n.get("department", "")
            dept_str = f" [{dept}]" if dept else ""
            tools = n.get("external_tools", [])
            tools_str = f" 工具: {', '.join(tools)}" if tools else ""

            lines.append(
                f"{indent}- **{n['role_title']}**{dept_str} → Agent: {agent_label}"
                f"{tools_str}"
            )

        h_edges = [e for e in edges if e.get("edge_type") == "hierarchy"]
        o_edges = [e for e in edges if e.get("edge_type") != "hierarchy"]

        lines.append(f"\n### 连线关系 ({len(edges)} 条)\n")
        if h_edges:
            lines.append("**层级：**")
            for e in h_edges:
                src = self._find_title_by_id(nodes, e["source"])
                tgt = self._find_title_by_id(nodes, e["target"])
                lines.append(f"- {src} → {tgt}")
        if o_edges:
            lines.append("\n**协作/咨询/上报：**")
            for e in o_edges:
                src = self._find_title_by_id(nodes, e["source"])
                tgt = self._find_title_by_id(nodes, e["target"])
                etype = e.get("edge_type", "collaborate")
                label = f" \"{e['label']}\"" if e.get("label") else ""
                bidir = "↔" if e.get("bidirectional", True) else "→"
                lines.append(f"- {src} {bidir} {tgt} ({etype}{label})")

        lines.append("\n---\n确认无误后请调用 create 正式创建。")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def _create(self, params: dict[str, Any]) -> str:
        name = params.get("name", "")
        if not name:
            return "❌ create 需要提供 name（组织名称）"

        nodes_raw = params.get("nodes", [])
        if not nodes_raw:
            return "❌ create 需要提供 nodes（节点列表）"

        nodes, edges, errors = self._build_org_structure(params)
        if errors:
            return "⚠️ 结构有问题，请修正后重试：\n" + "\n".join(f"- {e}" for e in errors)

        manager = self._get_org_manager()
        if manager is None:
            return "❌ 组织管理器未初始化，请确认服务已启动"

        org_data = {
            "name": name,
            "description": params.get("description", ""),
            "core_business": params.get("core_business", ""),
            "nodes": nodes,
            "edges": edges,
        }

        try:
            org = manager.create(org_data)
            edge_summary = self._format_edge_summary(org.edges)
            return (
                f"✅ 组织「{org.name}」创建成功！\n"
                f"- ID: {org.id}\n"
                f"- 节点数: {len(org.nodes)}\n"
                f"- 连线: {edge_summary}\n"
                f"- 状态: dormant（需在前端启动）\n\n"
                f"用户可在组织编排页面查看和微调架构。"
            )
        except Exception as e:
            logger.error(f"[OrgSetup] Failed to create org: {e}", exc_info=True)
            return f"❌ 创建失败: {e}"

    # ------------------------------------------------------------------
    # create_from_template
    # ------------------------------------------------------------------

    async def _create_from_template(self, params: dict[str, Any]) -> str:
        template_id = params.get("template_id", "")
        if not template_id:
            return "❌ create_from_template 需要提供 template_id"

        manager = self._get_org_manager()
        if manager is None:
            return "❌ 组织管理器未初始化，请确认服务已启动"

        overrides = params.get("overrides") or {}

        try:
            org = manager.create_from_template(template_id, overrides)
            return (
                f"✅ 从模板「{template_id}」创建组织成功！\n"
                f"- 名称: {org.name}\n"
                f"- ID: {org.id}\n"
                f"- 节点数: {len(org.nodes)}\n"
                f"- 状态: dormant（需在前端启动）"
            )
        except FileNotFoundError:
            return f"❌ 模板 '{template_id}' 不存在。请先调用 get_resources 查看可用模板。"
        except Exception as e:
            logger.error(f"[OrgSetup] Failed to create from template: {e}", exc_info=True)
            return f"❌ 创建失败: {e}"

    # ------------------------------------------------------------------
    # update_org — incremental update preserving node IDs
    # ------------------------------------------------------------------

    async def _update_org(self, params: dict[str, Any]) -> str:
        org_id = params.get("org_id", "")
        if not org_id:
            return "❌ update_org 需要提供 org_id"

        manager = self._get_org_manager()
        if manager is None:
            return "❌ 组织管理器未初始化"

        org = manager.get(org_id)
        if org is None:
            return f"❌ 组织 '{org_id}' 不存在。请先调用 list_orgs 查看现有组织。"

        changes: list[str] = []

        # --- 1. Top-level field updates ---
        update_fields = params.get("update_fields") or {}
        safe_fields = {
            k: v for k, v in update_fields.items()
            if k not in ("id", "created_at", "nodes", "edges", "status")
        }

        # --- 2. Remove nodes ---
        remove_ids: set[str] = set()
        for ref in params.get("remove_nodes", []):
            matched = self._resolve_node(org.nodes, ref)
            if matched:
                remove_ids.add(matched.id)
                changes.append(f"删除节点「{matched.role_title}」({matched.id})")
            else:
                changes.append(f"⚠️ 未找到要删除的节点: {ref}")

        nodes_dict: dict[str, dict] = {}
        for n in org.nodes:
            if n.id not in remove_ids:
                nodes_dict[n.id] = n.to_dict()

        # Clean edges referencing removed nodes
        edges_list = [
            e.to_dict() for e in org.edges
            if e.source not in remove_ids and e.target not in remove_ids
        ]

        # --- 3. Update / add nodes ---
        from ...orgs.tool_categories import get_preset_for_role, get_avatar_for_role

        title_to_id: dict[str, str] = {
            nd["role_title"]: nid for nid, nd in nodes_dict.items()
        }
        new_edges: list[dict] = []

        for upd in params.get("update_nodes", []):
            node_id = upd.get("node_id", "")
            role_title = upd.get("role_title", "").strip()

            existing = None
            if node_id and node_id in nodes_dict:
                existing = nodes_dict[node_id]
            elif role_title:
                for nid, nd in nodes_dict.items():
                    if nd["role_title"] == role_title:
                        existing = nd
                        node_id = nid
                        break

            if existing is not None:
                # Merge update into existing node
                updated_fields = []
                for field in (
                    "role_title", "role_goal", "department", "level",
                    "agent_profile_id", "external_tools", "custom_prompt",
                ):
                    if field in upd and upd[field] is not None:
                        old_val = existing.get(field)
                        new_val = upd[field]
                        if old_val != new_val:
                            existing[field] = new_val
                            updated_fields.append(field)

                if "agent_profile_id" in upd and upd["agent_profile_id"]:
                    existing["agent_source"] = f"ref:{upd['agent_profile_id']}"
                    existing["agent_profile_id"] = upd["agent_profile_id"]

                if updated_fields:
                    changes.append(
                        f"修改节点「{existing['role_title']}」: "
                        f"{', '.join(updated_fields)}"
                    )

                # Handle parent change → new edge
                parent_title = upd.get("parent_role_title", "").strip()
                if parent_title:
                    parent_id = title_to_id.get(parent_title)
                    if parent_id:
                        # Remove old hierarchy edges targeting this node
                        edges_list = [
                            e for e in edges_list
                            if not (e["target"] == node_id and e.get("edge_type") == "hierarchy")
                        ]
                        new_edges.append({
                            "id": f"edge_{uuid.uuid4().hex[:12]}",
                            "source": parent_id,
                            "target": node_id,
                            "edge_type": "hierarchy",
                            "bidirectional": True,
                        })
                        changes.append(
                            f"更新层级：{existing['role_title']} 的上级改为 {parent_title}"
                        )
            else:
                # New node
                new_id = f"node_{uuid.uuid4().hex[:12]}"
                agent_profile_id = upd.get("agent_profile_id")
                agent_source = "local"
                if agent_profile_id:
                    agent_source = f"ref:{agent_profile_id}"

                ext_tools = upd.get("external_tools")
                if not ext_tools and role_title:
                    ext_tools = get_preset_for_role(role_title)

                avatar = get_avatar_for_role(role_title) if role_title else "ceo"

                new_node = {
                    "id": new_id,
                    "role_title": role_title,
                    "role_goal": upd.get("role_goal", ""),
                    "department": upd.get("department", ""),
                    "level": upd.get("level", 1),
                    "agent_profile_id": agent_profile_id,
                    "agent_source": agent_source,
                    "external_tools": ext_tools or [],
                    "custom_prompt": upd.get("custom_prompt", ""),
                    "avatar": avatar,
                    "position": {"x": 0, "y": 0},
                }
                nodes_dict[new_id] = new_node
                title_to_id[role_title] = new_id

                changes.append(f"新增节点「{role_title}」(Agent: {agent_profile_id or 'default'})")

                # Create hierarchy edge for new node
                parent_title = upd.get("parent_role_title", "").strip()
                if parent_title:
                    parent_id = title_to_id.get(parent_title)
                    if parent_id:
                        new_edges.append({
                            "id": f"edge_{uuid.uuid4().hex[:12]}",
                            "source": parent_id,
                            "target": new_id,
                            "edge_type": "hierarchy",
                            "bidirectional": True,
                        })

        edges_list.extend(new_edges)

        # --- 4. Remove edges ---
        for edge_id_ref in params.get("remove_edges", []):
            found = False
            for e in edges_list:
                if e.get("id") == edge_id_ref:
                    if e.get("edge_type") == "hierarchy":
                        changes.append(
                            f"⚠️ 不能通过 remove_edges 删除层级连线 {edge_id_ref}，"
                            f"请通过修改节点的 parent_role_title 调整层级"
                        )
                    else:
                        src_title = self._find_title_by_id(
                            list(nodes_dict.values()), e["source"]
                        )
                        tgt_title = self._find_title_by_id(
                            list(nodes_dict.values()), e["target"]
                        )
                        edges_list = [
                            ex for ex in edges_list if ex.get("id") != edge_id_ref
                        ]
                        changes.append(
                            f"删除连线 {src_title} → {tgt_title} ({e.get('edge_type')})"
                        )
                    found = True
                    break
            if not found:
                changes.append(f"⚠️ 未找到要删除的连线: {edge_id_ref}")

        # --- 5. Add edges ---
        allowed_edge_types = ("collaborate", "escalate", "consult")
        for er in params.get("add_edges", []):
            etype = er.get("edge_type", "")
            if etype not in allowed_edge_types:
                changes.append(
                    f"⚠️ add_edges 不支持 edge_type='{etype}'，"
                    f"层级关系请用 parent_role_title"
                )
                continue

            src_ref = er.get("source", "").strip()
            tgt_ref = er.get("target", "").strip()
            src_id = title_to_id.get(src_ref, src_ref)
            tgt_id = title_to_id.get(tgt_ref, tgt_ref)

            if src_id not in nodes_dict:
                changes.append(f"⚠️ add_edges 中 source='{src_ref}' 未找到对应节点")
                continue
            if tgt_id not in nodes_dict:
                changes.append(f"⚠️ add_edges 中 target='{tgt_ref}' 未找到对应节点")
                continue
            if src_id == tgt_id:
                changes.append("⚠️ add_edges 中 source 和 target 不能是同一节点")
                continue

            duplicate = any(
                e["source"] == src_id and e["target"] == tgt_id
                and e.get("edge_type") == etype
                for e in edges_list
            )
            if duplicate:
                src_title = nodes_dict[src_id].get("role_title", src_id)
                tgt_title = nodes_dict[tgt_id].get("role_title", tgt_id)
                changes.append(
                    f"⚠️ 连线已存在: {src_title} → {tgt_title} ({etype})，跳过"
                )
                continue

            new_edge = {
                "id": f"edge_{uuid.uuid4().hex[:12]}",
                "source": src_id,
                "target": tgt_id,
                "edge_type": etype,
                "label": er.get("label", ""),
                "bidirectional": er.get("bidirectional", True),
            }
            edges_list.append(new_edge)

            src_title = nodes_dict[src_id].get("role_title", src_id)
            tgt_title = nodes_dict[tgt_id].get("role_title", tgt_id)
            label_str = f" \"{er.get('label')}\"" if er.get("label") else ""
            changes.append(f"添加连线 {src_title} ↔ {tgt_title} ({etype}{label_str})")

        # --- 6. Recalculate positions ---
        final_nodes = list(nodes_dict.values())
        self._calculate_positions(final_nodes)

        # --- 7. Commit update ---
        update_data: dict[str, Any] = {
            **safe_fields,
            "nodes": final_nodes,
            "edges": edges_list,
        }

        try:
            org = manager.update(org_id, update_data)

            if safe_fields:
                changes.append(f"更新组织字段: {', '.join(safe_fields.keys())}")

            if not changes:
                return "ℹ️ 未检测到任何变更。请提供要修改的内容。"

            summary = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(changes))
            edge_summary = self._format_edge_summary(org.edges)
            return (
                f"✅ 组织「{org.name}」修改成功！\n\n"
                f"变更摘要：\n{summary}\n\n"
                f"当前节点数: {len(org.nodes)} | 连线: {edge_summary}"
            )
        except Exception as e:
            logger.error(f"[OrgSetup] Failed to update org: {e}", exc_info=True)
            return f"❌ 修改失败: {e}"

    # ------------------------------------------------------------------
    # delete_org
    # ------------------------------------------------------------------

    async def _delete_org(self, params: dict[str, Any]) -> str:
        org_id = params.get("org_id", "")
        if not org_id:
            return "❌ delete_org 需要提供 org_id"

        from ...orgs.runtime import get_runtime
        rt = get_runtime()
        if rt is not None:
            try:
                await rt.delete_org(org_id)
                return f"✅ 组织({org_id}) 已永久删除。"
            except ValueError:
                return f"❌ 组织 '{org_id}' 不存在"
            except Exception as e:
                logger.error(f"[OrgSetup] Failed to delete org: {e}", exc_info=True)
                return f"❌ 删除失败: {e}"

        manager = self._get_org_manager()
        if manager is None:
            return "❌ 组织管理器未初始化"

        org = manager.get(org_id)
        if org is None:
            return f"❌ 组织 '{org_id}' 不存在"

        org_name = org.name
        try:
            manager.delete(org_id)
            return f"✅ 组织「{org_name}」({org_id}) 已永久删除。"
        except Exception as e:
            logger.error(f"[OrgSetup] Failed to delete org: {e}", exc_info=True)
            return f"❌ 删除失败: {e}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_org_manager(self):
        """Get OrgManager from the running app or create one."""
        from ...config import settings
        try:
            from ...orgs.manager import OrgManager
            return OrgManager(settings.data_dir)
        except Exception as e:
            logger.error(f"[OrgSetup] Cannot get OrgManager: {e}")
            return None

    @staticmethod
    def _resolve_node(nodes, ref: str):
        """Find a node by ID or role_title."""
        for n in nodes:
            if n.id == ref or n.role_title == ref:
                return n
        return None

    def _build_org_structure(
        self, params: dict[str, Any]
    ) -> tuple[list[dict], list[dict], list[str]]:
        """Build nodes and edges from params, auto-generating IDs and layout.

        Returns (nodes, edges, errors).
        """
        from ...orgs.tool_categories import get_preset_for_role, get_avatar_for_role

        nodes_raw = params.get("nodes", [])
        errors: list[str] = []
        nodes: list[dict] = []
        title_to_id: dict[str, str] = {}

        for i, nr in enumerate(nodes_raw):
            title = nr.get("role_title", "").strip()
            if not title:
                errors.append(f"节点 #{i + 1} 缺少 role_title")
                continue

            node_id = f"node_{uuid.uuid4().hex[:12]}"
            title_to_id[title] = node_id

            level = nr.get("level", 0)
            agent_profile_id = nr.get("agent_profile_id")
            agent_source = "local"
            if agent_profile_id:
                agent_source = f"ref:{agent_profile_id}"

            ext_tools = nr.get("external_tools")
            if not ext_tools:
                ext_tools = get_preset_for_role(title)

            avatar = get_avatar_for_role(title)

            node = {
                "id": node_id,
                "role_title": title,
                "role_goal": nr.get("role_goal", ""),
                "department": nr.get("department", ""),
                "level": level,
                "agent_profile_id": agent_profile_id,
                "agent_source": agent_source,
                "external_tools": ext_tools,
                "custom_prompt": nr.get("custom_prompt", ""),
                "avatar": avatar,
                "position": {"x": 0, "y": 0},
            }
            nodes.append(node)

        if errors:
            return nodes, [], errors

        self._calculate_positions(nodes)

        edges: list[dict] = []
        for nr, node in zip(nodes_raw, nodes):
            parent_title = nr.get("parent_role_title", "").strip()
            if not parent_title:
                continue
            parent_id = title_to_id.get(parent_title)
            if parent_id is None:
                errors.append(
                    f"节点「{node['role_title']}」的上级「{parent_title}」未找到"
                )
                continue
            edge_id = f"edge_{uuid.uuid4().hex[:12]}"
            edges.append({
                "id": edge_id,
                "source": parent_id,
                "target": node["id"],
                "edge_type": "hierarchy",
                "bidirectional": True,
            })

        allowed_edge_types = ("collaborate", "escalate", "consult")
        for er in params.get("edges", []):
            etype = er.get("edge_type", "")
            if etype not in allowed_edge_types:
                errors.append(
                    f"edges 中不支持 edge_type='{etype}'，"
                    f"层级关系请用 parent_role_title，"
                    f"此处仅支持: {', '.join(allowed_edge_types)}"
                )
                continue
            src_ref = er.get("source", "").strip()
            tgt_ref = er.get("target", "").strip()
            src_id = title_to_id.get(src_ref)
            tgt_id = title_to_id.get(tgt_ref)
            if not src_id:
                errors.append(f"edges 中 source='{src_ref}' 未找到对应节点")
                continue
            if not tgt_id:
                errors.append(f"edges 中 target='{tgt_ref}' 未找到对应节点")
                continue
            if src_id == tgt_id:
                errors.append(f"edges 中 source 和 target 不能是同一节点: '{src_ref}'")
                continue
            edges.append({
                "id": f"edge_{uuid.uuid4().hex[:12]}",
                "source": src_id,
                "target": tgt_id,
                "edge_type": etype,
                "label": er.get("label", ""),
                "bidirectional": er.get("bidirectional", True),
            })

        root_nodes = [n for n in nodes if n["level"] == 0]
        if not root_nodes:
            errors.append("至少需要一个 level=0 的根节点")

        return nodes, edges, errors

    def _calculate_positions(self, nodes: list[dict]) -> None:
        """Assign canvas positions based on level (tree layout)."""
        by_level: dict[int, list[dict]] = {}
        for n in nodes:
            level = n.get("level", 0)
            by_level.setdefault(level, []).append(n)

        y_spacing = 180
        x_spacing = 250

        for level, level_nodes in sorted(by_level.items()):
            count = len(level_nodes)
            total_width = (count - 1) * x_spacing
            start_x = 400 - total_width // 2

            for i, node in enumerate(level_nodes):
                node["position"] = {
                    "x": start_x + i * x_spacing,
                    "y": level * y_spacing,
                }

    def _get_agent_label(self, agent_id: str | None) -> str:
        """Get human-readable label for an agent ID."""
        if not agent_id:
            return "default"
        try:
            from ...agents.presets import SYSTEM_PRESETS
            for p in SYSTEM_PRESETS:
                if p.id == agent_id:
                    return f"{p.name} ({p.id})"
        except Exception:
            pass
        try:
            from ...agents.profile import get_profile_store
            profile = get_profile_store().get(agent_id)
            if profile:
                return f"{profile.name} ({agent_id})"
        except Exception:
            pass
        return agent_id

    def _find_title_by_id(self, nodes: list[dict], node_id: str) -> str:
        for n in nodes:
            if n["id"] == node_id:
                return n["role_title"]
        return node_id

    @staticmethod
    def _format_edge_summary(edges) -> str:
        """Format edge count by type for display."""
        counts: dict[str, int] = {}
        for e in edges:
            if hasattr(e, "edge_type"):
                etype = e.edge_type.value if hasattr(e.edge_type, "value") else e.edge_type
            else:
                etype = e.get("edge_type", "hierarchy")
            counts[etype] = counts.get(etype, 0) + 1

        total = sum(counts.values())
        if total == 0:
            return "0 条"
        parts = []
        type_labels = {
            "hierarchy": "层级",
            "collaborate": "协作",
            "escalate": "上报",
            "consult": "咨询",
        }
        for t, label in type_labels.items():
            if t in counts:
                parts.append(f"{label} {counts[t]}")
        return f"{total} 条（{' / '.join(parts)}）"

    @staticmethod
    def _find_title_by_node_id(nodes, node_id: str) -> str:
        """Find role_title by node_id from OrgNode objects."""
        for n in nodes:
            nid = n.id if hasattr(n, "id") else n.get("id", "")
            title = n.role_title if hasattr(n, "role_title") else n.get("role_title", "")
            if nid == node_id:
                return title
        return node_id


def create_handler(agent: Agent):
    """Factory function following the project convention."""
    handler = OrgSetupHandler(agent)
    return handler.handle
