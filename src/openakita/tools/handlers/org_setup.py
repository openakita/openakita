"""
Organization setup handler — create and manage organizations through natural language.

Always registered (multi-agent mode is always on).
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
                    "skills_summary": p.skills[:5] if p.skills else ["all (universal)"],
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
                    "skills_summary": p.skills[:5] if p.skills else ["all (universal)"],
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
            result["tool_categories"] = dict(TOOL_CATEGORIES.items())
        except Exception:
            result["tool_categories"] = {}

        result["usage_hint"] = (
            "Use the information above to design an organization structure for the user.\n"
            "To assign a role to each node, use one of these approaches (in order of preference):\n"
            "1. Pick a suitable agent_profile_id from agents or custom_agents\n"
            "2. Fill in custom_prompt directly to create a brand-new role (no agent_profile_id needed)\n"
            "   — for cases where no existing Agent is a good match\n"
            "3. Set both agent_profile_id and custom_prompt to use a preset Agent "
            "as the base and append custom instructions\n"
            "Configure appropriate tool categories (external_tools). Ask the user if information is insufficient."
        )

        return json.dumps(result, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # list_orgs
    # ------------------------------------------------------------------

    def _list_orgs(self) -> str:
        manager = self._get_org_manager()
        if manager is None:
            return "❌ Organization manager is not initialized"

        try:
            orgs = manager.list_orgs(include_archived=True)
        except Exception as e:
            logger.error(f"[OrgSetup] list_orgs failed: {e}", exc_info=True)
            return f"❌ Failed to list organizations: {e}"

        if not orgs:
            return "There are no organizations yet. Use create or create_from_template to create one."

        lines = [f"Existing organizations: {len(orgs)} total\n"]
        for o in orgs:
            status = o.get("status", "unknown")
            lines.append(
                f"- **{o.get('name', '')}** (ID: {o.get('id', '')})\n"
                f"  Status: {status} | Nodes: {o.get('node_count', 0)} | "
                f"Edges: {o.get('edge_count', 0)}"
            )
        lines.append(
            "\nUse get_org to view an organization's full structure, "
            "update_org to modify, and delete_org to delete."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # get_org
    # ------------------------------------------------------------------

    def _get_org(self, params: dict[str, Any]) -> str:
        org_id = params.get("org_id", "")
        if not org_id:
            return "❌ get_org requires org_id"

        manager = self._get_org_manager()
        if manager is None:
            return "❌ Organization manager is not initialized"

        org = manager.get(org_id)
        if org is None:
            return f"❌ Organization '{org_id}' does not exist"

        lines = [
            f"## Organization: {org.name}",
            f"- ID: {org.id}",
            f"- Status: {org.status.value if hasattr(org.status, 'value') else org.status}",
            f"- Description: {org.description or '(none)'}",
            f"- Core business: {org.core_business or '(none)'}",
            "",
            f"### Nodes ({len(org.nodes)})\n",
        ]

        for n in sorted(org.nodes, key=lambda x: (x.level, x.department)):
            indent = "  " * n.level
            agent_label = self._get_agent_label(n.agent_profile_id)
            dept = f" [{n.department}]" if n.department else ""
            tools = n.external_tools or []
            tools_str = f" | Tools: {', '.join(tools)}" if tools else ""

            lines.append(
                f"{indent}- **{n.role_title}**{dept}\n"
                f"{indent}  ID: `{n.id}` | Agent: {agent_label}{tools_str}"
            )
            if n.role_goal:
                lines.append(f"{indent}  Goal: {n.role_goal}")

        hierarchy_edges = []
        other_edges = []
        for e in org.edges:
            etype = e.edge_type.value if hasattr(e.edge_type, "value") else e.edge_type
            if etype == "hierarchy":
                hierarchy_edges.append(e)
            else:
                other_edges.append(e)

        lines.append(f"\n### Edges ({len(org.edges)})\n")

        if hierarchy_edges:
            lines.append("**Hierarchy:**")
            for e in hierarchy_edges:
                src = self._find_title_by_node_id(org.nodes, e.source)
                tgt = self._find_title_by_node_id(org.nodes, e.target)
                lines.append(f"- {src} → {tgt}")

        if other_edges:
            lines.append("\n**Collaborate / consult / escalate:**")
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
            "Use update_org to modify this organization. When editing a node, provide its node_id for exact matching.\n"
            "Use add_edges to add collaboration edges and remove_edges (pass edge ID) to remove them."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # preview
    # ------------------------------------------------------------------

    def _preview(self, params: dict[str, Any]) -> str:
        name = params.get("name", "")
        if not name:
            return "❌ preview requires name (organization name)"

        nodes_raw = params.get("nodes", [])
        if not nodes_raw:
            return "❌ preview requires nodes (node list)"

        nodes, edges, errors = self._build_org_structure(params)
        if errors:
            return "⚠️ Structure validation found issues:\n" + "\n".join(f"- {e}" for e in errors)

        lines = [f"## Organization preview: {name}\n"]
        if params.get("core_business"):
            lines.append(f"Core business: {params['core_business']}\n")

        lines.append(f"Nodes: {len(nodes)}, Edges: {len(edges)}\n")
        lines.append("### Node details\n")

        for n in sorted(nodes, key=lambda x: (x.get("level", 0), x.get("department", ""))):
            indent = "  " * n.get("level", 0)
            agent_id = n.get("agent_profile_id", "default")
            agent_label = self._get_agent_label(agent_id)
            dept = n.get("department", "")
            dept_str = f" [{dept}]" if dept else ""
            tools = n.get("external_tools", [])
            tools_str = f" Tools: {', '.join(tools)}" if tools else ""

            lines.append(
                f"{indent}- **{n['role_title']}**{dept_str} → Agent: {agent_label}"
                f"{tools_str}"
            )

        h_edges = [e for e in edges if e.get("edge_type") == "hierarchy"]
        o_edges = [e for e in edges if e.get("edge_type") != "hierarchy"]

        lines.append(f"\n### Edges ({len(edges)})\n")
        if h_edges:
            lines.append("**Hierarchy:**")
            for e in h_edges:
                src = self._find_title_by_id(nodes, e["source"])
                tgt = self._find_title_by_id(nodes, e["target"])
                lines.append(f"- {src} → {tgt}")
        if o_edges:
            lines.append("\n**Collaborate / consult / escalate:**")
            for e in o_edges:
                src = self._find_title_by_id(nodes, e["source"])
                tgt = self._find_title_by_id(nodes, e["target"])
                etype = e.get("edge_type", "collaborate")
                label = f" \"{e['label']}\"" if e.get("label") else ""
                bidir = "↔" if e.get("bidirectional", True) else "→"
                lines.append(f"- {src} {bidir} {tgt} ({etype}{label})")

        lines.append("\n---\nOnce confirmed, call create to create it.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def _create(self, params: dict[str, Any]) -> str:
        name = params.get("name", "")
        if not name:
            return "❌ create requires name (organization name)"

        nodes_raw = params.get("nodes", [])
        if not nodes_raw:
            return "❌ create requires nodes (node list)"

        nodes, edges, errors = self._build_org_structure(params)
        if errors:
            return "⚠️ Structure has issues; please fix and retry:\n" + "\n".join(f"- {e}" for e in errors)

        manager = self._get_org_manager()
        if manager is None:
            return "❌ Organization manager is not initialized; please ensure the service is running"

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
                f"✅ Organization \"{org.name}\" created successfully!\n"
                f"- ID: {org.id}\n"
                f"- Nodes: {len(org.nodes)}\n"
                f"- Edges: {edge_summary}\n"
                f"- Status: dormant (needs to be started in the frontend)\n\n"
                f"The user can view and fine-tune the structure on the organization editor page."
            )
        except Exception as e:
            logger.error(f"[OrgSetup] Failed to create org: {e}", exc_info=True)
            return f"❌ Creation failed: {e}"

    # ------------------------------------------------------------------
    # create_from_template
    # ------------------------------------------------------------------

    async def _create_from_template(self, params: dict[str, Any]) -> str:
        template_id = params.get("template_id", "")
        if not template_id:
            return "❌ create_from_template requires template_id"

        manager = self._get_org_manager()
        if manager is None:
            return "❌ Organization manager is not initialized; please ensure the service is running"

        overrides = params.get("overrides") or {}

        try:
            org = manager.create_from_template(template_id, overrides)
            return (
                f"✅ Organization created from template \"{template_id}\"!\n"
                f"- Name: {org.name}\n"
                f"- ID: {org.id}\n"
                f"- Nodes: {len(org.nodes)}\n"
                f"- Status: dormant (needs to be started in the frontend)"
            )
        except FileNotFoundError:
            return f"❌ Template '{template_id}' does not exist. Call get_resources first to see available templates."
        except Exception as e:
            logger.error(f"[OrgSetup] Failed to create from template: {e}", exc_info=True)
            return f"❌ Creation failed: {e}"

    # ------------------------------------------------------------------
    # update_org — incremental update preserving node IDs
    # ------------------------------------------------------------------

    async def _update_org(self, params: dict[str, Any]) -> str:
        org_id = params.get("org_id", "")
        if not org_id:
            return "❌ update_org requires org_id"

        manager = self._get_org_manager()
        if manager is None:
            return "❌ Organization manager is not initialized"

        org = manager.get(org_id)
        if org is None:
            return f"❌ Organization '{org_id}' does not exist. Call list_orgs first to see existing organizations."

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
                changes.append(f"Removed node \"{matched.role_title}\" ({matched.id})")
            else:
                changes.append(f"⚠️ Node to remove not found: {ref}")

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
        from ...orgs.tool_categories import get_avatar_for_role, get_preset_for_role

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
                        f"Updated node \"{existing['role_title']}\": "
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
                            f"Updated hierarchy: {existing['role_title']}'s parent changed to {parent_title}"
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

                changes.append(f"Added node \"{role_title}\" (Agent: {agent_profile_id or 'default'})")

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
                            f"⚠️ Cannot remove hierarchy edge {edge_id_ref} via remove_edges; "
                            f"adjust the hierarchy by modifying a node's parent_role_title instead"
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
                            f"Removed edge {src_title} → {tgt_title} ({e.get('edge_type')})"
                        )
                    found = True
                    break
            if not found:
                changes.append(f"⚠️ Edge to remove not found: {edge_id_ref}")

        # --- 5. Add edges ---
        allowed_edge_types = ("collaborate", "escalate", "consult")
        for er in params.get("add_edges", []):
            etype = er.get("edge_type", "")
            if etype not in allowed_edge_types:
                changes.append(
                    f"⚠️ add_edges does not support edge_type='{etype}'; "
                    f"use parent_role_title for hierarchy relationships"
                )
                continue

            src_ref = er.get("source", "").strip()
            tgt_ref = er.get("target", "").strip()
            src_id = title_to_id.get(src_ref, src_ref)
            tgt_id = title_to_id.get(tgt_ref, tgt_ref)

            if src_id not in nodes_dict:
                changes.append(f"⚠️ add_edges: no node found for source='{src_ref}'")
                continue
            if tgt_id not in nodes_dict:
                changes.append(f"⚠️ add_edges: no node found for target='{tgt_ref}'")
                continue
            if src_id == tgt_id:
                changes.append("⚠️ add_edges: source and target cannot be the same node")
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
                    f"⚠️ Edge already exists: {src_title} → {tgt_title} ({etype}); skipping"
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
            changes.append(f"Added edge {src_title} ↔ {tgt_title} ({etype}{label_str})")

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
                changes.append(f"Updated organization fields: {', '.join(safe_fields.keys())}")

            if not changes:
                return "ℹ️ No changes detected. Please provide what you want to modify."

            summary = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(changes))
            edge_summary = self._format_edge_summary(org.edges)
            return (
                f"✅ Organization \"{org.name}\" updated successfully!\n\n"
                f"Change summary:\n{summary}\n\n"
                f"Current nodes: {len(org.nodes)} | Edges: {edge_summary}"
            )
        except Exception as e:
            logger.error(f"[OrgSetup] Failed to update org: {e}", exc_info=True)
            return f"❌ Update failed: {e}"

    # ------------------------------------------------------------------
    # delete_org
    # ------------------------------------------------------------------

    async def _delete_org(self, params: dict[str, Any]) -> str:
        org_id = params.get("org_id", "")
        if not org_id:
            return "❌ delete_org requires org_id"

        from ...orgs.runtime import get_runtime
        rt = get_runtime()
        if rt is not None:
            try:
                await rt.delete_org(org_id)
                return f"✅ Organization ({org_id}) has been permanently deleted."
            except ValueError:
                return f"❌ Organization '{org_id}' does not exist"
            except Exception as e:
                logger.error(f"[OrgSetup] Failed to delete org: {e}", exc_info=True)
                return f"❌ Delete failed: {e}"

        manager = self._get_org_manager()
        if manager is None:
            return "❌ Organization manager is not initialized"

        org = manager.get(org_id)
        if org is None:
            return f"❌ Organization '{org_id}' does not exist"

        org_name = org.name
        try:
            manager.delete(org_id)
            return f"✅ Organization \"{org_name}\" ({org_id}) has been permanently deleted."
        except Exception as e:
            logger.error(f"[OrgSetup] Failed to delete org: {e}", exc_info=True)
            return f"❌ Delete failed: {e}"

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
        from ...orgs.tool_categories import get_avatar_for_role, get_preset_for_role

        nodes_raw = params.get("nodes", [])
        errors: list[str] = []
        nodes: list[dict] = []
        title_to_id: dict[str, str] = {}

        for i, nr in enumerate(nodes_raw):
            title = nr.get("role_title", "").strip()
            if not title:
                errors.append(f"Node #{i + 1} is missing role_title")
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
        for nr, node in zip(nodes_raw, nodes, strict=False):
            parent_title = nr.get("parent_role_title", "").strip()
            if not parent_title:
                continue
            parent_id = title_to_id.get(parent_title)
            if parent_id is None:
                errors.append(
                    f"Parent \"{parent_title}\" of node \"{node['role_title']}\" not found"
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
                    f"edges does not support edge_type='{etype}'; "
                    f"use parent_role_title for hierarchy relationships; "
                    f"only these are supported here: {', '.join(allowed_edge_types)}"
                )
                continue
            src_ref = er.get("source", "").strip()
            tgt_ref = er.get("target", "").strip()
            src_id = title_to_id.get(src_ref)
            tgt_id = title_to_id.get(tgt_ref)
            if not src_id:
                errors.append(f"edges: no node found for source='{src_ref}'")
                continue
            if not tgt_id:
                errors.append(f"edges: no node found for target='{tgt_ref}'")
                continue
            if src_id == tgt_id:
                errors.append(f"edges: source and target cannot be the same node: '{src_ref}'")
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
            errors.append("At least one root node with level=0 is required")

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
            return "0"
        parts = []
        type_labels = {
            "hierarchy": "hierarchy",
            "collaborate": "collaborate",
            "escalate": "escalate",
            "consult": "consult",
        }
        for t, label in type_labels.items():
            if t in counts:
                parts.append(f"{label} {counts[t]}")
        return f"{total} ({' / '.join(parts)})"

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
