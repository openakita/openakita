"""
OrgToolHandler — 组织工具执行器

处理组织节点 Agent 调用的 org_* 系列工具。
每个 handler 方法接收 tool_name, arguments, context(org_id, node_id) 并返回结果。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

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

_LIM_EVENT = 10000
_LIM_WS = 2000
_LIM_EXEC_LOG = 2000
_LIM_TOOL_RETURN = 200
_LIM_TITLE = 200

# BUG-3：识别用户原始指令是否带"硬边界关键词"。命中时 _handle_org_delegate_task
# 会把这段原始指令做成"父任务硬边界"前置到子任务 task_content 里，避免子节点
# 撕破"50 字以内/不要写代码/只列纲要"等显式约束。
# 关键词集合保守选取（高召回 + 低误伤）；未命中时不附加边界，对普通任务零影响。
_BOUNDARY_KEYWORDS = (
    "字以内", "字以下", "字之内", "字内", "字的",
    "字简述", "字概述", "字摘要", "字方案",
    "不要写代码", "不要代码", "禁止写代码", "不要实现", "不要写实现",
    "只列", "只要", "仅列", "仅要", "只给", "仅给",
    "纲要", "提纲", "要点", "概要", "摘要",
    "简短", "简洁", "简述", "概述",
    "一句话", "几句话", "三两句", "两三句",
)


def _has_explicit_boundary(text: str) -> bool:
    """Return True when the text contains an explicit scope/format constraint."""
    if not text:
        return False
    return any(kw in text for kw in _BOUNDARY_KEYWORDS)

# Tools whose ``to_node`` / ``node_id`` / ``target_node_id`` parameters must
# resolve to a **specific** node before the handler runs. Used by
# ``OrgToolHandler._resolve_node_refs`` to switch from lenient fuzzy matching
# (which is the historical behaviour for search tools like
# ``org_find_colleague``) to strict exact-only matching (so that ambiguous
# role titles surface as structured errors instead of silently binding to
# the wrong node — typically the caller itself).
_STRICT_REF_TOOLS: set[str] = {
    "org_delegate_task",
    "org_send_message",
    "org_reply_message",
    "org_submit_deliverable",
    "org_accept_deliverable",
    "org_reject_deliverable",
}


class OrgToolHandler:
    """Dispatch and execute org_* tool calls."""

    def __init__(self, runtime: OrgRuntime) -> None:
        self._runtime = runtime

    def _org_not_running_error(self, org_id: str) -> str:
        """根据组织是否刚被显式 stop/delete 返回不同的错误消息。

        - 若组织在近期被显式停止/删除：返回"组织已停止，任务被取消"，
          让 LLM 知道这是一次终态，不应再重试。
        - 否则（组织未激活、id 不存在等）：返回原来的"组织未运行"。
        """
        try:
            if self._runtime.is_org_recently_stopped(org_id):
                return (
                    "[组织已停止] 组织已被停止或删除，当前任务已被取消。"
                    "请停止继续调用任何 org_* 工具，直接给用户一个文字总结说明任务已终止。"
                )
        except Exception:
            pass
        return "组织未运行"

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

    def _resolve_node_refs(
        self, args: dict, org_id: str, tool_name: str | None = None
    ) -> None:
        """Resolve node references: LLM may pass role titles or wrong-cased IDs.

        Behaviour depends on *tool_name*:

        - If ``tool_name`` is in ``_STRICT_REF_TOOLS`` (write-effect tools
          like delegate / send_message / reply_message), we only rewrite
          ``args[key]`` to the canonical node id when ``resolve_reference``
          returns ``exact_id`` or ``exact_title``. Ambiguous or fuzzy
          matches are **kept as-is** so the downstream handler can surface
          a structured error listing the candidate IDs — this is what
          prevents the "产品总监" ↔ "产品经理" substring collision from
          silently resolving the caller to itself.
        - If ``tool_name`` is outside that set (search / read tools such
          as org_find_colleague, org_get_memory_of_node, org_pause_node,
          …), we keep the historical lenient behaviour: any hit — exact
          or fuzzy — wins, matching pre-existing caller expectations and
          avoiding regressions in search flows.

        ``tool_name=None`` defaults to the lenient path for backward
        compatibility with any direct test harness.
        """
        org = self._runtime.get_org(org_id)
        if not org:
            return

        strict = tool_name in _STRICT_REF_TOOLS

        for key in ("to_node", "node_id", "target_node_id"):
            val = args.get(key, "")
            if not val:
                continue

            if strict:
                node, _candidates, status = org.resolve_reference(val)
                # Exact hits are safe to rewrite; everything else (ambiguous
                # title, fuzzy, not_found) must be passed through untouched
                # so the handler can emit an informative error including
                # the candidate list.
                if status in ("exact_id", "exact_title") and node is not None:
                    args[key] = node.id
                continue

            # Lenient path (search / read tools): first try exact hits,
            # then fall back to the legacy substring / title / id matching.
            if org.get_node(val):
                continue
            val_lower = val.lower().replace(" ", "_").replace("-", "_")
            for n in org.nodes:
                if (
                    n.id == val_lower
                    or n.role_title == val
                    or n.role_title.lower() == val.lower()
                ):
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

    @staticmethod
    def _attachment_key(att: dict) -> tuple[str, str]:
        """Stable dedup key for a file attachment dict.

        Key = (filename, file_path). Size/timestamp are intentionally excluded
        so a re-write of the same file (which may change size by a byte) is
        treated as the same attachment and replaces the previous entry.
        """
        if not isinstance(att, dict):
            return ("", "")
        filename = str(att.get("filename") or "").strip()
        file_path = str(att.get("file_path") or att.get("path") or "").strip()
        return (filename, file_path)

    @classmethod
    def _merge_file_attachments(
        cls, existing: list[dict], incoming: list[dict]
    ) -> list[dict]:
        """Merge incoming attachments into existing list, deduping by (filename, file_path).

        If a newer attachment shares a key with an older one, the newer
        replaces the older (keeping insertion order at the old position).
        Entries with an empty key are appended as-is (defensive fallback).
        """
        result: list[dict] = []
        index_by_key: dict[tuple[str, str], int] = {}
        for att in existing or []:
            key = cls._attachment_key(att)
            if not key[0] and not key[1]:
                result.append(att)
                continue
            if key in index_by_key:
                result[index_by_key[key]] = att
            else:
                index_by_key[key] = len(result)
                result.append(att)
        for att in incoming or []:
            key = cls._attachment_key(att)
            if not key[0] and not key[1]:
                result.append(att)
                continue
            if key in index_by_key:
                result[index_by_key[key]] = att
            else:
                index_by_key[key] = len(result)
                result.append(att)
        return result

    # 文件名清洗：去掉路径分隔符 / 控制字符 / 平台保留字符，避免 LLM
    # 给的标题里包含 ../ 或 :*?"<>| 这种东西穿越到 workspace 之外。
    _DELIVERABLE_NAME_FORBIDDEN = set('\\/:*?"<>|\r\n\t')

    # 自动落盘 deliverable 的最小字符数。低于这个长度通常是聊天式回复
    # （"我已完成"），落盘成附件反而噪音。LLM 写出的真实文档（带 markdown
    # 标题或列表）通常 ≥300 字符；用户实测 case ~ 476 字符。
    _DELIVERABLE_AUTO_PERSIST_MIN_CHARS = 300

    @classmethod
    def _slugify_deliverable_title(cls, title: str) -> str:
        cleaned = "".join(
            ch for ch in (title or "") if ch not in cls._DELIVERABLE_NAME_FORBIDDEN
        ).strip()
        cleaned = cleaned.replace(" ", "_")
        if len(cleaned) > 60:
            cleaned = cleaned[:60].rstrip("_- ")
        return cleaned or "deliverable"

    @staticmethod
    def _looks_like_structured_document(body: str) -> bool:
        """Heuristic to decide whether a deliverable string is a 'document'
        worth materialising as an attachment.

        True if ANY of:
          - Has at least one ATX markdown heading (`#`..`######`) at line start
          - Has at least 3 bullet list items (`- ` or `* `) at line start
          - Contains a fenced code block (```)

        Designed to be conservative so plain conversational replies like
        "我已完成" do not trigger auto-persist.
        """
        if not body:
            return False
        import re
        if re.search(r"(?m)^\s{0,3}#{1,6}\s", body):
            return True
        bullet_lines = re.findall(r"(?m)^\s{0,3}[-*]\s+\S", body)
        if len(bullet_lines) >= 3:
            return True
        if "```" in body:
            return True
        return False

    def _auto_persist_deliverable(
        self,
        *,
        workspace,
        chain_id: str,
        title: str,
        body: str,
    ):
        """Persist a long inline deliverable to ``<workspace>/deliverables/``.

        Returns the absolute Path on success, or None on any failure (caller
        only logs a warning and continues; this is a best-effort fallback).
        Resolved path is verified to stay strictly inside the workspace
        ``deliverables`` folder so that a malicious / careless LLM-supplied
        title cannot escape via path-traversal.
        """
        from datetime import datetime
        from pathlib import Path

        try:
            base_ws = Path(workspace).resolve()
        except Exception:
            return None
        deliverables_dir = (base_ws / "deliverables").resolve()
        try:
            deliverables_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None

        slug = self._slugify_deliverable_title(title)
        chain_short = (chain_id or "chain").split(":")[-1][:12] or "chain"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate = (deliverables_dir / f"{chain_short}_{slug}_{ts}.md").resolve()

        try:
            deliverables_dir_str = str(deliverables_dir)
            if not str(candidate).startswith(deliverables_dir_str):
                return None
        except Exception:
            return None

        header = f"# {title.strip() or '交付物'}\n\n" if title else ""
        try:
            candidate.write_text(header + (body or ""), encoding="utf-8")
        except Exception:
            return None
        return candidate

    # 节点级最终答复兜底落盘的最小字符数。比 submit_deliverable 通道更宽松：
    # 此通道由 OrgRuntime 在 expects_artifact=True、且本任务零文件登记时主动
    # 触发，已完成"是否需要附件"的语义把关，无需再用结构化文档启发式过滤。
    # 200 字符是经验阈值——比"我已完成"长，又能覆盖 200~300 字的简短报告。
    _FINAL_ANSWER_AUTO_PERSIST_MIN_CHARS = 200

    def auto_persist_node_final_answer(
        self,
        *,
        org_id: str,
        node_id: str,
        chain_id: str | None,
        title: str,
        body: str,
        workspace,
    ) -> dict | None:
        """OrgRuntime 兜底落盘入口：把节点的纯文字最终答复保存为 .md 附件。

        与 ``org_submit_deliverable`` 内的 auto-persist 分支对应，但调用面
        更窄：只在「用户确实期望附件交付 + 本任务还没产出过任何文件」时被
        ``runtime._run_node_task`` 调用，因此不复用 ``_looks_like_structured_document``
        启发式（语义把关已在上游完成），但保留同样的：
          * 字符数下限（200，比 deliverable 通道宽松）；
          * path-traversal 校验（沿用 ``_auto_persist_deliverable``）；
          * **唯一登记入口** ``runtime._register_file_output`` —— 不引入并行
            落盘/广播路径，确保黑板写入、WS 广播、ProjectTask 联动只发生一次。

        失败/不满足条件时返回 None；不抛异常（best-effort，不能影响主流程）。
        """
        body_stripped = (body or "").strip()
        if not body_stripped:
            return None
        if len(body_stripped) < self._FINAL_ANSWER_AUTO_PERSIST_MIN_CHARS:
            return None
        if workspace is None:
            return None
        try:
            persisted_path = self._auto_persist_deliverable(
                workspace=workspace,
                chain_id=chain_id or "",
                title=(title or "").strip() or "final_answer",
                body=body_stripped,
            )
        except Exception:
            logger.warning(
                "[ToolHandler] auto_persist_node_final_answer write failed",
                exc_info=True,
            )
            return None
        if persisted_path is None:
            return None
        try:
            registered = self._runtime._register_file_output(
                org_id, node_id,
                chain_id=chain_id or None,
                filename=persisted_path.name,
                file_path=str(persisted_path),
                workspace=workspace,
            )
        except Exception:
            logger.warning(
                "[ToolHandler] auto_persist_node_final_answer register failed",
                exc_info=True,
            )
            return None
        if registered:
            logger.info(
                "[ToolHandler] auto-persisted node final answer to %s "
                "(org=%s node=%s chain=%s len=%d)",
                persisted_path, org_id, node_id, chain_id, len(body_stripped),
            )
        return registered

    def _link_project_task(
        self, org_id: str, chain_id: str, *,
        title: str = "",
        assignee: str | None = None,
        delegated_by: str | None = None,
        status: str | None = None,
        parent_task_id: str | None = None,
        depth: int = 0,
        deliverable_content: str = "",
        delivery_summary: str = "",
        file_attachment: dict | None = None,
    ) -> None:
        """Auto-link a task chain to an active project's ProjectTask.

        Priority: chain_id match -> assignee match (project with assignee's tasks)
        -> first active project fallback.
        """
        try:
            from openakita.orgs.models import ProjectTask, TaskStatus
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            org_dir = mgr._org_dir(org_id)
            store = ProjectStore(org_dir)

            # 1. chain_id match
            existing = store.find_task_by_chain(chain_id)
            if existing:
                # Skip updates if task has been cancelled or reset by user
                if existing.status in (TaskStatus.CANCELLED, TaskStatus.TODO, TaskStatus.ACCEPTED):
                    logger.info(
                        f"[ToolHandler] Skipping update for task {existing.id}: "
                        f"status={existing.status.value} (externally changed)"
                    )
                    return
                updates: dict[str, Any] = {}
                if status:
                    updates["status"] = TaskStatus(status)
                    if status == "in_progress" and not existing.started_at:
                        updates["started_at"] = _now_iso()
                        if (existing.progress_pct or 0) < 5:
                            updates["progress_pct"] = 5
                    elif status == "delivered":
                        updates["delivered_at"] = _now_iso()
                        updates["progress_pct"] = max(existing.progress_pct or 0, 80)
                    elif status == "accepted":
                        updates["completed_at"] = _now_iso()
                        updates["progress_pct"] = 100
                if deliverable_content:
                    old = existing.deliverable_content or ""
                    new_stripped = deliverable_content.strip()
                    old_stripped = old.strip()
                    if not old_stripped:
                        updates["deliverable_content"] = deliverable_content
                    elif new_stripped == old_stripped:
                        # exact same payload — do not store again
                        pass
                    elif new_stripped in old_stripped:
                        # new content fully contained in old — skip append
                        pass
                    elif old_stripped in new_stripped:
                        # new content is a superset — replace
                        updates["deliverable_content"] = deliverable_content
                    else:
                        updates["deliverable_content"] = old + "\n\n---\n\n" + deliverable_content
                if delivery_summary:
                    updates["delivery_summary"] = delivery_summary
                if file_attachment:
                    updates["file_attachments"] = self._merge_file_attachments(
                        list(existing.file_attachments or []),
                        [file_attachment],
                    )
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
                title=title[:_LIM_TITLE],
                status=TaskStatus.IN_PROGRESS,
                assignee_node_id=assignee,
                delegated_by=delegated_by,
                chain_id=chain_id,
                parent_task_id=parent_task_id,
                depth=depth,
                started_at=_now_iso(),
                deliverable_content=deliverable_content,
                delivery_summary=delivery_summary,
            )
            store.add_task(proj.id, task)
        except Exception as exc:
            logger.debug("project-task auto-link failed: %s", exc)

    def _append_execution_log(
        self, org_id: str, chain_id: str, entry: str, node_id: str
    ) -> None:
        """Append an entry to a ProjectTask's execution_log."""
        try:
            from openakita.orgs.models import TaskStatus
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            org_dir = mgr._org_dir(org_id)
            store = ProjectStore(org_dir)
            existing = store.find_task_by_chain(chain_id)
            if not existing:
                return
            if existing.status in (TaskStatus.CANCELLED, TaskStatus.TODO, TaskStatus.ACCEPTED):
                return
            log_entry = {"at": _now_iso(), "by": node_id, "entry": entry[:_LIM_EXEC_LOG]}
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
            from openakita.orgs.models import TaskStatus
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            org_dir = mgr._org_dir(org_id)
            store = ProjectStore(org_dir)
            existing = store.find_task_by_chain(chain_id)
            if not existing:
                return

            if tool_name == "create_plan":
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
                    f"计划创建: {tool_input.get('task_summary', '')[:_LIM_EXEC_LOG]}",
                    node_id,
                )
            elif tool_name == "update_plan_step":
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
                    f"步骤 {step_id}: {status} - {result_text[:_LIM_EXEC_LOG]}",
                    node_id,
                )
            elif tool_name == "complete_plan":
                summary = tool_input.get("summary", "")
                store.update_task(existing.project_id, existing.id, {
                    "status": TaskStatus.ACCEPTED,
                    "progress_pct": 100,
                    "completed_at": _now_iso(),
                })
                self._append_execution_log(
                    org_id, chain_id,
                    f"计划完成: {summary[:_LIM_EXEC_LOG]}",
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

        # 每次 org_* 工具调用都是一次"组织在活动"的进度信号，用来阻止命令
        # 看门狗误判卡死。对没有进行中 UserCommandTracker 的 org 是 O(0)。
        try:
            touch = getattr(self._runtime, "_touch_trackers_for_org", None)
            if callable(touch):
                touch(org_id)
        except Exception:
            pass

        arguments = self._resolve_aliases(arguments)
        arguments = self._coerce_types(arguments)
        self._resolve_node_refs(arguments, org_id, tool_name=tool_name)

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

    # ── 协调者反模式 heuristic guard ──
    # 协调者（有下属的节点）经常错误地用 ``org_send_message(question)`` 给下级
    # 派发任务，绕过 ``org_delegate_task`` 的 chain 注册，导致：
    #   1) UserCommandTracker 看不到子任务，提前判定命令完成
    #   2) 子任务无 deadline / 无验收闭环
    # 触发条件：sender 有直属下级 + msg_type=question + content 含明显任务措辞。
    # 触发后拒绝发送，引导改用 org_delegate_task。受
    # ``org_question_task_guard`` flag 控制，可一键关闭。
    _TASK_INTENT_PATTERNS: tuple[str, ...] = (
        "撰写", "编写", "起草", "草拟", "拟定",
        "优化", "改写", "重写",
        "产出", "给出", "生成", "制作", "做一份", "做一版",
        "完成", "完成一份", "完成一版",
        "整理一份", "整理出", "提供一份", "提供一版",
        "出一份", "出一版", "出一稿",
        "写一篇", "写一份", "写一版", "写一稿",
        "给我一份", "给我一稿", "给我一版",
    )

    def _looks_like_task_assignment(self, content: str) -> bool:
        if not content:
            return False
        return any(p in content for p in self._TASK_INTENT_PATTERNS)

    async def _handle_org_send_message(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return self._org_not_running_error(org_id)

        # 反模式拦截：协调者用 question 派任务（受 flag 控制）
        try:
            from openakita.config import settings as _settings_sm
            _guard_enabled = bool(getattr(
                _settings_sm, "org_question_task_guard", True,
            ))
        except Exception:
            _guard_enabled = True

        if _guard_enabled:
            raw_msg_type = args.get("msg_type", "question")
            content_preview = (args.get("content") or "")[:2000]
            org_for_guard = self._runtime.get_org(org_id)
            sender_has_children = False
            if org_for_guard:
                try:
                    sender_has_children = bool(
                        org_for_guard.get_children(node_id)
                    )
                except Exception:
                    sender_has_children = False
            if (
                raw_msg_type == "question"
                and sender_has_children
                and self._looks_like_task_assignment(content_preview)
            ):
                logger.info(
                    "[ToolHandler] block question-as-task by=%s to=%s",
                    node_id, args.get("to_node", ""),
                )
                return (
                    "[org_send_message 拦截] 检测到你正用 msg_type=question "
                    "向下属派发实际任务（含'撰写/优化/产出/完成'等任务措辞）。"
                    "这会绕过任务链跟踪，导致系统认为你的指令已完成而提前结束。"
                    "请改用 org_delegate_task 正式派发任务（一次只能派一个，"
                    "可并行多次调用），并在下属交付后用 org_accept_deliverable "
                    "验收。需要等下属交付时可调用 org_wait_for_deliverable。"
                )

        metadata: dict = {}

        # 若调用方当前绑定的 chain 已关闭，把 chain_closed 标记放进 metadata，
        # 供接收端 `_on_node_message` 做软门禁。不拦截发送本身，因为回复/总结
        # 这类对话性消息仍然有价值，只是不应再重新激活 ReAct。
        # 注意：仅在 chain 已关闭时才打 metadata，不对"开放中"的 chain 外泄 chain_id，
        # 以免把 sender 的 chain 语义传染给 receiver 的下一次 ReAct 调用。
        current_chain = self._runtime.get_current_chain_id(org_id, node_id)
        if current_chain and self._runtime.is_chain_closed(org_id, current_chain):
            metadata["task_chain_id"] = current_chain
            metadata["chain_closed"] = True

        # ── propagate_chain 接力 ──
        # delegate 误判时 LLM 可以走 send_message + propagate_chain=true 兜底，
        # 让接收方在 submit_deliverable 时使用同一 chain_id，整棵 chain 不断裂。
        # 已关闭的 chain 不接力（避免复活已结束的工作流）。
        propagate_chain = bool(args.get("propagate_chain", False))
        if propagate_chain:
            explicit_chain = (args.get("task_chain_id") or "").strip()
            relay_chain = explicit_chain or (current_chain or "")
            if relay_chain and not self._runtime.is_chain_closed(org_id, relay_chain):
                metadata["task_chain_id"] = relay_chain
                metadata["propagate_chain"] = True
                metadata["relay_from_node"] = node_id

        raw_type = args.get("msg_type", "question")
        try:
            msg_type = MsgType(raw_type)
        except ValueError:
            msg_type = MsgType.QUESTION
            logger.warning(f"[OrgToolHandler] Invalid msg_type '{raw_type}', falling back to 'question'")

        to_node = args.get("to_node", "")
        org = self._runtime.get_org(org_id)
        if org:
            caller_node = org.get_node(node_id)
            caller_label = (
                f"`{caller_node.id}`({caller_node.role_title})"
                if caller_node else f"`{node_id}`"
            )
            # 和 org_delegate_task 用同一套 resolve_reference 协议，确保
            # to_node 必须是反引号包住的精确节点 id 或完全相同的唯一 role_title；
            # 名字相近的模糊命中一律退到"请用精确 id"错误，避免把消息
            # 错发给同名同事（例如"产品总监"/"产品经理"的 substring 歧义）。
            resolved, candidates, status = org.resolve_reference(to_node)
            if status == "ambiguous_title":
                cand_list = ", ".join(
                    f"`{c.id}`({c.role_title})" for c in candidates
                )
                return (
                    f"[org_send_message 失败] 你是 {caller_label}，to_node='{to_node}' "
                    f"对应多个节点：{cand_list}。请改用上面列出的精确节点 id（反引号包住的那一个）。"
                )
            if status == "fuzzy":
                cand = candidates[0] if candidates else None
                cand_label = (
                    f"`{cand.id}`({cand.role_title})" if cand else f"'{to_node}'"
                )
                if cand and cand.id == node_id:
                    return (
                        f"[org_send_message 失败] 你是 {caller_label}，"
                        f"to_node='{to_node}' 模糊匹配到的是你自己（{cand_label}），不能给自己发消息。"
                        "请使用准确的目标节点 id。"
                    )
                return (
                    f"[org_send_message 失败] 你是 {caller_label}，to_node='{to_node}' "
                    f"不是精确匹配，最接近的是 {cand_label}。为避免误发，请把 to_node 改为 "
                    "上面建议的精确节点 id 再试。"
                )
            if status == "not_found":
                avail = ", ".join(f"{n.id}({n.role_title})" for n in org.nodes)
                return (
                    f"[org_send_message 失败] 你是 {caller_label}，节点 '{to_node}' 不存在。"
                    f"可用节点: {avail}"
                )

            to_node = resolved.id
            if to_node == node_id:
                return (
                    f"[org_send_message 失败] 你是 {caller_label}，不能给自己发消息。"
                )

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=to_node,
            msg_type=msg_type,
            content=args["content"],
            priority=args.get("priority", 0),
            metadata=metadata,
        )

        # 工具级在途锁：仅对 propagate_chain=true 的接力路径生效。
        # 普通 question/notify 等对话性消息不抢锁（避免影响正常多轮对话）。
        # 接力路径有效 chain_id 必须存在；否则 metadata 不会带 chain，messenger
        # 自身的 chain 级 dedupe 也不会启用，这条专门的工具锁就是必要兜底。
        relay_chain_for_lock = ""
        if propagate_chain and metadata.get("propagate_chain"):
            relay_chain_for_lock = (metadata.get("task_chain_id") or "").strip()
        _inflight_key = ""
        if relay_chain_for_lock:
            _inflight_key = (
                f"send_relay:{org_id}:{node_id}:{to_node}:{relay_chain_for_lock}"
            )
            if not self._runtime._try_acquire_tool_inflight(_inflight_key):
                logger.warning(
                    "[ToolInflight] dedupe drop org_send_message(propagate): %s",
                    _inflight_key,
                )
                return (
                    f"[去重] 检测到 {self._runtime._tool_inflight_window_secs:.0f}s 内"
                    f"已用 propagate_chain 接力同一任务链（{relay_chain_for_lock[:12]}）"
                    f"给 {to_node}，已忽略。请改用 org_wait_for_deliverable 等待结果。"
                )

        try:
            ok = await messenger.send(msg)
        except Exception:
            if _inflight_key:
                self._runtime._release_tool_inflight(_inflight_key)
            raise
        if not ok and _inflight_key:
            self._runtime._release_tool_inflight(_inflight_key)
        if ok:
            await self._runtime._broadcast_ws("org:message", {
                "org_id": org_id, "from_node": node_id, "to_node": to_node,
                "msg_type": args.get("msg_type", "question"),
                "content": args["content"][:_LIM_WS],
            })
            self._runtime._mark_effective_action(org_id, node_id)
            self._runtime._on_inbound_for_node(org_id, to_node)
        return f"消息已发送给 {to_node}" if ok else "发送失败"

    async def _handle_org_reply_message(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return self._org_not_running_error(org_id)
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
        self._runtime._mark_effective_action(org_id, node_id)
        self._runtime._on_inbound_for_node(org_id, to_node)
        return "已回复"

    async def _handle_org_delegate_task(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return self._org_not_running_error(org_id)

        org = self._runtime.get_org(org_id)

        # chain_id 计算策略（受 ``org_chain_parent_enforced`` flag 控制）：
        #   - flag=True（默认，新行为）：每次 delegate 一律新建子 chain，并把
        #     新 chain 挂到 caller 的 current_chain 之下作为父子关系，便于
        #     UserCommandTracker 沿子树关系判定真正的"全树关闭"。
        #   - flag=False（旧行为）：caller 已有 current_chain 时复用，整棵
        #     调用树共用一个 chain_id。这是出 bug 前的兼容路径。
        # LLM 显式传入的 ``task_chain_id`` 始终优先（用于"重派/续派"场景的
        # 主动指定 chain）。
        try:
            from openakita.config import settings as _settings_dt
            _chain_parent_enforced = bool(getattr(
                _settings_dt, "org_chain_parent_enforced", True,
            ))
        except Exception:
            _chain_parent_enforced = True

        caller_chain = self._runtime.get_current_chain_id(org_id, node_id)
        explicit_chain = args.get("task_chain_id") or None
        if explicit_chain:
            chain_id = explicit_chain
            parent_chain = caller_chain if caller_chain != chain_id else None
        elif _chain_parent_enforced:
            chain_id = _now_iso() + ":" + node_id[:8]
            parent_chain = caller_chain or None
        else:
            chain_id = caller_chain or (_now_iso() + ":" + node_id[:8])
            parent_chain = None

        # 软屏障：如果当前 chain 已被验收/打回/取消，禁止继续 delegate。
        # 这是防止"任务完成后组织继续自主派活"的核心拦截点之一。
        try:
            from openakita.config import settings as _settings
            if (getattr(_settings, "org_suppress_closed_chain_reactivation", True)
                    and self._runtime.is_chain_closed(org_id, chain_id)):
                logger.info(
                    "[ToolHandler] block delegate on closed chain=%s by=%s to=%s",
                    chain_id, node_id, args.get("to_node", ""),
                )
                return (
                    f"[已关闭] 任务链 {chain_id} 已结束（验收/打回/取消），"
                    "禁止基于该 chain 继续 org_delegate_task。"
                    "如确有新工作需要，请由上级重新发起独立任务；"
                    "当前请直接用文字总结回复，不要再调用任何 org_* 工具。"
                )
        except Exception as exc:
            logger.debug("delegate closed-chain check skipped: %s", exc)

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

        # task_affinity 的语义是"同一 chain 的后续消息路由到同一个 clone 实例"，
        # 它是给 messenger.send 用的（参见 messenger.send 里 affinity_node !=
        # to_node and != from_node 的反自指守卫）。在 delegate 这条路径上，
        # 之前把 to_node 无条件覆盖成 existing_affinity 会出现一个致命的
        # 自指：CEO 用 chain X 派给 CPO 后，affinity[X] = CPO；CPO 用同一个
        # chain X 继续向下派给 PM 时，to_node=pm 会被改写回 cpo，紧接着
        # 触发"不能把任务委派给自己"。
        # 这里只在三个条件同时满足时才走 affinity 改写：
        #   1) existing_affinity 不是 caller 自己（避免自指）
        #   2) existing_affinity 不是当前显式 to_node（无需改写）
        #   3) existing_affinity 与 to_node 同属一个 clone 组
        # 这样既保留了"clone 路由"的原意，又不会拦截上下游正常派活。
        existing_affinity = messenger.get_task_affinity(chain_id)
        if (
            existing_affinity
            and existing_affinity != node_id
            and existing_affinity != to_node
            and org
        ):
            affinity_node = org.get_node(existing_affinity)
            target_node = org.get_node(to_node)
            if (
                affinity_node
                and target_node
                and affinity_node.status not in (NodeStatus.FROZEN, NodeStatus.OFFLINE)
            ):
                same_clone_group = (
                    affinity_node.clone_source == target_node.id
                    or target_node.clone_source == affinity_node.id
                    or (
                        affinity_node.clone_source is not None
                        and affinity_node.clone_source == target_node.clone_source
                    )
                )
                if same_clone_group:
                    to_node = existing_affinity

        if org:
            # 便于错误消息里明确告诉 LLM 它自己是谁，避免 LLM 误以为"再试一次"就行
            caller_node = org.get_node(node_id)
            caller_label = (
                f"`{caller_node.id}`({caller_node.role_title})"
                if caller_node else f"`{node_id}`"
            )

            # _resolve_node_refs 在 strict 模式下只对 exact_id/exact_title 做了
            # 改写；fuzzy/ambiguous/not_found 都原样保留在 to_node 里，必须在
            # 这里用 resolve_reference 再跑一次严格解析，产出结构化错误，
            # 否则 LLM 根本不知道该用哪个精确节点 id。
            resolved, candidates, status = org.resolve_reference(to_node)
            children = org.get_children(node_id)
            children_hint = (
                "你的直属下级：" + ", ".join(
                    f"{c.role_title}(`{c.id}`)" for c in children
                )
                if children
                else "你是叶子节点，没有直属下级，无法使用 org_delegate_task。"
            )

            if status == "ambiguous_title":
                cand_list = ", ".join(
                    f"`{c.id}`({c.role_title})" for c in candidates
                )
                return (
                    f"[org_delegate_task 失败] 你是 {caller_label}，to_node='{to_node}' "
                    f"对应多个节点：{cand_list}。请改用上面列出的精确节点 id（反引号包住的那一个）再试一次。"
                    f"{children_hint}"
                )
            if status == "fuzzy":
                cand = candidates[0] if candidates else None
                cand_label = (
                    f"`{cand.id}`({cand.role_title})" if cand else f"'{to_node}'"
                )
                # 对自指（模糊匹配恰好命中调用者自己）单独提示，堵上最常见的
                # "产品总监把任务派给自己"死循环。
                if cand and cand.id == node_id:
                    return (
                        f"[org_delegate_task 失败] 你是 {caller_label}，"
                        f"to_node='{to_node}' 模糊匹配到的是你自己（{cand_label}），不能委派给自己。"
                        f"请改用下方列出的下级精确节点 id。{children_hint}"
                    )
                return (
                    f"[org_delegate_task 失败] 你是 {caller_label}，to_node='{to_node}' "
                    f"不是精确匹配，最接近的是 {cand_label}。为避免误派，请把 to_node 改为 "
                    f"上面建议的精确节点 id 再试。{children_hint}"
                )
            if status == "not_found":
                avail = ", ".join(f"{n.id}({n.role_title})" for n in org.nodes)
                return (
                    f"[org_delegate_task 失败] 你是 {caller_label}，目标节点 '{to_node}' 不存在。"
                    f"可用节点: {avail}。请检查 to_node 参数，或改用 org_submit_deliverable 自行完成。"
                )

            # exact_id / exact_title
            to_node = resolved.id

            # Validate hierarchy: only direct children can receive delegated tasks
            child_ids = {c.id for c in children}
            if to_node not in child_ids:
                if to_node == node_id:
                    # ── 生产级 trace：记录"自指误判"现场 ──
                    # pytest 已经证明 static path 不会让 to_node 误指回 caller
                    # （详见 tests/orgs/test_org_delegate_self_misjudge_repro.py）。
                    # 这条路径在生产里仍然偶发触发，说明是 dynamic 状态污染，
                    # 必须把现场所有可疑变量 dump 到 ERROR 日志，下次拿到日志
                    # 即可定位真正的注入路径（args 改写者、affinity 路由、
                    # clone 关系、外部 hook 等）。
                    try:
                        affinity_dump = messenger.get_task_affinity(chain_id) \
                            if messenger else None
                        caller_node_obj = org.get_node(node_id) if org else None
                        clone_src = (
                            caller_node_obj.clone_source if caller_node_obj else None
                        )
                        children_dump = [c.id for c in children] if children else []
                        logger.error(
                            "[delegate-self-misjudge] caller=%s resolved_to_node=%s "
                            "raw_args=%r resolve_status=%s candidates=%s "
                            "chain_id=%s caller_chain=%s explicit_chain=%s "
                            "chain_depth=%s affinity=%s clone_source=%s "
                            "children=%s",
                            node_id, to_node, args, status,
                            [c.id for c in candidates],
                            chain_id, caller_chain, explicit_chain,
                            chain_depth, affinity_dump, clone_src,
                            children_dump,
                        )
                    except Exception:
                        logger.error(
                            "[delegate-self-misjudge] dump failed",
                            exc_info=True,
                        )
                    hint = (
                        f"[org_delegate_task 失败] 你就是 {caller_label}，不能把任务委派给自己。"
                    )
                else:
                    target_node = org.get_node(to_node)
                    target_label = (
                        f"`{target_node.id}`({target_node.role_title})"
                        if target_node else f"`{to_node}`"
                    )
                    hint = (
                        f"[org_delegate_task 失败] 你是 {caller_label}，"
                        f"{target_label} 不是你的直属下级，无法委派给它。"
                    )
                if children:
                    child_list = ", ".join(f"{c.role_title}(`{c.id}`)" for c in children)
                    # 自指分支额外提示 send_message + propagate_chain 兜底，给 LLM
                    # 一条独立于 delegate 的出口，避免反复重试 delegate 把组织
                    # 卡死在 Supervisor 看门狗的死循环判定里。
                    fallback_hint = ""
                    if to_node == node_id and chain_id:
                        fallback_hint = (
                            f" 如确认目标节点合法（参考组织结构里反引号包住的精确 id），"
                            f"且 delegate 反复失败，可临时改用 "
                            f"org_send_message(to_node=<下属id>, content=<任务>, "
                            f"propagate_chain=true, task_chain_id=\"{chain_id}\") "
                            f"把当前 chain 接力下去，下属交付时会沿用同一 task_chain_id。"
                        )
                    return (
                        f"{hint} 你的直属下级只有：{child_list}。"
                        f"如果任务本就该由你自己完成，请改用 org_submit_deliverable 交付成果；"
                        f"不要反复调用 org_delegate_task，否则会被 Supervisor 判定死循环并终止。"
                        f"{fallback_hint}"
                    )
                return (
                    f"{hint} 你是叶子节点，没有直属下级，根本无法使用 org_delegate_task。"
                    f"请直接调用 org_submit_deliverable 把任务结果交付给你的上级；"
                    f"若需协作可用 org_send_message。禁止继续重试 org_delegate_task。"
                )

        try:
            from openakita.orgs.models import TaskStatus as _TS
            from openakita.orgs.project_store import ProjectStore
            _store = ProjectStore(self._runtime._manager._org_dir(org_id))
            _existing = _store.find_task_by_chain(chain_id)
            if (_existing
                    and _existing.assignee_node_id == to_node
                    and _existing.status in (_TS.IN_PROGRESS, _TS.DELIVERED)):
                return (
                    f"{to_node} 已在处理此任务链（{chain_id[:12]}），无需重复委派。"
                    f"请用 org_list_delegated_tasks 查看进度。"
                )
        except Exception:
            pass

        # 工具级在途锁：堵住 ProjectStore 还没落盘前 LLM 在同一 ReAct iter
        # 内 emit 多个 delegate_task 给同 chain 同目标的并发穿透。
        # 失败路径下立即释放锁让后续重试可继续；成功路径让锁自然过期，
        # 窗口内紧接的重发会得到友好提示而不是真的二次入队。
        _inflight_key = f"delegate:{org_id}:{node_id}:{to_node}:{chain_id}"
        if not self._runtime._try_acquire_tool_inflight(_inflight_key):
            logger.warning(
                "[ToolInflight] dedupe drop org_delegate_task: %s", _inflight_key
            )
            return (
                f"[去重] 检测到 {self._runtime._tool_inflight_window_secs:.0f}s 内"
                f"重复对 {to_node} 派发同一任务链（{chain_id[:12]}），已忽略。"
                f"请改用 org_wait_for_deliverable 等待交付，或 org_list_delegated_tasks 查看进度。"
            )

        # BUG-3：把"用户原始指令"做成父任务硬边界，前置到子任务 task_content 里。
        # 仅当用户原话带显式约束（字数限制、不要写代码、只列纲要等）才注入，
        # 普通任务不附加边界，零行为破坏。
        _task_content = args["task"]
        try:
            _root_intent = self._runtime.get_active_root_intent(org_id)
            if _root_intent and _has_explicit_boundary(_root_intent):
                _intent_brief = _root_intent.strip()
                if len(_intent_brief) > 300:
                    _intent_brief = _intent_brief[:300] + "..."
                _task_content = (
                    "[父任务硬边界 — 禁止超出]\n"
                    f"用户原始指令：{_intent_brief}\n"
                    "你的产出必须严格遵守该指令的范围、字数、格式约束。"
                    "若上级转述时与原始指令冲突，以原始指令为准。\n\n"
                    "—— 上级派发的子任务如下 ——\n"
                    + str(args["task"])
                )
        except Exception:
            logger.debug(
                "[ToolHandler] root_intent boundary inject failed",
                exc_info=True,
            )
            _task_content = args["task"]

        try:
            await messenger.send_task(
                from_node=node_id,
                to_node=to_node,
                task_content=_task_content,
                priority=args.get("priority", 0),
                metadata=metadata,
            )
        except Exception:
            self._runtime._release_tool_inflight(_inflight_key)
            raise

        self._runtime._mark_effective_action(org_id, node_id)
        self._runtime._on_inbound_for_node(org_id, to_node)
        messenger.bind_task_affinity(chain_id, to_node)
        self._runtime._chain_delegation_depth[chain_id] = chain_depth + 1

        # 维护 chain 父子关系（org_chain_parent_enforced 路径下使用）。
        # parent_chain 在上面的 chain_id 计算分支里已经决定：caller 已有
        # current_chain 且本次新建子 chain 时 = caller_chain，其它路径 = None。
        try:
            if parent_chain and parent_chain != chain_id:
                self._runtime._chain_parent.setdefault(chain_id, parent_chain)
            else:
                self._runtime._chain_parent.setdefault(chain_id, None)
        except Exception:
            logger.debug(
                "[ToolHandler] chain_parent register failed", exc_info=True,
            )

        # 注册一个 chain 关闭事件，供 org_wait_for_deliverable 阻塞等待。
        # 同一 chain 重复 delegate 时复用既有 event。
        try:
            if chain_id not in self._runtime._chain_events:
                self._runtime._chain_events[chain_id] = asyncio.Event()
        except Exception:
            logger.debug(
                "[ToolHandler] chain_event create failed", exc_info=True,
            )

        # 用户命令生命周期追踪：如果当前 org 上存在进行中的 UserCommandTracker
        # 且本次派工源自 tracker 的 root 或其后代，则把新 chain 登记进 tracker，
        # 作为"该命令尚未完成"的信号之一。关闭时由 _mark_chain_closed 反向解注册。
        try:
            register = getattr(self._runtime, "_tracker_register_chain", None)
            if callable(register):
                register(org_id, node_id, chain_id)
        except Exception:
            logger.debug(
                "[ToolHandler] tracker_register_chain failed",
                exc_info=True,
            )

        self._runtime.get_event_store(org_id).emit(
            "task_assigned", node_id,
            {"to": to_node, "task": args["task"][:_LIM_EVENT], "chain_id": chain_id},
        )
        await self._runtime._broadcast_ws("org:task_delegated", {
            "org_id": org_id, "from_node": node_id, "to_node": to_node,
            "task": args["task"][:_LIM_WS], "chain_id": chain_id,
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
            title=args["task"][:_LIM_TITLE],
            assignee=to_node,
            delegated_by=node_id,
            status="in_progress",
            parent_task_id=parent_task_id,
            depth=depth,
        )
        self._append_execution_log(
            org_id, chain_id,
            f"委派给 {to_node}: {args['task'][:_LIM_EXEC_LOG]}",
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
            return self._org_not_running_error(org_id)

        result = await messenger.escalate(
            node_id, args["content"], priority=args.get("priority", 1),
            metadata={},
        )
        if result:
            await self._runtime._broadcast_ws("org:escalation", {
                "org_id": org_id, "from_node": node_id,
                "to_node": result.to_node if hasattr(result, "to_node") else "",
                "content": args["content"][:_LIM_WS],
            })
            self._runtime._mark_effective_action(org_id, node_id)
            try:
                to_node_e = result.to_node if hasattr(result, "to_node") else ""
                if to_node_e:
                    self._runtime._on_inbound_for_node(org_id, to_node_e)
            except Exception:
                pass
            return "已上报给上级"
        return "无法上报（没有上级节点）"

    async def _handle_org_broadcast(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return self._org_not_running_error(org_id)
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
            "content": args["content"][:_LIM_WS],
        })
        self._runtime.get_event_store(org_id).emit(
            "broadcast", node_id,
            {"scope": scope, "content": args["content"][:_LIM_EVENT]},
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
                "goal": n.role_goal[:_LIM_TOOL_RETURN] if n.role_goal else "",
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
            tags=args.get("tags", []),
            importance=args.get("importance", 0.5),
        )
        if entry is None:
            return f"黑板已有相似内容，跳过重复写入: {args['content'][:50]}"
        await self._runtime._broadcast_ws("org:blackboard_update", {
            "org_id": org_id, "scope": "org", "node_id": node_id,
            "memory_type": args.get("memory_type", "fact"),
            "content": args["content"][:_LIM_WS],
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
            tags=args.get("tags", []),
            importance=args.get("importance", 0.5),
        )
        if entry is None:
            return "部门记忆已有相似内容，跳过重复写入"
        await self._runtime._broadcast_ws("org:blackboard_update", {
            "org_id": org_id, "scope": "department", "department": dept,
            "node_id": node_id, "memory_type": args.get("memory_type", "fact"),
            "content": args["content"][:_LIM_WS],
        })
        return f"已写入 {dept} 部门记忆"

    # ------------------------------------------------------------------
    # Node-level memory tools
    # ------------------------------------------------------------------

    async def _handle_org_read_node_memory(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        if not bb:
            return "黑板不可用"
        entries = bb.read_node(node_id, limit=args.get("limit", 10))
        if not entries:
            return "(暂无私有记忆)"
        return "\n".join(f"[{e.memory_type.value}] {e.content}" for e in entries)

    async def _handle_org_write_node_memory(
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
        bb.write_node(
            node_id,
            content=args["content"],
            memory_type=mt,
            tags=args.get("tags", []),
            importance=args.get("importance", 0.5),
        )
        await self._runtime._broadcast_ws("org:blackboard_update", {
            "org_id": org_id, "scope": "node", "node_id": node_id,
            "memory_type": raw_mt,
            "content": args["content"][:_LIM_WS],
        })
        return f"已写入私有记忆: {args['content'][:50]}"

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
                        lines = [ln for ln in content.split("\n") if query in ln.lower()][:3]
                        results.append(f"📄 {f.name}\n" + "\n".join(f"  > {ln.strip()}" for ln in lines))
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
        if node_id != "user":
            caller = org.get_node(node_id)
            if not caller:
                return "你不在此组织中"
            roots = org.get_root_nodes()
            if caller.level >= target.level and (not roots or node_id != roots[0].id):
                return "只能冻结比你层级低的节点"
        target.frozen_by = node_id
        target.frozen_reason = args.get("reason", "")
        target.frozen_at = _now_iso()
        await self._runtime.set_node_status(
            org, target, NodeStatus.FROZEN, "freeze", current_task="",
        )
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
        target.frozen_by = None
        target.frozen_reason = None
        target.frozen_at = None
        self._runtime._node_consecutive_failures.pop(f"{org_id}:{target_id}", None)
        await self._runtime.set_node_status(
            org, target, NodeStatus.IDLE, "unfreeze", current_task="",
        )
        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            messenger.unfreeze_mailbox(target.id)
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
            return self._org_not_running_error(org_id)

        to_node = args.get("to_node", "")
        deliverable = args.get("deliverable", "")
        summary = args.get("summary", "")
        raw_file_attachments = args.get("file_attachments") or []

        # chain_id 强制策略（org_chain_parent_enforced=True 时启用）：
        # submit 时必须使用 caller 当前 incoming chain（即上级派给我时的 chain）。
        # 这是修复"content-op submit 时 LLM 漏传 task_chain_id 导致开新链、
        # 整树 chain 关系断裂"的关键。LLM 传错时用 caller current_chain 强制覆盖
        # 并 warn；caller 没有 current_chain 时 fall back 到 LLM 传值或新 chain
        # （保持旧兼容路径，例如 root 节点意外调 submit 的边缘场景）。
        try:
            from openakita.config import settings as _settings_sd
            _enforce_sd = bool(getattr(
                _settings_sd, "org_chain_parent_enforced", True,
            ))
        except Exception:
            _enforce_sd = True

        explicit_chain_sd = args.get("task_chain_id") or None
        caller_chain_sd = self._runtime.get_current_chain_id(org_id, node_id)
        if _enforce_sd and caller_chain_sd:
            if explicit_chain_sd and explicit_chain_sd != caller_chain_sd:
                logger.warning(
                    "[ToolHandler] submit_deliverable chain_id mismatch: "
                    "node=%s LLM_passed=%s overridden_to=%s",
                    node_id, explicit_chain_sd, caller_chain_sd,
                )
            chain_id = caller_chain_sd
        else:
            chain_id = explicit_chain_sd or _now_iso()

        if not to_node:
            org = self._runtime.get_org(org_id)
            if org:
                parent = org.get_parent(node_id)
                if parent:
                    to_node = parent.id
        if not to_node:
            return (
                "你是组织最高负责人，没有上级节点可提交。"
                "你的执行结果会自动返回给指挥者，无需使用 org_submit_deliverable。"
                "请直接在回复中总结成果即可。"
            )

        # 幂等性拦截：同一 chain 已被验收(accepted) / 已被打回(rejected)时，
        # 拒绝再次提交，避免出现"两份一模一样的交付物/附件"以及父级被再次唤醒。
        # 注意：已 delivered 但未验收不拦截（允许 agent 补交修订版，由下游去重兜底）。
        try:
            from openakita.config import settings as _settings
            if getattr(_settings, "org_reject_resubmit_after_accept", True) and chain_id:
                events = self._runtime.get_event_store(org_id)
                if events:
                    recent_acc = events.query(event_type="task_accepted", limit=50)
                    for ev in recent_acc:
                        if ev.get("data", {}).get("chain_id") == chain_id:
                            logger.info(
                                "[ToolHandler] reject resubmit on closed chain=%s by=%s",
                                chain_id, node_id,
                            )
                            return (
                                f"[已关闭] 任务链 {chain_id} 已被验收通过，不能再次提交交付物。"
                                "如有新的增量成果，请作为独立任务重新发起或直接在回复中总结，"
                                "不要再调用 org_submit_deliverable/org_delegate_task。"
                            )
                    recent_rej = events.query(event_type="task_rejected", limit=50)
                    for ev in recent_rej:
                        if ev.get("data", {}).get("chain_id") == chain_id:
                            # rejected 仍允许重新 submit 修正版本（这正是 rejected 的语义）
                            break
        except Exception as exc:
            logger.debug("submit-idempotency check skipped: %s", exc)

        # 把显式声明的 file_attachments 全部登记到黑板 + ProjectTask。
        # 使用 runtime._register_file_output 作为唯一登记入口，确保和
        # write_file / generate_image / deliver_artifacts 共用一条路径
        # （避免双写黑板条目）。registered_attachments 里只保留登记成功
        # 的条目（路径存在 + 黑板可写），随 TASK_DELIVERED 送到父节点。
        registered_attachments: list[dict] = []
        if isinstance(raw_file_attachments, list) and raw_file_attachments:
            try:
                org_for_ws = self._runtime.get_org(org_id)
                workspace = (
                    self._runtime._resolve_org_workspace(org_for_ws)
                    if org_for_ws else None
                )
            except Exception:
                workspace = None
            for att in raw_file_attachments:
                if not isinstance(att, dict):
                    continue
                fp = att.get("file_path") or att.get("path")
                if not fp:
                    continue
                try:
                    registered = self._runtime._register_file_output(
                        org_id, node_id,
                        chain_id=chain_id or None,
                        filename=att.get("filename"),
                        file_path=fp,
                        workspace=workspace,
                    )
                except Exception:
                    logger.debug(
                        "submit-deliverable register_file_output failed",
                        exc_info=True,
                    )
                    registered = None
                if registered:
                    registered_attachments.append(registered)
                else:
                    logger.info(
                        "[ToolHandler] submit_deliverable skipped unregistrable "
                        "attachment: %s (file missing?)", fp,
                    )

        # 自动附件兜底：CPO/PM 这类不带 filesystem 工具的角色，常常把整段
        # markdown 长文塞进 deliverable 字段，前端只能看到聊天里一段长文，
        # 没法点附件下载，也不进黑板。这里在没有任何显式 file_attachments
        # 且 deliverable 看起来是结构化文档（含 markdown 标题/列表/代码块）
        # 且字符数达到下限时，自动落盘到
        # `<workspace>/deliverables/<chain_short>_<title>.md`，再走和
        # write_file/generate_image 一样的 _register_file_output 唯一登记入口
        # （runtime.py），保证不出现"双写黑板"。任何异常只 warning，不影响
        # 原 submit_deliverable 主流程。
        deliverable_stripped = (deliverable or "").strip()
        should_auto_persist = (
            not registered_attachments
            and deliverable_stripped
            and len(deliverable_stripped) >= self._DELIVERABLE_AUTO_PERSIST_MIN_CHARS
            and self._looks_like_structured_document(deliverable_stripped)
        )
        if should_auto_persist:
            try:
                org_for_auto = self._runtime.get_org(org_id)
                workspace_auto = (
                    self._runtime._resolve_org_workspace(org_for_auto)
                    if org_for_auto else None
                )
                if workspace_auto is not None:
                    auto_path = self._auto_persist_deliverable(
                        workspace=workspace_auto,
                        chain_id=chain_id,
                        title=summary or args.get("task_title") or "deliverable",
                        body=deliverable,
                    )
                    if auto_path is not None:
                        try:
                            registered = self._runtime._register_file_output(
                                org_id, node_id,
                                chain_id=chain_id or None,
                                filename=auto_path.name,
                                file_path=str(auto_path),
                                workspace=workspace_auto,
                            )
                        except Exception:
                            logger.warning(
                                "submit-deliverable auto-attachment register failed",
                                exc_info=True,
                            )
                            registered = None
                        if registered:
                            registered_attachments.append(registered)
                            logger.info(
                                "[ToolHandler] auto-persisted deliverable to %s "
                                "(node=%s chain=%s len=%d)",
                                auto_path, node_id, chain_id,
                                len(deliverable),
                            )
            except Exception:
                logger.warning(
                    "submit-deliverable auto-attachment persist failed",
                    exc_info=True,
                )

        metadata: dict = {
            "deliverable": deliverable[:2000],
            "summary": summary[:500],
            "task_chain_id": chain_id,
        }
        if registered_attachments:
            metadata["file_attachments"] = registered_attachments

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=to_node,
            msg_type=MsgType.TASK_DELIVERED,
            content=f"任务交付: {deliverable[:_LIM_EVENT]}",
            metadata=metadata,
        )

        # 工具级在途锁：拦掉 LLM 在同一 ReAct iter emit 多个 submit_deliverable
        # 给同一 chain 同一上级造成的"附件出现 N 份、父级被多次唤醒"。
        # messenger.send 内部已有内容 hash 级 dedupe 兜底；这里加一层是为了
        # 给 LLM 返回明确的友好文案，让它不会因为得到 False 又紧接重试。
        _inflight_key = f"submit:{org_id}:{node_id}:{to_node}:{chain_id}"
        if not self._runtime._try_acquire_tool_inflight(_inflight_key):
            logger.warning(
                "[ToolInflight] dedupe drop org_submit_deliverable: %s", _inflight_key
            )
            return (
                f"[去重] 检测到 {self._runtime._tool_inflight_window_secs:.0f}s 内"
                f"已向 {to_node} 提交过同一任务链（{chain_id[:12]}）的交付物，已忽略。"
                f"请等待上级验收（org_wait_for_acceptance / 直接结束本轮回复）。"
            )

        try:
            ok = await messenger.send(msg)
        except Exception:
            self._runtime._release_tool_inflight(_inflight_key)
            raise
        if not ok:
            # send 被 messenger 内部 dedupe / bandwidth / target-not-found 拒绝时，
            # 立即释放锁让后续合理的重试可以继续；让 LLM 拿到明确反馈。
            self._runtime._release_tool_inflight(_inflight_key)

        self._runtime.get_event_store(org_id).emit(
            "task_delivered", node_id,
            {
                "to": to_node, "chain_id": chain_id,
                "deliverable_preview": deliverable[:_LIM_EVENT],
                "file_count": len(registered_attachments),
            },
        )

        if ok:
            await self._runtime._broadcast_ws("org:task_delivered", {
                "org_id": org_id, "from_node": node_id, "to_node": to_node,
                "chain_id": chain_id, "summary": summary[:_LIM_WS],
            })
            self._link_project_task(
                org_id, chain_id, status="delivered",
                deliverable_content=deliverable[:2000],
                delivery_summary=summary[:500],
            )
            self._recalc_parent_progress(org_id, chain_id)
            self._append_execution_log(
                org_id, chain_id,
                f"提交交付物给 {to_node}: {summary[:_LIM_EXEC_LOG]}",
                node_id,
            )
            self._runtime._mark_effective_action(org_id, node_id)
            self._runtime._on_inbound_for_node(org_id, to_node)
            tail = (
                f"（附带 {len(registered_attachments)} 个文件附件）"
                if registered_attachments else ""
            )
            return f"交付物已提交给 {to_node}{tail}，等待验收。"
        return "提交失败"

    async def _handle_org_accept_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return self._org_not_running_error(org_id)

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
            content=f"验收通过: {feedback[:_LIM_EVENT]}",
            metadata=metadata,
        )
        await messenger.send(msg)

        if chain_id:
            # 旧行为保留（messenger.release_task_affinity + chain_delegation_depth 清理）
            # 由 _cleanup_accepted_chain 统一承担；此处仍显式调用以保证即便 cleanup 被禁用
            # (未来扩展) 也不会退化为泄漏。
            messenger.release_task_affinity(chain_id)
            self._runtime._chain_delegation_depth.pop(chain_id, None)
            try:
                self._runtime._cleanup_accepted_chain(
                    org_id, chain_id, reason="accepted",
                )
            except Exception as exc:
                logger.debug("cleanup_accepted_chain on accept failed: %s", exc)

        self._runtime.get_event_store(org_id).emit(
            "task_accepted", node_id,
            {"from": from_node, "chain_id": chain_id},
        )
        await self._runtime._broadcast_ws("org:task_accepted", {
            "org_id": org_id, "from_node": from_node, "accepted_by": node_id,
            "chain_id": chain_id, "feedback": feedback[:_LIM_WS],
        })
        relayed_files: list[dict] = []
        if chain_id:
            self._link_project_task(org_id, chain_id, status="accepted")
            self._append_execution_log(
                org_id, chain_id, f"验收通过: {feedback[:_LIM_EXEC_LOG]}", node_id,
            )
            self._recalc_parent_progress(org_id, chain_id)

            try:
                from openakita.orgs.project_store import ProjectStore as _PS
                _store = _PS(self._runtime._manager._org_dir(org_id))
                _child = _store.find_task_by_chain(chain_id)
                if _child:
                    _child_files = getattr(_child, "file_attachments", None) or []
                    if _child_files:
                        relayed_files = [dict(f) for f in _child_files]
                    if _child.parent_task_id and _child_files:
                        _parent, _ = _store.get_task(_child.parent_task_id)
                        if _parent:
                            _merged = self._merge_file_attachments(
                                list(getattr(_parent, "file_attachments", None) or []),
                                list(_child_files),
                            )
                            _store.update_task(
                                _parent.project_id, _parent.id,
                                {"file_attachments": _merged},
                            )
            except Exception:
                pass

        bb = self._runtime.get_blackboard(org_id)
        if bb:
            bb.write_org(
                content=f"任务验收通过 [{chain_id[:8] if chain_id else ''}]: {feedback[:_LIM_EVENT]}",
                source_node=node_id,
                memory_type=MemoryType.PROGRESS,
                tags=["acceptance", "completed"],
            )

        # 返回结构化 JSON，对齐 deliver_artifacts 的 receipts 协议。
        # reasoning_engine 会解析 receipts 进 delivery_receipts，让
        # TaskVerify 认可"中继交付"——即父节点自己没调用 deliver_artifacts，
        # 但子节点已经把文件交上来并被父节点 accept 的场景。
        receipts = [
            {
                "status": "relayed",
                "filename": f.get("filename", ""),
                "file_path": f.get("file_path", ""),
                "file_size": f.get("file_size"),
                "source_node": from_node,
            }
            for f in relayed_files
        ]
        payload = {
            "ok": True,
            "accepted_from": from_node,
            "chain_id": chain_id,
            "receipts": receipts,
            "message": f"已验收 {from_node} 的交付物。",
        }
        return json.dumps(payload, ensure_ascii=False)

    async def _handle_org_reject_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return self._org_not_running_error(org_id)

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
            content=f"任务打回: {reason[:_LIM_EVENT]}",
            metadata=metadata,
        )
        await messenger.send(msg)

        self._runtime.get_event_store(org_id).emit(
            "task_rejected", node_id,
            {"from": from_node, "chain_id": chain_id, "reason": reason[:_LIM_EVENT]},
        )
        await self._runtime._broadcast_ws("org:task_rejected", {
            "org_id": org_id, "from_node": from_node, "rejected_by": node_id,
            "chain_id": chain_id, "reason": reason[:_LIM_WS],
        })
        if chain_id:
            self._link_project_task(org_id, chain_id, status="rejected")
            self._append_execution_log(
                org_id, chain_id,
                f"打回: {reason[:_LIM_EXEC_LOG]}",
                node_id,
            )
            self._recalc_parent_progress(org_id, chain_id)
            # rejected 也需要清理：让下游 agent 不会再用旧 chain 继续送交付物；
            # 但不级联 cancel 子任务（rejected 意味着重做，可能仍依赖子任务结果）。
            try:
                self._runtime._cleanup_accepted_chain(
                    org_id, chain_id, reason="rejected",
                    cascade_cancel_children=False,
                )
            except Exception as exc:
                logger.debug("cleanup_accepted_chain on reject failed: %s", exc)

        return f"已打回 {from_node} 的交付物，原因：{reason[:50]}"

    async def _handle_org_wait_for_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        """阻塞等待下级任务交付，避免 org_list_delegated_tasks 轮询触发死循环。

        多事件 wait 防止死锁：
          - 任一指定 chain 关闭（被 accept/reject/cancel）
          - 节点 inbox 收到 question/escalate（需要 coordinator 立即处理）
          - timeout 到期（默认 60s，最大 300s）
          - 整个组织被 soft-stop / 命令被取消
        所有路径出口都会 ``_touch_trackers_for_org``，避免命令看门狗误判。
        """
        try:
            from openakita.config import settings as _s_wait
            if not getattr(_s_wait, "org_wait_primitive_enabled", True):
                return (
                    "[org_wait_for_deliverable 已禁用] "
                    "请改用 org_list_delegated_tasks 查询进度。"
                )
        except Exception:
            pass

        try:
            timeout = int(args.get("timeout") or 60)
        except (TypeError, ValueError):
            timeout = 60
        timeout = max(1, min(300, timeout))

        runtime = self._runtime
        my_chain = runtime.get_current_chain_id(org_id, node_id)
        explicit_chains_raw = args.get("chain_ids")
        if isinstance(explicit_chains_raw, list):
            explicit_chains = [
                c for c in explicit_chains_raw if isinstance(c, str) and c
            ]
        else:
            explicit_chains = []

        if explicit_chains:
            target_chains = explicit_chains
        else:
            # 反查 _chain_parent：所有以 my_chain 为父的子 chain
            target_chains = [
                c for c, p in runtime._chain_parent.items() if p == my_chain
            ]

        # 过滤掉已关闭的 chain（不再有意义）
        open_targets = [
            c for c in target_chains
            if not runtime.is_chain_closed(org_id, c)
        ]
        if not open_targets:
            return (
                "没有需要等待的未关闭子链。可能下级已全部交付——"
                "请检查 inbox 中的 deliverable 消息后用 org_accept_deliverable 验收，"
                "或调用 org_list_delegated_tasks 确认状态。"
            )

        # 准备 chain events（缺失时按需补建）
        chain_events: list[tuple[str, asyncio.Event]] = []
        for c in open_targets:
            ev = runtime._chain_events.get(c)
            if ev is None:
                ev = asyncio.Event()
                runtime._chain_events[c] = ev
            chain_events.append((c, ev))

        # 节点 inbox 事件：每次 wait 调用都重置，只关心"等待期内"的新消息
        inbox_key = f"{org_id}:{node_id}"
        inbox_event = runtime._node_inbox_events.get(inbox_key)
        if inbox_event is None:
            inbox_event = asyncio.Event()
            runtime._node_inbox_events[inbox_key] = inbox_event
        inbox_event.clear()

        runtime._touch_trackers_for_org(org_id)

        waiters: list[asyncio.Task] = []
        for c, ev in chain_events:
            waiters.append(
                asyncio.create_task(ev.wait(), name=f"wait_chain:{c[:24]}")
            )
        waiters.append(
            asyncio.create_task(inbox_event.wait(), name=f"wait_inbox:{node_id}")
        )

        try:
            done, _pending = await asyncio.wait(
                waiters, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for w in waiters:
                if not w.done():
                    w.cancel()
            for w in waiters:
                try:
                    await w
                except (asyncio.CancelledError, Exception):
                    pass

        runtime._touch_trackers_for_org(org_id)

        # 复检 chain 状态（asyncio.wait 返回时可能已有多个 chain 同时关闭）
        closed_chains_now = [
            c for c, _ in chain_events
            if runtime.is_chain_closed(org_id, c)
        ]
        inbox_triggered = inbox_event.is_set()

        if not done:
            return (
                f"[等待超时] {timeout}s 内未收到下级新交付/新消息。"
                f"未关闭子链：{open_targets[:5]}{'...' if len(open_targets) > 5 else ''}。"
                "建议：用 org_list_delegated_tasks 查看具体进度，"
                "或继续 org_wait_for_deliverable 再等一轮；"
                "若已等待较久且确实需要推进，可向用户输出阶段性汇总。"
            )

        parts: list[str] = []
        if closed_chains_now:
            preview = closed_chains_now[:5]
            extra = "..." if len(closed_chains_now) > 5 else ""
            parts.append(
                f"以下子链已关闭，请检查相关 deliverable：{preview}{extra}"
            )
        if inbox_triggered:
            parts.append(
                "下级有新消息（question/escalate）需要你立即响应——"
                "请先处理 inbox 中的消息，处理完可继续 org_wait_for_deliverable 等剩余子链。"
            )
        if not parts:
            parts.append(
                "[wait 已返回] 未识别到具体事件来源，可能是命令被取消或事件被竞态消化。"
                "请检查组织状态后决定下一步。"
            )
        return " | ".join(parts)

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
                    "round": round_num, "content": response[:_LIM_WS],
                })

            prev_round_summary = "\n".join(round_opinions)

        conclusion = await self._meeting_summarize(org_id, topic, meeting_record)
        if conclusion:
            meeting_record.append(f"\n### 会议结论\n\n{conclusion}")

        bb = self._runtime.get_blackboard(org_id)
        if bb:
            summary_text = conclusion or meeting_record[-1][:_LIM_EVENT]
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
            body=f"任务指令: {args['prompt'][:_LIM_WS]}\n类型: {args.get('schedule_type', 'interval')}",
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
                log_entry = {"at": _now_iso(), "by": node_id, "entry": args["log_entry"][:_LIM_EXEC_LOG]}
                new_log = list(existing.execution_log or []) + [log_entry]
                updates["execution_log"] = new_log
            if updates.get("progress_pct", 0) >= 100 and str(existing.status) == "in_progress":
                from openakita.orgs.models import TaskStatus
                updates["status"] = TaskStatus.DELIVERED
            if updates:
                store.update_task(existing.project_id, existing.id, updates)
            msg = f"已汇报进度: {updates.get('progress_pct', '')}%"
            if "status" in updates:
                msg += f" (状态已自动更新为 {updates['status'].value})"
            return msg
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
            return list(tasks[:limit])
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
            return list(tasks[:limit])
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
            from openakita.orgs.models import TaskStatus
            from openakita.orgs.project_store import ProjectStore

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
                        entry = e if isinstance(e, dict) else {"at": _now_iso(), "by": node_id, "entry": str(e)[:_LIM_EXEC_LOG]}
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
            from openakita.orgs.models import ProjectTask, TaskStatus
            from openakita.orgs.project_store import ProjectStore

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
                title=title[:_LIM_TITLE],
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

