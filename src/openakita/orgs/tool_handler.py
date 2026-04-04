"""
OrgToolHandler — 组织工具执行器

处理组织节点 Agent 调用的 org_* 系列工具。
每个 handler 方法接收 tool_name, arguments, context(org_id, node_id) 并返回结果。
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from openakita.memory.types import normalize_tags

from .models import (
    MemoryType,
    MsgType,
    NodeSchedule,
    NodeStatus,
    OrgMessage,
    ScheduleType,
    _now_iso,
)

if TYPE_CHECKING:
    from .runtime import OrgRuntime

logger = logging.getLogger(__name__)


class OrgToolHandler:
    """Dispatch and execute org_* tool calls."""

    def __init__(self, runtime: OrgRuntime) -> None:
        self._runtime = runtime

    _INT_DEFAULTS: dict[str, int] = {
        "priority": 0,
        "bandwidth_limit": 60,
        "limit": 10,
        "max_rounds": 3,
        "interval_s": 60,
        "progress_pct": 0,
    }
    _FLOAT_DEFAULTS: dict[str, float] = {
        "importance": 0.5,
    }

    @staticmethod
    def _coerce_types(args: dict) -> dict:
        """Ensure LLM-provided arguments have correct Python types."""
        for key, default in OrgToolHandler._INT_DEFAULTS.items():
            if key in args:
                try:
                    args[key] = int(args[key])
                except (ValueError, TypeError):
                    args[key] = default
        for key, default in OrgToolHandler._FLOAT_DEFAULTS.items():
            if key in args:
                try:
                    args[key] = float(args[key])
                except (ValueError, TypeError):
                    args[key] = default
        if "tags" in args and isinstance(args["tags"], str):
            import json as _json
            try:
                parsed = _json.loads(args["tags"])
                if isinstance(parsed, list):
                    args["tags"] = parsed
            except Exception:
                args["tags"] = [
                    t.strip()
                    for t in args["tags"].replace("\u3001", ",").split(",")
                    if t.strip()
                ]
        return args

    @staticmethod
    def _effective_max_delegation_depth(org: Any) -> int:
        """Compute effective max delegation depth based on org structure.

        Ensures the limit is at least the org's actual hierarchy depth + a buffer,
        so tasks can always reach the lowest level of the org chart.
        """
        if not org:
            return 10
        org_depth = max((n.level for n in org.nodes), default=0)
        explicit = org.max_delegation_depth
        return max(explicit, org_depth + 3)

    def _resolve_node_refs(self, args: dict, org_id: str) -> None:
        """Resolve node references: LLM may pass role titles or wrong-cased IDs."""
        org = self._runtime.get_org(org_id)
        if not org:
            return
        for key in ("to_node", "node_id", "target_node_id"):
            val = args.get(key, "")
            if not val:
                continue
            if org.get_node(val):
                continue
            val_lower = val.lower().replace(" ", "_").replace("-", "_")
            for n in org.nodes:
                if n.id == val_lower or n.role_title == val or n.role_title.lower() == val.lower():
                    args[key] = n.id
                    break

    @staticmethod
    def _resolve_aliases(args: dict) -> dict:
        """Resolve common LLM parameter name variations to canonical names."""
        if "to_node" not in args:
            args["to_node"] = (
                args.pop("target_node", None)
                or args.pop("target", None)
                or args.pop("to", None)
                or ""
            )
        if "task" not in args:
            alias_task = (
                args.pop("task_description", None)
                or args.pop("task_content", None)
                or args.pop("description", None)
            )
            if alias_task:
                args["task"] = alias_task
        if "content" not in args:
            args["content"] = (
                args.pop("message", None)
                or args.pop("text", None)
                or args.pop("body", None)
                or ""
            )
        if "need" not in args and "query" in args and "filename" not in args:
            args["need"] = args.get("query", "")
        if "query" not in args and "need" in args and "filename" not in args:
            args["query"] = args.get("need", "")
        if "node_id" not in args:
            v = args.pop("target_id", None)
            if v:
                args["node_id"] = v
        if "reply_to" not in args:
            v = args.pop("reply_to_id", None) or args.pop("message_id", None)
            if v:
                args["reply_to"] = v
        if "filename" not in args:
            v = args.pop("file_name", None) or args.pop("file", None)
            if v:
                args["filename"] = v
        return args

    def _link_project_task(
        self, org_id: str, chain_id: str, *,
        title: str = "",
        assignee: str | None = None,
        delegated_by: str | None = None,
        status: str | None = None,
        parent_task_id: str | None = None,
        depth: int = 0,
    ) -> None:
        """Auto-link a task chain to an active project's ProjectTask.

        Priority: chain_id match -> assignee match (project with assignee's tasks)
        -> first active project fallback.
        """
        try:
            from openakita.orgs.project_store import ProjectStore
            from openakita.orgs.models import ProjectTask, TaskStatus

            mgr = self._runtime._manager
            org_dir = mgr._org_dir(org_id)
            store = ProjectStore(org_dir)

            # 1. chain_id match
            existing = store.find_task_by_chain(chain_id)
            if existing:
                updates: dict[str, Any] = {}
                if status:
                    updates["status"] = TaskStatus(status)
                    if status == "in_progress" and not existing.started_at:
                        updates["started_at"] = _now_iso()
                    elif status == "delivered":
                        updates["delivered_at"] = _now_iso()
                    elif status == "accepted":
                        updates["completed_at"] = _now_iso()
                if updates:
                    store.update_task(existing.project_id, existing.id, updates)
                return
            if not title:
                return

            active_projects = [
                p for p in store.list_projects()
                if p.status.value == "active" and p.org_id == org_id
            ]
            if not active_projects:
                from openakita.orgs.models import OrgProject, ProjectStatus
                default_proj = OrgProject(
                    org_id=org_id,
                    name="任务追踪",
                    status=ProjectStatus.ACTIVE,
                )
                store.create_project(default_proj)
                active_projects = [default_proj]

            # 2. assignee match: prefer project that has tasks for this assignee
            proj = None
            if assignee:
                for p in active_projects:
                    for t in p.tasks:
                        if t.assignee_node_id == assignee:
                            proj = p
                            break
                    if proj:
                        break

            # 3. first project fallback
            if not proj:
                proj = active_projects[0]

            task = ProjectTask(
                project_id=proj.id,
                title=title[:120],
                status=TaskStatus.IN_PROGRESS,
                assignee_node_id=assignee,
                delegated_by=delegated_by,
                chain_id=chain_id,
                parent_task_id=parent_task_id,
                depth=depth,
                started_at=_now_iso(),
            )
            store.add_task(proj.id, task)
        except Exception as exc:
            logger.debug("project-task auto-link failed: %s", exc)

    def _append_execution_log(
        self, org_id: str, chain_id: str, entry: str, node_id: str
    ) -> None:
        """Append an entry to a ProjectTask's execution_log."""
        try:
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            org_dir = mgr._org_dir(org_id)
            store = ProjectStore(org_dir)
            existing = store.find_task_by_chain(chain_id)
            if not existing:
                return
            log_entry = {"at": _now_iso(), "by": node_id, "entry": entry[:500]}
            new_log = list(existing.execution_log or []) + [log_entry]
            store.update_task(existing.project_id, existing.id, {"execution_log": new_log})
        except Exception as exc:
            logger.debug("execution_log append failed: %s", exc)

    def _recalc_parent_progress(self, org_id: str, chain_id: str) -> None:
        """Recursively recalc parent task progress after child status change."""
        try:
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            org_dir = mgr._org_dir(org_id)
            store = ProjectStore(org_dir)
            task = store.find_task_by_chain(chain_id)
            if task and task.parent_task_id:
                store.recalc_progress(task.parent_task_id)
        except Exception as exc:
            logger.debug("recalc_parent_progress failed: %s", exc)

    def _bridge_plan_to_task(
        self, org_id: str, node_id: str,
        tool_name: str, tool_input: dict, result: str,
        chain_id: str | None = None,
    ) -> None:
        """Intercept plan tool results and sync to ProjectTask (plan_steps, progress_pct, execution_log)."""
        if not chain_id:
            chain_id = getattr(self._runtime, "get_current_chain_id", lambda o, n: None)(
                org_id, node_id
            )
        if not chain_id:
            return
        try:
            from openakita.orgs.project_store import ProjectStore
            from openakita.orgs.models import TaskStatus

            mgr = self._runtime._manager
            org_dir = mgr._org_dir(org_id)
            store = ProjectStore(org_dir)
            existing = store.find_task_by_chain(chain_id)
            if not existing:
                return

            if tool_name == "create_todo":
                steps = tool_input.get("steps", [])
                if isinstance(steps, str):
                    try:
                        steps = json.loads(steps)
                    except (json.JSONDecodeError, TypeError):
                        steps = []
                plan_steps = []
                for s in steps:
                    plan_steps.append({
                        "id": s.get("id", f"step_{len(plan_steps)}"),
                        "description": s.get("description", ""),
                        "status": s.get("status", "pending"),
                        "result": s.get("result", ""),
                    })
                store.update_task(existing.project_id, existing.id, {"plan_steps": plan_steps})
                self._append_execution_log(
                    org_id, chain_id,
                    f"计划创建: {tool_input.get('task_summary', '')[:80]}",
                    node_id,
                )
            elif tool_name == "update_todo_step":
                step_id = tool_input.get("step_id", "")
                status = tool_input.get("status", "")
                result_text = tool_input.get("result", "")
                plan_steps = list(existing.plan_steps or [])
                for s in plan_steps:
                    if s.get("id") == step_id:
                        s["status"] = status
                        s["result"] = result_text
                        break
                store.update_task(existing.project_id, existing.id, {"plan_steps": plan_steps})
                completed = sum(1 for s in plan_steps if s.get("status") == "completed")
                progress_pct = int(100 * completed / len(plan_steps)) if plan_steps else 0
                store.update_task(existing.project_id, existing.id, {"progress_pct": progress_pct})
                self._append_execution_log(
                    org_id, chain_id,
                    f"步骤 {step_id}: {status} - {result_text[:80]}",
                    node_id,
                )
            elif tool_name == "complete_todo":
                summary = tool_input.get("summary", "")
                store.update_task(existing.project_id, existing.id, {
                    "status": TaskStatus.ACCEPTED,
                    "progress_pct": 100,
                    "completed_at": _now_iso(),
                })
                self._append_execution_log(
                    org_id, chain_id,
                    f"计划完成: {summary[:80]}",
                    node_id,
                )
        except Exception as exc:
            logger.debug("plan bridge failed: %s", exc)

    async def handle(
        self, tool_name: str, arguments: dict, org_id: str, node_id: str
    ) -> str:
        """Execute an org tool and return the result as a string."""
        handler = getattr(self, f"_handle_{tool_name}", None)
        if handler is None:
            return f"Unknown org tool: {tool_name}"

        arguments = self._resolve_aliases(arguments)
        arguments = self._coerce_types(arguments)
        self._resolve_node_refs(arguments, org_id)

        try:
            result = await handler(arguments, org_id, node_id)
            if isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False, indent=2)
            return str(result)
        except Exception as e:
            logger.error(f"[OrgToolHandler] Error in {tool_name}: {e}")
            return f"Tool error: {e}"

    # ------------------------------------------------------------------
    # Communication tools
    # ------------------------------------------------------------------

    async def _handle_org_send_message(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        metadata: dict = {}

        raw_type = args.get("msg_type", "question")
        try:
            msg_type = MsgType(raw_type)
        except ValueError:
            msg_type = MsgType.QUESTION
            logger.warning(f"[OrgToolHandler] Invalid msg_type '{raw_type}', falling back to 'question'")

        to_node = args["to_node"]
        org = self._runtime.get_org(org_id)
        if org:
            resolved = org.get_node(to_node)
            if resolved:
                to_node = resolved.id
            else:
                avail = ", ".join(f"{n.id}({n.role_title})" for n in org.nodes)
                return f"节点 '{to_node}' 不存在。可用节点: {avail}"

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=to_node,
            msg_type=msg_type,
            content=args["content"],
            priority=args.get("priority", 0),
            metadata=metadata,
        )
        ok = await messenger.send(msg)
        if ok:
            await self._runtime._broadcast_ws("org:message", {
                "org_id": org_id, "from_node": node_id, "to_node": to_node,
                "msg_type": args.get("msg_type", "question"),
                "content": args["content"][:120],
            })
        return f"消息已发送给 {to_node}" if ok else "发送失败"

    async def _handle_org_reply_message(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"
        original = messenger._pending_messages.get(args["reply_to"])
        to_node = original.from_node if original else ""
        if not to_node:
            return f"原始消息 {args['reply_to']} 未找到，无法确定回复目标"
        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=to_node,
            msg_type=MsgType.ANSWER,
            content=args["content"],
            reply_to=args["reply_to"],
        )
        await messenger.send(msg)
        return "已回复"

    async def _handle_org_delegate_task(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        org = self._runtime.get_org(org_id)

        chain_id = (
            args.get("task_chain_id")
            or self._runtime.get_current_chain_id(org_id, node_id)
            or _now_iso() + ":" + node_id[:8]
        )
        chain_depth = self._runtime._chain_delegation_depth.get(chain_id, 0)
        max_depth = self._effective_max_delegation_depth(org)
        if chain_depth + 1 > max_depth:
            return (
                f"此任务链的委派层级已达上限（{max_depth}层），无法继续向下委派。"
                f"请自行完成此项工作，或用 org_submit_deliverable 提交当前成果给上级重新安排。"
            )

        metadata = {}
        if args.get("deadline"):
            metadata["task_deadline"] = args["deadline"]

        metadata["_delegation_depth"] = chain_depth + 1
        metadata["task_chain_id"] = chain_id

        to_node = args["to_node"]

        existing_affinity = messenger.get_task_affinity(chain_id)
        if existing_affinity:
            if org:
                affinity_node = org.get_node(existing_affinity)
                if affinity_node and affinity_node.status not in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
                    to_node = existing_affinity

        if org:
            resolved = org.get_node(to_node)
            if resolved:
                to_node = resolved.id
            else:
                avail = ", ".join(f"{n.id}({n.role_title})" for n in org.nodes)
                return f"节点 '{to_node}' 不存在。可用节点: {avail}"

            # Validate hierarchy: only direct children can receive delegated tasks
            children = org.get_children(node_id)
            child_ids = {c.id for c in children}
            if to_node not in child_ids:
                if to_node == node_id:
                    hint = "不能给自己委派任务。"
                else:
                    target_node = org.get_node(to_node)
                    target_label = f"'{target_node.role_title}'" if target_node else f"'{to_node}'"
                    hint = f"{target_label} 不是你的直属下级，无法委派。"
                if children:
                    child_list = ", ".join(f"{c.role_title}(`{c.id}`)" for c in children)
                    return f"{hint}你的直属下级: {child_list}"
                return f"{hint}你没有直属下级，无法使用 org_delegate_task。请自行完成任务，或用 org_send_message 与同事协作。"

        await messenger.send_task(
            from_node=node_id,
            to_node=to_node,
            task_content=args["task"],
            priority=args.get("priority", 0),
            metadata=metadata,
        )

        messenger.bind_task_affinity(chain_id, to_node)
        self._runtime._chain_delegation_depth[chain_id] = chain_depth + 1

        self._runtime.get_event_store(org_id).emit(
            "task_assigned", node_id,
            {"to": to_node, "task": args["task"][:100], "chain_id": chain_id},
        )
        await self._runtime._broadcast_ws("org:task_delegated", {
            "org_id": org_id, "from_node": node_id, "to_node": to_node,
            "task": args["task"][:120], "chain_id": chain_id,
        })

        parent_task_id = None
        depth = 0
        parent_chain = getattr(self._runtime, "get_current_chain_id", lambda o, n: None)(
            org_id, node_id
        )
        if parent_chain:
            from openakita.orgs.project_store import ProjectStore
            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            parent_task = store.find_task_by_chain(parent_chain)
            if parent_task:
                parent_task_id = parent_task.id
                depth = (parent_task.depth or 0) + 1

        self._link_project_task(
            org_id, chain_id,
            title=args["task"][:120],
            assignee=to_node,
            delegated_by=node_id,
            status="in_progress",
            parent_task_id=parent_task_id,
            depth=depth,
        )
        self._append_execution_log(
            org_id, chain_id,
            f"委派给 {to_node}: {args['task'][:80]}",
            node_id,
        )
        return (
            f"任务已分配给 {to_node}（chain: {chain_id[:12]}）: {args['task'][:50]}\n"
            f"⚠️ 注意：任务已异步下发，下级尚未完成。"
            f"请勿立即汇报「已完成」，应使用 org_list_delegated_tasks 跟踪进度，"
            f"或等待下级通过 org_submit_deliverable 提交结果后再做最终汇报。"
        )

    async def _handle_org_escalate(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        result = await messenger.escalate(
            node_id, args["content"], priority=args.get("priority", 1),
            metadata={},
        )
        if result:
            await self._runtime._broadcast_ws("org:escalation", {
                "org_id": org_id, "from_node": node_id,
                "to_node": result.to_node if hasattr(result, "to_node") else "",
                "content": args["content"][:120],
            })
            return "已上报给上级"
        return "无法上报（没有上级节点）"

    async def _handle_org_broadcast(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"
        scope = args.get("scope", "department")
        msg_type = MsgType.DEPT_BROADCAST if scope == "department" else MsgType.BROADCAST
        org = self._runtime.get_org(org_id)
        node = org.get_node(node_id) if org else None
        if msg_type == MsgType.BROADCAST and node and node.level > 0:
            return "只有顶层节点可以全组织广播，你可以使用部门广播"

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            msg_type=msg_type,
            content=args["content"],
            metadata={},
        )
        await messenger.send(msg)
        scope_label = "部门" if scope == "department" else "全组织"
        await self._runtime._broadcast_ws("org:broadcast", {
            "org_id": org_id, "from_node": node_id, "scope": scope,
            "content": args["content"][:120],
        })
        self._runtime.get_event_store(org_id).emit(
            "broadcast", node_id,
            {"scope": scope, "content": args["content"][:200]},
        )
        return f"已{scope_label}广播"

    # ------------------------------------------------------------------
    # Organization awareness tools
    # ------------------------------------------------------------------

    async def _handle_org_get_org_chart(
        self, args: dict, org_id: str, node_id: str
    ) -> dict:
        org = self._runtime.get_org(org_id)
        if not org:
            return {"error": "组织未找到"}
        departments: dict[str, list] = {}
        for n in org.nodes:
            dept = n.department or "未分配"
            departments.setdefault(dept, []).append({
                "id": n.id,
                "title": n.role_title,
                "goal": n.role_goal[:80] if n.role_goal else "",
                "skills": n.skills[:5],
                "status": n.status.value,
                "level": n.level,
            })
        edges = [
            {"from": e.source, "to": e.target, "type": e.edge_type.value}
            for e in org.edges
        ]
        return {"departments": [{"name": k, "members": v} for k, v in departments.items()], "edges": edges}

    async def _handle_org_find_colleague(
        self, args: dict, org_id: str, node_id: str
    ) -> list:
        org = self._runtime.get_org(org_id)
        if not org:
            return []
        need = (args.get("need") or args.get("query") or "").lower()
        if not need:
            return []
        prefer_dept = args.get("prefer_department", "").lower()
        results = []
        for n in org.nodes:
            if n.id == node_id:
                continue
            score = 0.0
            text = f"{n.role_title} {n.role_goal} {' '.join(n.skills)}".lower()
            for word in need.split():
                if word in text:
                    score += 0.3
            if prefer_dept and n.department.lower() == prefer_dept:
                score += 0.2
            if n.status == NodeStatus.IDLE:
                score += 0.1
            if score > 0:
                results.append({
                    "id": n.id,
                    "title": n.role_title,
                    "department": n.department,
                    "relevance": round(min(score, 1.0), 2),
                    "status": n.status.value,
                })
        results.sort(key=lambda x: x["relevance"], reverse=True)
        return results[:5]

    async def _handle_org_get_node_status(
        self, args: dict, org_id: str, node_id: str
    ) -> dict:
        org = self._runtime.get_org(org_id)
        if not org:
            return {"error": "组织未找到"}
        target_id = args.get("node_id") or args.get("target_node") or ""
        target = org.get_node(target_id)
        if not target:
            return {"error": f"节点未找到: {target_id}"}
        messenger = self._runtime.get_messenger(org_id)
        pending = messenger.get_pending_count(target.id) if messenger else 0
        return {
            "id": target.id,
            "title": target.role_title,
            "status": target.status.value,
            "department": target.department,
            "pending_messages": pending,
        }

    async def _handle_org_get_org_status(
        self, args: dict, org_id: str, node_id: str
    ) -> dict:
        org = self._runtime.get_org(org_id)
        if not org:
            return {"error": "组织未找到"}
        node_stats: dict[str, int] = {}
        for n in org.nodes:
            s = n.status.value
            node_stats[s] = node_stats.get(s, 0) + 1
        return {
            "org_name": org.name,
            "status": org.status.value,
            "node_count": len(org.nodes),
            "node_stats": node_stats,
            "total_tasks": org.total_tasks_completed,
            "total_messages": org.total_messages_exchanged,
        }

    # ------------------------------------------------------------------
    # Memory tools
    # ------------------------------------------------------------------

    async def _handle_org_read_blackboard(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        if not bb:
            return "黑板不可用"
        entries = bb.read_org(
            limit=args.get("limit", 10),
            tag=args.get("tag"),
        )
        if not entries:
            return "(黑板暂无内容)"
        lines = []
        for e in entries:
            tags = f" [{', '.join(e.tags)}]" if e.tags else ""
            lines.append(f"[{e.memory_type.value}] {e.content}{tags} (by {e.source_node})")
        return "\n".join(lines)

    async def _handle_org_write_blackboard(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        if not bb:
            return "黑板不可用"
        raw_mt = args.get("memory_type", "fact")
        try:
            mt = MemoryType(raw_mt)
        except ValueError:
            mt = MemoryType.FACT
            logger.warning(f"[OrgToolHandler] Invalid memory_type '{raw_mt}', falling back to 'fact'")
        entry = bb.write_org(
            content=args["content"],
            source_node=node_id,
            memory_type=mt,
            tags=normalize_tags(args.get("tags")),
            importance=args.get("importance", 0.5),
        )
        if entry is None:
            return f"黑板已有相似内容，跳过重复写入: {args['content'][:50]}"
        await self._runtime._broadcast_ws("org:blackboard_update", {
            "org_id": org_id, "scope": "org", "node_id": node_id,
            "memory_type": args.get("memory_type", "fact"),
            "content": args["content"][:120],
        })
        return f"已写入组织黑板: {args['content'][:50]}"

    async def _handle_org_read_dept_memory(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        org = self._runtime.get_org(org_id)
        if not bb or not org:
            return "不可用"
        node = org.get_node(node_id)
        dept = node.department if node else ""
        if not dept:
            return "你未分配部门"
        entries = bb.read_department(dept, limit=args.get("limit", 10))
        if not entries:
            return f"({dept} 暂无部门记忆)"
        return "\n".join(f"[{e.memory_type.value}] {e.content}" for e in entries)

    async def _handle_org_write_dept_memory(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        org = self._runtime.get_org(org_id)
        if not bb or not org:
            return "不可用"
        node = org.get_node(node_id)
        dept = node.department if node else ""
        if not dept:
            return "你未分配部门"
        raw_mt = args.get("memory_type", "fact")
        try:
            mt = MemoryType(raw_mt)
        except ValueError:
            mt = MemoryType.FACT
        entry = bb.write_department(
            dept, args["content"], node_id,
            memory_type=mt,
            tags=normalize_tags(args.get("tags")),
            importance=args.get("importance", 0.5),
        )
        if entry is None:
            return "部门记忆已有相似内容，跳过重复写入"
        await self._runtime._broadcast_ws("org:blackboard_update", {
            "org_id": org_id, "scope": "department", "department": dept,
            "node_id": node_id, "memory_type": args.get("memory_type", "fact"),
            "content": args["content"][:120],
        })
        return f"已写入 {dept} 部门记忆"

    # ------------------------------------------------------------------
    # Policy tools
    # ------------------------------------------------------------------

    async def _handle_org_list_policies(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org_dir = self._runtime._manager._org_dir(org_id)
        policies_dir = org_dir / "policies"
        if not policies_dir.exists():
            return "(暂无制度文件)"
        files = sorted(policies_dir.glob("*.md"))
        if not files:
            return "(暂无制度文件)"
        return "\n".join(f"- {f.name}" for f in files)

    async def _handle_org_read_policy(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org_dir = self._runtime._manager._org_dir(org_id)
        fname = args["filename"]
        if ".." in fname or "/" in fname or "\\" in fname:
            return "非法文件名"
        p = org_dir / "policies" / fname
        if not p.is_file():
            return f"制度文件不存在: {fname}"
        return p.read_text(encoding="utf-8")

    async def _handle_org_search_policy(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org_dir = self._runtime._manager._org_dir(org_id)
        policies_dir = org_dir / "policies"
        query = args["query"].lower()
        results = []
        if policies_dir.exists():
            for f in policies_dir.glob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                    if query in content.lower() or query in f.name.lower():
                        lines = [l for l in content.split("\n") if query in l.lower()][:3]
                        results.append(f"📄 {f.name}\n" + "\n".join(f"  > {l.strip()}" for l in lines))
                except Exception:
                    continue
        if not results:
            return f"未找到与「{args['query']}」相关的制度"
        return "\n\n".join(results)

    # ------------------------------------------------------------------
    # HR tools
    # ------------------------------------------------------------------

    async def _handle_org_freeze_node(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        target_id = args.get("node_id") or args.get("target_node") or ""
        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"
        org.get_parent(target_id)
        caller = org.get_node(node_id)
        if not caller:
            return "你不在此组织中"
        roots = org.get_root_nodes()
        if caller.level >= target.level and (not roots or node_id != roots[0].id):
            return "只能冻结比你层级低的节点"
        target.status = NodeStatus.FROZEN
        target.frozen_by = node_id
        target.frozen_reason = args.get("reason", "")
        target.frozen_at = _now_iso()
        await self._runtime._save_org(org)
        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            messenger.freeze_mailbox(target.id)
        self._runtime.get_event_store(org_id).emit(
            "node_frozen", node_id,
            {"target": target.id, "reason": args.get("reason", "")},
        )
        return f"已冻结 {target.role_title}，原因：{args.get('reason', '')}"

    async def _handle_org_unfreeze_node(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        target_id = args.get("node_id") or args.get("target_node") or ""
        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"
        if target.status != NodeStatus.FROZEN:
            return f"{target.role_title} 未处于冻结状态"
        self._runtime.unfreeze_node(org, target)
        await self._runtime._save_org(org)
        self._runtime.get_event_store(org_id).emit(
            "node_unfrozen", node_id, {"target": target.id},
        )
        return f"已解冻 {target.role_title}"

    async def _handle_org_request_clone(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        scaler = self._runtime.get_scaler()
        try:
            req = await scaler.request_clone(
                org_id=org_id,
                requester=node_id,
                source_node_id=args["source_node_id"],
                reason=args["reason"],
                ephemeral=args.get("ephemeral", True),
            )
            if req.status == "approved":
                return f"克隆申请已自动批准。新节点: {req.result_node_id}"
            return f"克隆申请已提交（ID: {req.id}），等待审批。"
        except ValueError as e:
            return str(e)

    async def _handle_org_request_recruit(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        scaler = self._runtime.get_scaler()
        try:
            req = scaler.request_recruit(
                org_id=org_id,
                requester=node_id,
                role_title=args["role_title"],
                role_goal=args.get("role_goal", ""),
                department=args.get("department", ""),
                parent_node_id=args["parent_node_id"],
                reason=args["reason"],
            )
            return f"招募申请已提交（ID: {req.id}，岗位: {args['role_title']}），等待审批。"
        except ValueError as e:
            return str(e)

    async def _handle_org_dismiss_node(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        scaler = self._runtime.get_scaler()
        ok = await scaler.dismiss_node(org_id, args["node_id"], by=node_id)
        if ok:
            return f"已裁撤节点 {args['node_id']}"
        return "裁撤失败（节点不存在或非临时节点）"

    # ------------------------------------------------------------------
    # Task delivery & acceptance
    # ------------------------------------------------------------------

    async def _handle_org_submit_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        to_node = args.get("to_node", "")
        deliverable = args.get("deliverable", "")
        summary = args.get("summary", "")
        chain_id = args.get("task_chain_id") or _now_iso()

        if not to_node:
            org = self._runtime.get_org(org_id)
            if org:
                parent = org.get_parent(node_id)
                if parent:
                    to_node = parent.id
        if not to_node:
            return "缺少 to_node 参数，无法确定提交目标（没有上级节点）"

        metadata = {
            "deliverable": deliverable[:2000],
            "summary": summary[:500],
            "task_chain_id": chain_id,
        }

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=to_node,
            msg_type=MsgType.TASK_DELIVERED,
            content=f"任务交付: {deliverable[:200]}",
            metadata=metadata,
        )
        ok = await messenger.send(msg)

        self._runtime.get_event_store(org_id).emit(
            "task_delivered", node_id,
            {"to": to_node, "chain_id": chain_id, "deliverable_preview": deliverable[:100]},
        )

        if ok:
            await self._runtime._broadcast_ws("org:task_delivered", {
                "org_id": org_id, "from_node": node_id, "to_node": to_node,
                "chain_id": chain_id, "summary": summary[:120],
            })
            self._link_project_task(org_id, chain_id, status="delivered")
            self._append_execution_log(
                org_id, chain_id,
                f"提交交付物给 {to_node}: {summary[:80]}",
                node_id,
            )
            return f"交付物已提交给 {to_node}，等待验收。"
        return "提交失败"

    async def _handle_org_accept_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        from_node = args.get("from_node", "")
        if not from_node:
            return "缺少 from_node 参数"
        if node_id == from_node:
            return "不能验收自己的交付物"

        chain_id = args.get("task_chain_id", "")
        if chain_id:
            events = self._runtime.get_event_store(org_id)
            if events:
                recent = events.query(event_type="task_accepted", limit=50)
                for ev in recent:
                    if ev.get("data", {}).get("chain_id") == chain_id:
                        return f"Deliverable for chain {chain_id} has already been accepted"

        feedback = args.get("feedback", "验收通过")

        metadata = {
            "task_chain_id": chain_id,
            "acceptance_feedback": feedback[:500],
        }

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=from_node,
            msg_type=MsgType.TASK_ACCEPTED,
            content=f"验收通过: {feedback[:200]}",
            metadata=metadata,
        )
        await messenger.send(msg)

        if chain_id:
            messenger.release_task_affinity(chain_id)
            self._runtime._chain_delegation_depth.pop(chain_id, None)

        self._runtime.get_event_store(org_id).emit(
            "task_accepted", node_id,
            {"from": from_node, "chain_id": chain_id},
        )
        await self._runtime._broadcast_ws("org:task_accepted", {
            "org_id": org_id, "from_node": from_node, "accepted_by": node_id,
            "chain_id": chain_id, "feedback": feedback[:120],
        })
        if chain_id:
            self._link_project_task(org_id, chain_id, status="accepted")
            self._append_execution_log(
                org_id, chain_id, f"验收通过: {feedback[:100]}", node_id,
            )
            self._recalc_parent_progress(org_id, chain_id)

        bb = self._runtime.get_blackboard(org_id)
        if bb:
            bb.write_org(
                content=f"任务验收通过 [{chain_id[:8] if chain_id else ''}]: {feedback[:100]}",
                source_node=node_id,
                memory_type=MemoryType.PROGRESS,
                tags=["acceptance", "completed"],
            )

        return f"已验收 {from_node} 的交付物。"

    async def _handle_org_reject_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        from_node = args.get("from_node", "")
        if not from_node:
            return "缺少 from_node 参数"
        if node_id == from_node:
            return "不能打回自己的交付物"

        chain_id = args.get("task_chain_id", "")
        if chain_id:
            events = self._runtime.get_event_store(org_id)
            if events:
                recent = events.query(event_type="task_accepted", limit=50)
                for ev in recent:
                    if ev.get("data", {}).get("chain_id") == chain_id:
                        return f"Deliverable for chain {chain_id} has already been accepted"

        reason = args.get("reason", "")

        metadata = {
            "task_chain_id": chain_id,
            "rejection_reason": reason[:500],
        }

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=from_node,
            msg_type=MsgType.TASK_REJECTED,
            content=f"任务打回: {reason[:200]}",
            metadata=metadata,
        )
        await messenger.send(msg)

        self._runtime.get_event_store(org_id).emit(
            "task_rejected", node_id,
            {"from": from_node, "chain_id": chain_id, "reason": reason[:100]},
        )
        await self._runtime._broadcast_ws("org:task_rejected", {
            "org_id": org_id, "from_node": from_node, "rejected_by": node_id,
            "chain_id": chain_id, "reason": reason[:120],
        })
        if chain_id:
            self._link_project_task(org_id, chain_id, status="rejected")
            self._append_execution_log(
                org_id, chain_id,
                f"打回: {reason[:80]}",
                node_id,
            )
            self._recalc_parent_progress(org_id, chain_id)

        return f"已打回 {from_node} 的交付物，原因：{reason[:50]}"

    # ------------------------------------------------------------------
    # Pixel appearance
    # ------------------------------------------------------------------

    async def _handle_org_set_appearance(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        node = org.get_node(node_id)
        if not node:
            return f"节点 {node_id} 不存在"

        appearance: dict[str, Any] = {}
        for key in ("body_type", "skin_tone", "hair_style", "hair_color",
                     "outfit_color", "accessory"):
            if key in args and args[key] is not None:
                appearance[key] = args[key]
        if "description" in args and args["description"]:
            appearance["description"] = args["description"]

        if not appearance:
            return "请至少提供一个形象参数或描述"

        from openakita.agents.profile import get_profile_store
        store = get_profile_store()
        profile = store.get(node.agent_profile_id) if node.agent_profile_id else None
        if profile:
            store.update(profile.id, {"pixel_appearance": appearance})

        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            await messenger.broadcast_ws_event(
                "org:appearance_changed",
                {"org_id": org_id, "node_id": node_id, "appearance": appearance},
            )

        return f"像素形象已更新：{json.dumps(appearance, ensure_ascii=False)}"

    # ------------------------------------------------------------------
    # Meeting tools
    # ------------------------------------------------------------------

    async def _handle_org_request_meeting(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        import asyncio

        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        participants = args.get("participants", [])
        topic = args.get("topic", "")
        max_rounds = min(args.get("max_rounds", 3), 5)

        if len(participants) > 6:
            return "会议参与人数上限为 6 人，建议拆分为多个小会议"

        all_members = [node_id] + participants
        valid = [mid for mid in all_members if org.get_node(mid) is not None]
        if len(valid) < 2:
            return "有效参与者不足 2 人"

        meeting_record: list[str] = [f"## 会议主题: {topic}\n"]
        meeting_record.append(f"主持人: {node_id}")
        meeting_record.append(f"参与者: {', '.join(participants)}\n")

        await self._runtime._broadcast_ws("org:meeting_started", {
            "org_id": org_id, "topic": topic,
            "host": node_id, "participants": participants, "rounds": max_rounds,
        })

        prev_round_summary = ""
        for round_num in range(1, max_rounds + 1):
            meeting_record.append(f"\n### 第 {round_num} 轮\n")

            await self._runtime._broadcast_ws("org:meeting_round", {
                "org_id": org_id, "round": round_num, "total_rounds": max_rounds,
            })

            async def _get_opinion(
                pid: str,
                _round: int = round_num,
                _prev: str = prev_round_summary,
            ) -> tuple[str, str]:
                node_obj = org.get_node(pid)
                if not node_obj or node_obj.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
                    return pid, "(缺席)"
                try:
                    response = await self._lightweight_meeting_speak(
                        org, node_obj, topic, _round, max_rounds, _prev,
                    )
                    return pid, response
                except Exception as e:
                    logger.error(f"[Meeting] {pid} speak error: {e}")
                    return pid, "(发言异常)"

            results = await asyncio.gather(*[_get_opinion(pid) for pid in valid])

            round_opinions = []
            for pid, response in results:
                node_obj = org.get_node(pid)
                title = node_obj.role_title if node_obj else pid
                meeting_record.append(f"- **{title}**: {response}")
                round_opinions.append(f"{title}: {response}")
                await self._runtime._broadcast_ws("org:meeting_speak", {
                    "org_id": org_id, "node_id": pid, "role_title": title,
                    "round": round_num, "content": response[:200],
                })

            prev_round_summary = "\n".join(round_opinions)

        conclusion = await self._meeting_summarize(org_id, topic, meeting_record)
        if conclusion:
            meeting_record.append(f"\n### 会议结论\n\n{conclusion}")

        bb = self._runtime.get_blackboard(org_id)
        if bb:
            summary_text = conclusion or meeting_record[-1][:200]
            bb.write_org(
                content=f"会议结论 — {topic}: {summary_text}",
                source_node=node_id,
                memory_type=MemoryType.DECISION,
                tags=["meeting"],
            )
            await self._runtime._broadcast_ws("org:blackboard_update", {
                "org_id": org_id, "node_id": node_id, "scope": "org",
            })

        self._runtime.get_event_store(org_id).emit(
            "meeting_completed", node_id,
            {"topic": topic, "participants": participants, "rounds": max_rounds},
        )

        await self._runtime._broadcast_ws("org:meeting_completed", {
            "org_id": org_id, "topic": topic,
            "conclusion": (conclusion or "")[:300],
        })

        return "\n".join(meeting_record)

    async def _lightweight_meeting_speak(
        self,
        org: Any,
        node: Any,
        topic: str,
        round_num: int,
        max_rounds: int,
        prev_round_summary: str,
    ) -> str:
        """轻量会议发言：直接 LLM 单次调用，不走完整 Agent/ReAct 循环。"""
        identity = self._runtime._get_identity(org.id)
        role_prompt = ""
        if identity:
            try:
                resolved = identity.resolve(node, org)
                role_prompt = (resolved.role or "")[:400]
            except Exception:
                pass

        context_parts = [
            f"你是「{org.name}」的 {node.role_title}（{node.department or ''}）。",
        ]
        role_goal = getattr(node, "role_goal", "") or ""
        if role_goal:
            context_parts.append(f"你的目标: {role_goal[:200]}")
        if role_prompt:
            context_parts.append(role_prompt)

        system_prompt = "\n".join(context_parts)

        user_parts = [
            f"你正在参加一个关于「{topic}」的组织内部会议（第 {round_num}/{max_rounds} 轮）。",
        ]
        if prev_round_summary:
            user_parts.append(f"\n上一轮发言摘要:\n{prev_round_summary[:800]}\n")
        user_parts.append(
            "请基于你的职责和专业领域，发表简洁的观点（100-200字）。"
            "直接表达核心观点，不要客套寒暄。"
        )

        try:
            text = await self._llm_simple_call(
                system_prompt, "\n".join(user_parts), max_tokens=400,
            )
            return text[:500] if text else "(无内容)"
        except Exception as e:
            logger.error(f"[Meeting] LLM call failed for {node.id}: {e}")
            return f"(发言失败: {e})"

    async def _meeting_summarize(
        self, org_id: str, topic: str, meeting_record: list[str],
    ) -> str:
        """用 LLM 生成会议结论。"""
        full_record = "\n".join(meeting_record)
        if len(full_record) > 3000:
            full_record = full_record[:3000] + "\n...(已截断)"

        user_msg = (
            f"以下是关于「{topic}」的会议讨论记录:\n\n{full_record}\n\n"
            "请总结会议结论，包括: 1) 达成的共识 2) 待决事项 3) 行动计划。"
            "用 150-300 字简洁总结。"
        )
        try:
            text = await self._llm_simple_call(
                "你是一位专业的会议记录员。", user_msg, max_tokens=500,
            )
            return (text or "")[:600]
        except Exception as e:
            logger.error(f"[Meeting] Summary LLM failed: {e}")
            return ""

    async def _llm_simple_call(
        self, system: str, user_content: str, max_tokens: int = 400,
    ) -> str:
        """统一的轻量 LLM 调用：兼容 Message 类型和 dict 类型 response。"""
        from openakita.llm.client import chat as llm_chat
        from openakita.llm.types import Message

        messages = [Message(role="user", content=user_content)]
        resp = await llm_chat(messages, system=system, max_tokens=max_tokens)
        if hasattr(resp, "text"):
            return resp.text or ""
        if isinstance(resp, dict):
            return resp.get("text", "") or str(resp.get("content", ""))
        return str(resp)

    # ------------------------------------------------------------------
    # Schedule tools
    # ------------------------------------------------------------------

    async def _handle_org_create_schedule(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        schedule_params = {
            "name": args["name"],
            "schedule_type": args.get("schedule_type", "interval"),
            "cron": args.get("cron"),
            "interval_s": args.get("interval_s"),
            "run_at": args.get("run_at"),
            "prompt": args["prompt"],
            "report_to": args.get("report_to"),
            "report_condition": args.get("report_condition", "on_issue"),
        }

        inbox = self._runtime.get_inbox(org_id)
        inbox.push_approval_request(
            org_id, node_id,
            title=f"{node_id} 申请创建定时任务「{args['name']}」",
            body=f"任务指令: {args['prompt'][:100]}\n类型: {args.get('schedule_type', 'interval')}",
            metadata={
                "action_type": "create_schedule",
                "node_id": node_id,
                "schedule_params": schedule_params,
            },
        )

        self._runtime.get_event_store(org_id).emit(
            "schedule_requested", node_id,
            {"name": args["name"]},
        )
        return f"定时任务「{args['name']}」已提交审批，批准后将自动创建。"

    async def _handle_org_list_my_schedules(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        schedules = self._runtime._manager.get_node_schedules(org_id, node_id)
        if not schedules:
            return "你目前没有定时任务"
        lines = []
        for s in schedules:
            status = "✅ 启用" if s.enabled else "⏸️ 暂停"
            freq = s.cron or (f"每 {s.interval_s}s" if s.interval_s else s.run_at or "未设置")
            last = s.last_run_at or "从未执行"
            lines.append(f"- [{status}] {s.name} | 频率: {freq} | 上次: {last}")
        return "\n".join(lines)

    async def _handle_org_assign_schedule(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        target_id = args["target_node_id"]
        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"

        caller = org.get_node(node_id)
        if caller and caller.level >= target.level:
            parent = org.get_parent(target_id)
            if not parent or parent.id != node_id:
                return "只能给直属下级指定定时任务"

        sched = NodeSchedule(
            name=args["name"],
            schedule_type=ScheduleType(args.get("schedule_type", "interval")),
            cron=args.get("cron"),
            interval_s=args.get("interval_s"),
            prompt=args["prompt"],
            report_to=args.get("report_to", node_id),
            report_condition=args.get("report_condition", "on_issue"),
            enabled=True,
        )
        self._runtime._manager.add_node_schedule(org_id, target_id, sched)

        self._runtime.get_event_store(org_id).emit(
            "schedule_assigned", node_id,
            {"target": target_id, "schedule_id": sched.id, "name": sched.name},
        )
        return f"已为 {target.role_title} 指定定时任务「{sched.name}」（ID: {sched.id}）"

    # ------------------------------------------------------------------
    # Policy proposal tool
    # ------------------------------------------------------------------

    async def _handle_org_propose_policy(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        inbox = self._runtime.get_inbox(org_id)
        inbox.push_approval_request(
            org_id, node_id,
            title=f"制度提议: {args['title']}",
            body=f"提议者: {node_id}\n原因: {args['reason']}\n文件: {args['filename']}\n\n{args['content'][:500]}",
            options=["approve", "reject"],
            metadata={
                "policy_filename": args["filename"],
                "policy_content": args["content"],
                "policy_title": args["title"],
            },
        )

        self._runtime.get_event_store(org_id).emit(
            "policy_proposed", node_id,
            {"filename": args["filename"], "title": args["title"]},
        )
        return f"制度提议「{args['title']}」已提交审批。"

    # ------------------------------------------------------------------
    # Tool request / grant / revoke
    # ------------------------------------------------------------------

    async def _handle_org_request_tools(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        parent = org.get_parent(node_id)
        if not parent:
            return "你是最高级节点，无法向上级申请。请直接配置 external_tools。"

        tools = args.get("tools", [])
        reason = args.get("reason", "")
        if not tools:
            return "参数不完整：请指定需要申请的工具列表（tools）。"

        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "消息系统未就绪"

        from .tool_categories import TOOL_CATEGORIES
        ", ".join(tools)
        cat_details = []
        for t in tools:
            if t in TOOL_CATEGORIES:
                cat_details.append(f"{t}({', '.join(TOOL_CATEGORIES[t])})")
            else:
                cat_details.append(t)

        content = (
            f"[工具申请] {node_id} 申请增加外部工具：{', '.join(cat_details)}\n"
            f"申请原因：{reason}\n\n"
            f"如果批准，请使用 org_grant_tools(node_id=\"{node_id}\", tools={tools}) 授权。"
        )

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=parent.id,
            msg_type=MsgType.QUESTION,
            content=content,
            metadata={"_tool_request": True, "requested_tools": tools},
        )
        await messenger.send(msg)

        self._runtime.get_event_store(org_id).emit(
            "tools_requested", node_id,
            {"tools": tools, "reason": reason, "superior": parent.id},
        )
        return f"工具申请已发送给 {parent.role_title}（{parent.id}），等待审批。"

    async def _handle_org_grant_tools(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"

        target_id = args.get("node_id", "")
        tools = args.get("tools", [])
        if not target_id or not tools:
            return "参数不完整：需要 node_id 和 tools"

        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"

        children = org.get_children(node_id)
        child_ids = {c.id for c in children}
        if target_id not in child_ids:
            return f"{target_id} 不是你的直属下级，无法授权。"

        existing = set(target.external_tools)
        for t in tools:
            if t not in existing:
                target.external_tools.append(t)
                existing.add(t)

        await self._runtime._save_org(org)
        self._runtime.evict_node_agent(org_id, target_id)

        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            notify = OrgMessage(
                org_id=org_id,
                from_node=node_id,
                to_node=target_id,
                msg_type=MsgType.FEEDBACK,
                content=f"你的工具权限已更新，新增：{', '.join(tools)}。下次激活时生效。",
                metadata={"_tool_grant": True, "granted_tools": tools},
            )
            await messenger.send(notify)

        self._runtime.get_event_store(org_id).emit(
            "tools_granted", node_id,
            {"target": target_id, "tools": tools},
        )
        return f"已授权 {target.role_title}（{target_id}）使用：{', '.join(tools)}"

    async def _handle_org_revoke_tools(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"

        target_id = args.get("node_id", "")
        tools = args.get("tools", [])
        if not target_id or not tools:
            return "参数不完整：需要 node_id 和 tools"

        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"

        children = org.get_children(node_id)
        child_ids = {c.id for c in children}
        if target_id not in child_ids:
            return f"{target_id} 不是你的直属下级，无法操作。"

        removed = []
        for t in tools:
            if t in target.external_tools:
                target.external_tools.remove(t)
                removed.append(t)

        if not removed:
            return f"{target.role_title} 没有这些工具可收回。"

        await self._runtime._save_org(org)
        self._runtime.evict_node_agent(org_id, target_id)

        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            notify = OrgMessage(
                org_id=org_id,
                from_node=node_id,
                to_node=target_id,
                msg_type=MsgType.FEEDBACK,
                content=f"你的部分工具权限已收回：{', '.join(removed)}。下次激活时生效。",
                metadata={"_tool_revoke": True, "revoked_tools": removed},
            )
            await messenger.send(notify)

        self._runtime.get_event_store(org_id).emit(
            "tools_revoked", node_id,
            {"target": target_id, "tools": removed},
        )
        return f"已收回 {target.role_title}（{target_id}）的工具：{', '.join(removed)}"

    # ------------------------------------------------------------------
    # Project task tools
    # ------------------------------------------------------------------

    async def _handle_org_report_progress(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        chain_id = args.get("task_chain_id", "")
        if not chain_id:
            return "缺少 task_chain_id"
        try:
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            existing = store.find_task_by_chain(chain_id)
            if not existing:
                return f"未找到任务链 {chain_id[:12]}"
            updates: dict[str, Any] = {}
            if "progress_pct" in args:
                pct = args["progress_pct"]
                try:
                    updates["progress_pct"] = min(100, max(0, int(pct)))
                except (ValueError, TypeError):
                    pass
            if args.get("log_entry"):
                log_entry = {"at": _now_iso(), "by": node_id, "entry": args["log_entry"][:500]}
                new_log = list(existing.execution_log or []) + [log_entry]
                updates["execution_log"] = new_log
            if updates:
                store.update_task(existing.project_id, existing.id, updates)
            return f"已汇报进度: {updates.get('progress_pct', '')}%"
        except Exception as e:
            logger.debug("org_report_progress failed: %s", e)
            return f"汇报失败: {e}"

    async def _handle_org_get_task_progress(
        self, args: dict, org_id: str, node_id: str
    ) -> dict:
        try:
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            task = None
            if args.get("task_chain_id"):
                task = store.find_task_by_chain(args["task_chain_id"])
            elif args.get("task_id"):
                task, _ = store.get_task(args["task_id"])
            if not task:
                return {"error": "任务未找到"}
            return {
                "id": task.id,
                "title": task.title,
                "status": task.status.value,
                "progress_pct": task.progress_pct,
                "plan_steps": task.plan_steps or [],
                "execution_log": task.execution_log or [],
                "assignee_node_id": task.assignee_node_id,
                "chain_id": task.chain_id,
            }
        except Exception as e:
            logger.debug("org_get_task_progress failed: %s", e)
            return {"error": str(e)}

    async def _handle_org_list_my_tasks(
        self, args: dict, org_id: str, node_id: str
    ) -> list:
        try:
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            status = args.get("status")
            limit = args.get("limit", 10)
            tasks = store.all_tasks(assignee=node_id, status=status)
            return [t for t in tasks[:limit]]
        except Exception as e:
            logger.debug("org_list_my_tasks failed: %s", e)
            return []

    async def _handle_org_list_delegated_tasks(
        self, args: dict, org_id: str, node_id: str
    ) -> list:
        try:
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            status = args.get("status")
            limit = args.get("limit", 10)
            tasks = store.all_tasks(delegated_by=node_id, status=status)
            return [t for t in tasks[:limit]]
        except Exception as e:
            logger.debug("org_list_delegated_tasks failed: %s", e)
            return []

    async def _handle_org_list_project_tasks(
        self, args: dict, org_id: str, node_id: str
    ) -> list:
        project_id = args.get("project_id", "")
        if not project_id:
            return []
        try:
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            proj = store.get_project(project_id)
            if not proj:
                return []
            status = args.get("status")
            limit = args.get("limit", 20)
            tasks = [
                {**t.to_dict(), "project_name": proj.name}
                for t in proj.tasks
                if not status or t.status.value == status
            ]
            return tasks[:limit]
        except Exception as e:
            logger.debug("org_list_project_tasks failed: %s", e)
            return []

    async def _handle_org_update_project_task(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        task_id = args.get("task_id")
        chain_id = args.get("task_chain_id")
        if not task_id and not chain_id:
            return "需要 task_id 或 task_chain_id"
        try:
            from openakita.orgs.project_store import ProjectStore
            from openakita.orgs.models import TaskStatus

            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            task = None
            proj_id = None
            if chain_id:
                task = store.find_task_by_chain(chain_id)
                if task:
                    proj_id = task.project_id
                    task_id = task.id
            elif task_id:
                task, proj = store.get_task(task_id)
                if task:
                    proj_id = task.project_id
            if not task or not proj_id:
                return "任务未找到"
            updates: dict[str, Any] = {}
            if "progress_pct" in args:
                try:
                    updates["progress_pct"] = min(100, max(0, int(args["progress_pct"])))
                except (ValueError, TypeError):
                    pass
            if "status" in args:
                try:
                    updates["status"] = TaskStatus(args["status"])
                except ValueError:
                    pass
            if "plan_steps" in args:
                updates["plan_steps"] = args["plan_steps"]
            if "execution_log" in args:
                new_entries = args["execution_log"]
                if isinstance(new_entries, list):
                    existing = list(task.execution_log or [])
                    for e in new_entries:
                        entry = e if isinstance(e, dict) else {"at": _now_iso(), "by": node_id, "entry": str(e)[:500]}
                        existing.append(entry)
                    updates["execution_log"] = existing
            if updates:
                store.update_task(proj_id, task_id, updates)
            return "已更新"
        except Exception as e:
            logger.debug("org_update_project_task failed: %s", e)
            return f"更新失败: {e}"

    async def _handle_org_create_project_task(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        project_id = args.get("project_id", "")
        title = args.get("title", "")
        if not project_id or not title:
            return "需要 project_id 和 title"
        try:
            from openakita.orgs.project_store import ProjectStore
            from openakita.orgs.models import ProjectTask, TaskStatus

            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            proj = store.get_project(project_id)
            if not proj:
                return f"项目 {project_id} 不存在"
            parent_task_id = args.get("parent_task_id")
            depth = 0
            if parent_task_id:
                parent_task, _ = store.get_task(parent_task_id)
                if parent_task:
                    depth = (parent_task.depth or 0) + 1
            task = ProjectTask(
                project_id=project_id,
                title=title[:120],
                description=args.get("description", ""),
                status=TaskStatus.TODO,
                assignee_node_id=args.get("assignee_node_id"),
                chain_id=args.get("chain_id"),
                parent_task_id=parent_task_id,
                depth=depth,
            )
            store.add_task(project_id, task)
            return f"已创建任务 {task.id}: {title[:50]}"
        except Exception as e:
            logger.debug("org_create_project_task failed: %s", e)
            return f"创建失败: {e}"
