"""
OrgToolHandler — Organization tool executor

Handles the org_* tools invoked by organization-node Agents.
Each handler method receives tool_name, arguments, context(org_id, node_id) and returns a result.
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
        """Return different error messages based on whether the org was recently explicitly stopped/deleted.

        - If the org was recently explicitly stopped/deleted: return "organization stopped, task cancelled"
          so the LLM knows this is a terminal state and should not retry.
        - Otherwise (org inactive, id not found, etc.): return the original "organization not running".
        """
        try:
            if self._runtime.is_org_recently_stopped(org_id):
                return (
                    "[Organization stopped] The organization has been stopped or deleted, and the current task has been cancelled. "
                    "Stop calling any org_* tools and reply directly to the user with a textual summary stating the task has been terminated."
                )
        except Exception:
            pass
        return "Organization not running"

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
          prevents the "Product Director" vs. "Product Manager" substring collision from
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

    # Filename sanitisation: strip path separators, control characters, and platform-reserved
    # characters to prevent LLM-supplied titles containing ../ or :*?"<>| from escaping
    # the workspace directory via path traversal.
    _DELIVERABLE_NAME_FORBIDDEN = set('\\/:*?"<>|\r\n\t')

    # Minimum character count for auto-persisting a deliverable to disk.
    # Shorter strings are typically conversational replies ("Done"), which add noise when
    # materialised as attachments. Real LLM-authored documents (with markdown headings or
    # lists) are generally >= 300 characters; empirical cases ~476 characters.
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
        "Done" do not trigger auto-persist.
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
        from pathlib import Path
        from datetime import datetime

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

        header = f"# {title.strip() or 'Deliverable'}\n\n" if title else ""
        try:
            candidate.write_text(header + (body or ""), encoding="utf-8")
        except Exception:
            return None
        return candidate

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
                    name="Task Tracking",
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
                    f"Plan created: {tool_input.get('task_summary', '')[:_LIM_EXEC_LOG]}",
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
                    f"Step {step_id}: {status} - {result_text[:_LIM_EXEC_LOG]}",
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
                    f"Plan completed: {summary[:_LIM_EXEC_LOG]}",
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

        # Each org_* tool call is a progress signal indicating "organization is active",
        # used to prevent the command watchdog from falsely flagging it as stuck.
        # O(0) for orgs without an active UserCommandTracker.
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

    # ── Coordinator antipattern heuristic guard ──
    # Coordinators (nodes with direct reports) often mistakenly use
    # ``org_send_message(question)`` to hand tasks to subordinates, bypassing
    # ``org_delegate_task``'s chain registration, which causes:
    #   1) UserCommandTracker cannot see the sub-task and prematurely declares the command complete
    #   2) Sub-task has no deadline and no acceptance closure
    # Triggers when: sender has direct reports + msg_type=question + content contains clear task wording.
    # When triggered, the send is rejected with guidance to use org_delegate_task instead.
    # Controlled by the ``org_question_task_guard`` flag; can be disabled in one step.
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

        # Antipattern guard: coordinator dispatching tasks via question (controlled by flag)
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
                    "[org_send_message blocked] Detected: you are using msg_type=question "
                    "to dispatch a real task to a subordinate (content contains task-intent keywords "
                    "such as 'write', 'produce', 'complete', 'optimize', etc.). "
                    "This bypasses task-chain tracking and causes the system to declare your command "
                    "complete prematurely. "
                    "Use org_delegate_task to formally assign the task instead (one call per subordinate; "
                    "multiple parallel calls are fine). After the subordinate delivers, use "
                    "org_accept_deliverable to accept. "
                    "To block until delivery, call org_wait_for_deliverable."
                )

        metadata: dict = {}

        # If the caller's currently bound chain is closed, tag chain_closed in metadata,
        # so the receiver's `_on_node_message` can apply a soft gate. We don't block the send itself,
        # since conversational messages like replies/summaries still have value — they just shouldn't
        # re-activate ReAct.
        # Note: only tag metadata when the chain is closed; do not leak chain_id for "open" chains,
        # to avoid contaminating the receiver's next ReAct call with the sender's chain semantics.
        current_chain = self._runtime.get_current_chain_id(org_id, node_id)
        if current_chain and self._runtime.is_chain_closed(org_id, current_chain):
            metadata["task_chain_id"] = current_chain
            metadata["chain_closed"] = True

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
            # Use the same resolve_reference protocol as org_delegate_task to ensure
            # to_node must be node_xxxxxxxx or an exactly matching unique role_title;
            # any name-similar fuzzy match falls back to a "please use exact id" error
            # to prevent sending messages to the wrong same-named colleague
            # (e.g. substring ambiguity between "Product Director" and "Product Manager").
            resolved, candidates, status = org.resolve_reference(to_node)
            if status == "ambiguous_title":
                cand_list = ", ".join(
                    f"`{c.id}`({c.role_title})" for c in candidates
                )
                return (
                    f"[org_send_message failed] You are {caller_label}, to_node='{to_node}' "
                    f"matches multiple nodes: {cand_list}. Please use the exact id in node_xxxxxxxx form."
                )
            if status == "fuzzy":
                cand = candidates[0] if candidates else None
                cand_label = (
                    f"`{cand.id}`({cand.role_title})" if cand else f"'{to_node}'"
                )
                if cand and cand.id == node_id:
                    return (
                        f"[org_send_message failed] You are {caller_label}, "
                        f"to_node='{to_node}' fuzzy-matched to yourself ({cand_label}); you cannot send messages to yourself. "
                        "Please use an accurate target node id."
                    )
                return (
                    f"[org_send_message failed] You are {caller_label}, to_node='{to_node}' "
                    f"is not an exact match; the closest is {cand_label}. To avoid misrouting, change to_node to "
                    "the exact id in `node_xxxxxxxx` form and try again."
                )
            if status == "not_found":
                avail = ", ".join(f"{n.id}({n.role_title})" for n in org.nodes)
                return (
                    f"[org_send_message failed] You are {caller_label}, node '{to_node}' does not exist. "
                    f"Available nodes: {avail}"
                )

            to_node = resolved.id
            if to_node == node_id:
                return (
                    f"[org_send_message failed] You are {caller_label}; you cannot send messages to yourself."
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
        ok = await messenger.send(msg)
        if ok:
            await self._runtime._broadcast_ws("org:message", {
                "org_id": org_id, "from_node": node_id, "to_node": to_node,
                "msg_type": args.get("msg_type", "question"),
                "content": args["content"][:_LIM_WS],
            })
        return f"Message sent to {to_node}" if ok else "Send failed"

    async def _handle_org_reply_message(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return self._org_not_running_error(org_id)
        original = messenger._pending_messages.get(args["reply_to"])
        to_node = original.from_node if original else ""
        if not to_node:
            return f"Original message {args['reply_to']} not found; cannot determine reply target"
        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=to_node,
            msg_type=MsgType.ANSWER,
            content=args["content"],
            reply_to=args["reply_to"],
        )
        await messenger.send(msg)
        return "Reply sent"

    async def _handle_org_delegate_task(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return self._org_not_running_error(org_id)

        org = self._runtime.get_org(org_id)

        # chain_id assignment strategy (controlled by ``org_chain_parent_enforced`` flag):
        #   - flag=True (default, new behaviour): every delegate creates a new sub-chain and
        #     attaches it as a child of the caller's current_chain, allowing
        #     UserCommandTracker to walk the subtree and determine true "whole-tree closed".
        #   - flag=False (legacy behaviour): reuse the caller's existing current_chain if one
        #     exists, so the entire call-tree shares one chain_id. This is the
        #     backward-compat path that predates the parent-tracking bug fix.
        # An explicit ``task_chain_id`` supplied by the LLM always takes priority
        # (used for intentional re-delegation or chain continuation scenarios).
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

        # Soft barrier: if the current chain has been accepted/rejected/cancelled, block further delegation.
        # This is one of the core interception points preventing "the org continues to self-dispatch work after task completion".
        try:
            from openakita.config import settings as _settings
            if (getattr(_settings, "org_suppress_closed_chain_reactivation", True)
                    and self._runtime.is_chain_closed(org_id, chain_id)):
                logger.info(
                    "[ToolHandler] block delegate on closed chain=%s by=%s to=%s",
                    chain_id, node_id, args.get("to_node", ""),
                )
                return (
                    f"[Closed] Task chain {chain_id} has ended (accepted/rejected/cancelled). "
                    "Further org_delegate_task based on this chain is forbidden. "
                    "If new work is truly needed, the supervisor should initiate a new independent task. "
                    "For now, reply with a textual summary and do not call any org_* tools."
                )
        except Exception as exc:
            logger.debug("delegate closed-chain check skipped: %s", exc)

        chain_depth = self._runtime._chain_delegation_depth.get(chain_id, 0)
        max_depth = self._effective_max_delegation_depth(org)
        if chain_depth + 1 > max_depth:
            return (
                f"The delegation depth of this task chain has reached its limit ({max_depth} levels); cannot delegate further down. "
                f"Please complete this work yourself, or use org_submit_deliverable to submit the current deliverable to your supervisor for reassignment."
            )

        metadata = {}
        if args.get("deadline"):
            metadata["task_deadline"] = args["deadline"]

        metadata["_delegation_depth"] = chain_depth + 1
        metadata["task_chain_id"] = chain_id

        to_node = args["to_node"]

        # task_affinity semantics: "subsequent messages on the same chain are routed to
        # the same clone instance". Used by messenger.send (see the affinity_node !=
        # to_node and != from_node anti-self-reference guard in messenger.send).
        # On the delegate path, unconditionally overwriting to_node with existing_affinity
        # caused a fatal self-reference: after CEO delegates chain X to CPO,
        # affinity[X]=CPO; when CPO then re-delegates chain X down to PM, to_node=pm
        # would be overwritten back to cpo, immediately triggering "cannot delegate to yourself".
        # We only apply the affinity rewrite when all three conditions hold:
        #   1) existing_affinity is not the caller itself (avoids self-reference)
        #   2) existing_affinity is not the current explicit to_node (no rewrite needed)
        #   3) existing_affinity and to_node belong to the same clone group
        # This preserves the intent of "clone routing" without blocking normal up/downstream delegation.
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
            # Makes error messages explicit about who the caller is, so the LLM doesn't assume "just retry" is enough.
            caller_node = org.get_node(node_id)
            caller_label = (
                f"`{caller_node.id}`({caller_node.role_title})"
                if caller_node else f"`{node_id}`"
            )

            # In strict mode, _resolve_node_refs only rewrites exact_id/exact_title;
            # fuzzy/ambiguous/not_found values are kept as-is in to_node. We must
            # re-run strict resolution via resolve_reference here to emit structured errors;
            # otherwise the LLM has no idea which node_xxxxxxxx to use.
            resolved, candidates, status = org.resolve_reference(to_node)
            children = org.get_children(node_id)
            children_hint = (
                "Your direct reports: " + ", ".join(
                    f"{c.role_title}(`{c.id}`)" for c in children
                )
                if children
                else "You are a leaf node with no direct reports; you cannot use org_delegate_task."
            )

            if status == "ambiguous_title":
                cand_list = ", ".join(
                    f"`{c.id}`({c.role_title})" for c in candidates
                )
                return (
                    f"[org_delegate_task failed] You are {caller_label}, to_node='{to_node}' "
                    f"matches multiple nodes: {cand_list}. Please use the exact id in node_xxxxxxxx form and try again. "
                    f"{children_hint}"
                )
            if status == "fuzzy":
                cand = candidates[0] if candidates else None
                cand_label = (
                    f"`{cand.id}`({cand.role_title})" if cand else f"'{to_node}'"
                )
                # Special-case self-reference (fuzzy match happens to hit the caller itself) to block the most common
                # "Product Director delegates to itself" infinite loop.
                if cand and cand.id == node_id:
                    return (
                        f"[org_delegate_task failed] You are {caller_label}, "
                        f"to_node='{to_node}' fuzzy-matched to yourself ({cand_label}); you cannot delegate to yourself. "
                        f"Please use the exact subordinate id in node_xxxxxxxx form. {children_hint}"
                    )
                return (
                    f"[org_delegate_task failed] You are {caller_label}, to_node='{to_node}' "
                    f"is not an exact match; the closest is {cand_label}. To avoid misrouting, change to_node to "
                    f"the exact id in `node_xxxxxxxx` form and try again. {children_hint}"
                )
            if status == "not_found":
                avail = ", ".join(f"{n.id}({n.role_title})" for n in org.nodes)
                return (
                    f"[org_delegate_task failed] You are {caller_label}, target node '{to_node}' does not exist. "
                    f"Available nodes: {avail}. Check the to_node parameter, or use org_submit_deliverable to complete it yourself."
                )

            # exact_id / exact_title
            to_node = resolved.id

            # Validate hierarchy: only direct children can receive delegated tasks
            child_ids = {c.id for c in children}
            if to_node not in child_ids:
                if to_node == node_id:
                    hint = (
                        f"[org_delegate_task failed] You are {caller_label}; you cannot delegate a task to yourself."
                    )
                else:
                    target_node = org.get_node(to_node)
                    target_label = (
                        f"`{target_node.id}`({target_node.role_title})"
                        if target_node else f"`{to_node}`"
                    )
                    hint = (
                        f"[org_delegate_task failed] You are {caller_label}, "
                        f"{target_label} is not your direct report, so you cannot delegate to it."
                    )
                if children:
                    child_list = ", ".join(f"{c.role_title}(`{c.id}`)" for c in children)
                    return (
                        f"{hint} Your direct reports are: {child_list}. "
                        f"If the task is supposed to be done by you, use org_submit_deliverable to deliver the results instead; "
                        f"do not repeatedly call org_delegate_task — the Supervisor will flag it as an infinite loop and terminate."
                    )
                return (
                    f"{hint} You are a leaf node with no direct reports; you cannot use org_delegate_task at all. "
                    f"Call org_submit_deliverable directly to deliver the task result to your supervisor; "
                    f"use org_send_message for collaboration. Do not keep retrying org_delegate_task."
                )

        try:
            from openakita.orgs.project_store import ProjectStore
            from openakita.orgs.models import TaskStatus as _TS
            _store = ProjectStore(self._runtime._manager._org_dir(org_id))
            _existing = _store.find_task_by_chain(chain_id)
            if (_existing
                    and _existing.assignee_node_id == to_node
                    and _existing.status in (_TS.IN_PROGRESS, _TS.DELIVERED)):
                return (
                    f"{to_node} is already working on this task chain ({chain_id[:12]}); no need to re-delegate. "
                    f"Use org_list_delegated_tasks to check progress."
                )
        except Exception:
            pass

        await messenger.send_task(
            from_node=node_id,
            to_node=to_node,
            task_content=args["task"],
            priority=args.get("priority", 0),
            metadata=metadata,
        )

        messenger.bind_task_affinity(chain_id, to_node)
        self._runtime._chain_delegation_depth[chain_id] = chain_depth + 1

        # Maintain chain parent-child relationship (used when org_chain_parent_enforced is active).
        # parent_chain was determined in the chain_id calculation block above:
        # it equals caller_chain when a new sub-chain is created, and None on all other paths.
        try:
            if parent_chain and parent_chain != chain_id:
                self._runtime._chain_parent.setdefault(chain_id, parent_chain)
            else:
                self._runtime._chain_parent.setdefault(chain_id, None)
        except Exception:
            logger.debug(
                "[ToolHandler] chain_parent register failed", exc_info=True,
            )

        # Register a chain-closed event for org_wait_for_deliverable to block on.
        # Re-delegating the same chain reuses the existing event.
        try:
            if chain_id not in self._runtime._chain_events:
                self._runtime._chain_events[chain_id] = asyncio.Event()
        except Exception:
            logger.debug(
                "[ToolHandler] chain_event create failed", exc_info=True,
            )

        # User command lifecycle tracking: if an active UserCommandTracker exists on this org
        # and this delegation originates from the tracker's root or descendants, register the new chain
        # into the tracker as a signal that "this command is not yet complete".
        # Unregistration on close is handled by _mark_chain_closed.
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
            f"Delegated to {to_node}: {args['task'][:_LIM_EXEC_LOG]}",
            node_id,
        )
        return (
            f"Task assigned to {to_node} (chain: {chain_id[:12]}): {args['task'][:50]}\n"
            f"Note: the task has been dispatched asynchronously; the subordinate has not yet completed it. "
            f"Do not immediately report 'completed' — use org_list_delegated_tasks to track progress, "
            f"or wait for the subordinate to submit results via org_submit_deliverable before issuing a final report."
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
            return "Escalated to supervisor"
        return "Cannot escalate (no supervisor node)"

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
            return "Only the top-level node can broadcast org-wide; you can use a department broadcast instead"

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            msg_type=msg_type,
            content=args["content"],
            metadata={},
        )
        await messenger.send(msg)
        scope_label = "department" if scope == "department" else "org-wide"
        await self._runtime._broadcast_ws("org:broadcast", {
            "org_id": org_id, "from_node": node_id, "scope": scope,
            "content": args["content"][:_LIM_WS],
        })
        self._runtime.get_event_store(org_id).emit(
            "broadcast", node_id,
            {"scope": scope, "content": args["content"][:_LIM_EVENT]},
        )
        return f"Broadcast sent ({scope_label})"

    # ------------------------------------------------------------------
    # Organization awareness tools
    # ------------------------------------------------------------------

    async def _handle_org_get_org_chart(
        self, args: dict, org_id: str, node_id: str
    ) -> dict:
        org = self._runtime.get_org(org_id)
        if not org:
            return {"error": "Organization not found"}
        departments: dict[str, list] = {}
        for n in org.nodes:
            dept = n.department or "Unassigned"
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
            return {"error": "Organization not found"}
        target_id = args.get("node_id") or args.get("target_node") or ""
        target = org.get_node(target_id)
        if not target:
            return {"error": f"Node not found: {target_id}"}
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
            return {"error": "Organization not found"}
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
            return "Blackboard unavailable"
        entries = bb.read_org(
            limit=args.get("limit", 10),
            tag=args.get("tag"),
        )
        if not entries:
            return "(Blackboard is empty)"
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
            return "Blackboard unavailable"
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
            return f"Blackboard already has similar content; skipping duplicate write: {args['content'][:50]}"
        await self._runtime._broadcast_ws("org:blackboard_update", {
            "org_id": org_id, "scope": "org", "node_id": node_id,
            "memory_type": args.get("memory_type", "fact"),
            "content": args["content"][:_LIM_WS],
        })
        return f"Written to org blackboard: {args['content'][:50]}"

    async def _handle_org_read_dept_memory(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        org = self._runtime.get_org(org_id)
        if not bb or not org:
            return "Unavailable"
        node = org.get_node(node_id)
        dept = node.department if node else ""
        if not dept:
            return "You have not been assigned a department"
        entries = bb.read_department(dept, limit=args.get("limit", 10))
        if not entries:
            return f"(No department memory for {dept})"
        return "\n".join(f"[{e.memory_type.value}] {e.content}" for e in entries)

    async def _handle_org_write_dept_memory(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        org = self._runtime.get_org(org_id)
        if not bb or not org:
            return "Unavailable"
        node = org.get_node(node_id)
        dept = node.department if node else ""
        if not dept:
            return "You have not been assigned a department"
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
            return "Department memory already has similar content; skipping duplicate write"
        await self._runtime._broadcast_ws("org:blackboard_update", {
            "org_id": org_id, "scope": "department", "department": dept,
            "node_id": node_id, "memory_type": args.get("memory_type", "fact"),
            "content": args["content"][:_LIM_WS],
        })
        return f"Written to {dept} department memory"

    # ------------------------------------------------------------------
    # Node-level memory tools
    # ------------------------------------------------------------------

    async def _handle_org_read_node_memory(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        if not bb:
            return "Blackboard unavailable"
        entries = bb.read_node(node_id, limit=args.get("limit", 10))
        if not entries:
            return "(No private memory)"
        return "\n".join(f"[{e.memory_type.value}] {e.content}" for e in entries)

    async def _handle_org_write_node_memory(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        if not bb:
            return "Blackboard unavailable"
        raw_mt = args.get("memory_type", "fact")
        try:
            mt = MemoryType(raw_mt)
        except ValueError:
            mt = MemoryType.FACT
        entry = bb.write_node(
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
        return f"Written to private memory: {args['content'][:50]}"

    # ------------------------------------------------------------------
    # Policy tools
    # ------------------------------------------------------------------

    async def _handle_org_list_policies(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org_dir = self._runtime._manager._org_dir(org_id)
        policies_dir = org_dir / "policies"
        if not policies_dir.exists():
            return "(No policy files)"
        files = sorted(policies_dir.glob("*.md"))
        if not files:
            return "(No policy files)"
        return "\n".join(f"- {f.name}" for f in files)

    async def _handle_org_read_policy(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org_dir = self._runtime._manager._org_dir(org_id)
        fname = args["filename"]
        if ".." in fname or "/" in fname or "\\" in fname:
            return "Illegal filename"
        p = org_dir / "policies" / fname
        if not p.is_file():
            return f"Policy file does not exist: {fname}"
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
            return f"No policy found related to '{args['query']}'"
        return "\n\n".join(results)

    # ------------------------------------------------------------------
    # HR tools
    # ------------------------------------------------------------------

    async def _handle_org_freeze_node(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "Organization not found"
        target_id = args.get("node_id") or args.get("target_node") or ""
        target = org.get_node(target_id)
        if not target:
            return f"Node not found: {target_id}"
        org.get_parent(target_id)
        if node_id != "user":
            caller = org.get_node(node_id)
            if not caller:
                return "You are not in this organization"
            roots = org.get_root_nodes()
            if caller.level >= target.level and (not roots or node_id != roots[0].id):
                return "You can only freeze nodes at levels below yours"
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
        return f"Froze {target.role_title}, reason: {args.get('reason', '')}"

    async def _handle_org_unfreeze_node(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "Organization not found"
        target_id = args.get("node_id") or args.get("target_node") or ""
        target = org.get_node(target_id)
        if not target:
            return f"Node not found: {target_id}"
        if target.status != NodeStatus.FROZEN:
            return f"{target.role_title} is not in frozen state"
        target.status = NodeStatus.IDLE
        target.frozen_by = None
        target.frozen_reason = None
        target.frozen_at = None
        self._runtime._node_consecutive_failures.pop(f"{org_id}:{target_id}", None)
        await self._runtime._save_org(org)
        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            messenger.unfreeze_mailbox(target.id)
        self._runtime.get_event_store(org_id).emit(
            "node_unfrozen", node_id, {"target": target.id},
        )
        return f"Unfroze {target.role_title}"

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
                return f"Clone request auto-approved. New node: {req.result_node_id}"
            return f"Clone request submitted (ID: {req.id}), awaiting approval."
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
            return f"Recruit request submitted (ID: {req.id}, role: {args['role_title']}), awaiting approval."
        except ValueError as e:
            return str(e)

    async def _handle_org_dismiss_node(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        scaler = self._runtime.get_scaler()
        ok = await scaler.dismiss_node(org_id, args["node_id"], by=node_id)
        if ok:
            return f"Dismissed node {args['node_id']}"
        return "Dismiss failed (node does not exist or is not ephemeral)"

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

        # chain_id enforcement policy (active when org_chain_parent_enforced=True):
        # on submit, the caller's current incoming chain (the chain assigned to it by its
        # superior) must be used. This is the fix for "LLM omits task_chain_id on submit,
        # opening a new chain and breaking the whole-tree chain relationship". If the LLM
        # passes the wrong value it is overridden with caller's current_chain (with a warning);
        # if the caller has no current_chain, fall back to the LLM value or a new chain
        # (backward-compat path for edge cases such as root node accidentally calling submit).
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
                "You are the top-level owner of the organization; there is no supervisor to submit to. "
                "Your execution results are automatically returned to the commander — no need to use org_submit_deliverable. "
                "Just summarize the results directly in your reply."
            )

        # Idempotency barrier: when the same chain has already been accepted/rejected,
        # refuse re-submission to avoid duplicate deliverables/attachments and prevent
        # the parent from being re-awakened.
        # Note: delivered-but-not-yet-accepted is not blocked (revised versions are allowed;
        # downstream dedup covers the case).
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
                                f"[Closed] Task chain {chain_id} has already been accepted; cannot submit a deliverable again. "
                                "If there is new incremental work, raise it as a separate task or summarize it directly in a reply; "
                                "do not call org_submit_deliverable/org_delegate_task again."
                            )
                    recent_rej = events.query(event_type="task_rejected", limit=50)
                    for ev in recent_rej:
                        if ev.get("data", {}).get("chain_id") == chain_id:
                            # Rejected still allows resubmitting a corrected version (that's the semantics of rejected)
                            break
        except Exception as exc:
            logger.debug("submit-idempotency check skipped: %s", exc)

        # Register all explicitly-declared file_attachments to the blackboard + ProjectTask.
        # Use runtime._register_file_output as the single registration entry point, shared with
        # write_file / generate_image / deliver_artifacts (avoiding duplicate blackboard entries).
        # registered_attachments only keeps successfully registered entries (path exists + blackboard writable),
        # which are sent to the parent node via TASK_DELIVERED.
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

        # Auto-attachment fallback: roles without filesystem tools (CPO, PM, etc.) often
        # embed entire markdown documents in the deliverable field. The front-end then shows
        # it as a long chat message with no downloadable attachment and nothing on the
        # blackboard. When there are no explicit file_attachments, the deliverable looks
        # like a structured document (markdown headings / lists / code blocks), and it meets
        # the minimum character threshold, auto-persist it to
        # `<workspace>/deliverables/<chain_short>_<title>.md`, then register it via the
        # shared _register_file_output entry point (same as write_file / generate_image) to
        # ensure no duplicate blackboard writes (runtime.py). Any exception is only a warning
        # and does not affect the main submit_deliverable flow.
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
            content=f"Task delivered: {deliverable[:_LIM_EVENT]}",
            metadata=metadata,
        )
        ok = await messenger.send(msg)

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
                f"Submitted deliverable to {to_node}: {summary[:_LIM_EXEC_LOG]}",
                node_id,
            )
            tail = (
                f" (with {len(registered_attachments)} file attachment(s))"
                if registered_attachments else ""
            )
            return f"Deliverable submitted to {to_node}{tail}, awaiting acceptance."
        return "Submission failed"

    async def _handle_org_accept_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return self._org_not_running_error(org_id)

        from_node = args.get("from_node", "")
        if not from_node:
            return "Missing from_node parameter"
        if node_id == from_node:
            return "You cannot accept your own deliverable"

        chain_id = args.get("task_chain_id", "")
        if chain_id:
            events = self._runtime.get_event_store(org_id)
            if events:
                recent = events.query(event_type="task_accepted", limit=50)
                for ev in recent:
                    if ev.get("data", {}).get("chain_id") == chain_id:
                        return f"Deliverable for chain {chain_id} has already been accepted"

        feedback = args.get("feedback", "Accepted")

        metadata = {
            "task_chain_id": chain_id,
            "acceptance_feedback": feedback[:500],
        }

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=from_node,
            msg_type=MsgType.TASK_ACCEPTED,
            content=f"Accepted: {feedback[:_LIM_EVENT]}",
            metadata=metadata,
        )
        await messenger.send(msg)

        if chain_id:
            # Legacy behavior (messenger.release_task_affinity + chain_delegation_depth cleanup)
            # is handled uniformly by _cleanup_accepted_chain; still invoked explicitly here to ensure
            # that even if cleanup is disabled (future extension), we don't regress to a leak.
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
                org_id, chain_id, f"Accepted: {feedback[:_LIM_EXEC_LOG]}", node_id,
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
                content=f"Task accepted [{chain_id[:8] if chain_id else ''}]: {feedback[:_LIM_EVENT]}",
                source_node=node_id,
                memory_type=MemoryType.PROGRESS,
                tags=["acceptance", "completed"],
            )

        # Return structured JSON aligned with deliver_artifacts' receipts protocol.
        # The reasoning_engine parses receipts into delivery_receipts so that
        # TaskVerify recognizes "relay delivery" — the case where the parent node
        # did not call deliver_artifacts itself, but the child submitted files that
        # the parent then accepted.
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
            "message": f"Accepted {from_node}'s deliverable.",
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
            return "Missing from_node parameter"
        if node_id == from_node:
            return "You cannot reject your own deliverable"

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
            content=f"Task rejected: {reason[:_LIM_EVENT]}",
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
                f"Rejected: {reason[:_LIM_EXEC_LOG]}",
                node_id,
            )
            self._recalc_parent_progress(org_id, chain_id)
            # Rejected also needs cleanup so downstream agents don't continue submitting deliverables with the old chain;
            # but we don't cascade-cancel child tasks (rejected means redo, which may still depend on child task results).
            try:
                self._runtime._cleanup_accepted_chain(
                    org_id, chain_id, reason="rejected",
                    cascade_cancel_children=False,
                )
            except Exception as exc:
                logger.debug("cleanup_accepted_chain on reject failed: %s", exc)

        return f"Rejected {from_node}'s deliverable, reason: {reason[:50]}"

    async def _handle_org_wait_for_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        """Block until a subordinate task is delivered, avoiding polling-loop deadlocks.

        Multi-event wait to prevent deadlocks — wakes on the first of:
          - Any specified chain closing (accepted/rejected/cancelled)
          - A question/escalate message arriving in the node's inbox (coordinator must handle it immediately)
          - Timeout expiry (default 60 s, max 300 s)
          - The whole organisation being soft-stopped or the command being cancelled
        All exit paths call ``_touch_trackers_for_org`` to prevent the command watchdog from misfiring.
        """
        try:
            from openakita.config import settings as _s_wait
            if not getattr(_s_wait, "org_wait_primitive_enabled", True):
                return (
                    "[org_wait_for_deliverable disabled] "
                    "Use org_list_delegated_tasks to check progress instead."
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
            # Reverse-lookup _chain_parent: all sub-chains whose parent is my_chain
            target_chains = [
                c for c, p in runtime._chain_parent.items() if p == my_chain
            ]

        # Filter out already-closed chains (no longer meaningful to wait on)
        open_targets = [
            c for c in target_chains
            if not runtime.is_chain_closed(org_id, c)
        ]
        if not open_targets:
            return (
                "No open sub-chains to wait on. Subordinates may have already delivered — "
                "check your inbox for deliverable messages and use org_accept_deliverable to accept, "
                "or call org_list_delegated_tasks to confirm status."
            )

        # Prepare chain events (create on demand for any that are missing)
        chain_events: list[tuple[str, asyncio.Event]] = []
        for c in open_targets:
            ev = runtime._chain_events.get(c)
            if ev is None:
                ev = asyncio.Event()
                runtime._chain_events[c] = ev
            chain_events.append((c, ev))

        # Node inbox event: reset on every wait call so we only care about new messages
        # that arrive during this wait window
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

        # Re-check chain state (multiple chains may have closed simultaneously when asyncio.wait returns)
        closed_chains_now = [
            c for c, _ in chain_events
            if runtime.is_chain_closed(org_id, c)
        ]
        inbox_triggered = inbox_event.is_set()

        if not done:
            return (
                f"[Wait timed out] No new deliverable or message received within {timeout}s. "
                f"Still-open sub-chains: {open_targets[:5]}{'...' if len(open_targets) > 5 else ''}. "
                "Suggestions: use org_list_delegated_tasks to check detailed progress, "
                "or call org_wait_for_deliverable again to wait another round; "
                "if you have been waiting a long time and genuinely need to move forward, "
                "output a progress summary to the user."
            )

        parts: list[str] = []
        if closed_chains_now:
            preview = closed_chains_now[:5]
            extra = "..." if len(closed_chains_now) > 5 else ""
            parts.append(
                f"The following sub-chains have closed — check their deliverables: {preview}{extra}"
            )
        if inbox_triggered:
            parts.append(
                "A subordinate has sent a new message (question/escalate) that requires your immediate attention — "
                "handle the inbox message first, then resume org_wait_for_deliverable for any remaining sub-chains."
            )
        if not parts:
            parts.append(
                "[wait returned] No specific trigger identified; the command may have been cancelled "
                "or the event was consumed by a race condition. Check organisation state before deciding next steps."
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
            return "Organization not found"
        participants = args.get("participants", [])
        topic = args.get("topic", "")
        max_rounds = min(args.get("max_rounds", 3), 5)

        if len(participants) > 6:
            return "Meeting participant limit is 6; consider splitting into multiple smaller meetings"

        all_members = [node_id] + participants
        valid = [mid for mid in all_members if org.get_node(mid) is not None]
        if len(valid) < 2:
            return "Fewer than 2 valid participants"

        meeting_record: list[str] = [f"## Meeting topic: {topic}\n"]
        meeting_record.append(f"Host: {node_id}")
        meeting_record.append(f"Participants: {', '.join(participants)}\n")

        await self._runtime._broadcast_ws("org:meeting_started", {
            "org_id": org_id, "topic": topic,
            "host": node_id, "participants": participants, "rounds": max_rounds,
        })

        prev_round_summary = ""
        for round_num in range(1, max_rounds + 1):
            meeting_record.append(f"\n### Round {round_num}\n")

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
                    return pid, "(Absent)"
                try:
                    response = await self._lightweight_meeting_speak(
                        org, node_obj, topic, _round, max_rounds, _prev,
                    )
                    return pid, response
                except Exception as e:
                    logger.error(f"[Meeting] {pid} speak error: {e}")
                    return pid, "(Speech error)"

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
            meeting_record.append(f"\n### Meeting conclusion\n\n{conclusion}")

        bb = self._runtime.get_blackboard(org_id)
        if bb:
            summary_text = conclusion or meeting_record[-1][:_LIM_EVENT]
            bb.write_org(
                content=f"Meeting conclusion — {topic}: {summary_text}",
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
        """Lightweight meeting speech: a single LLM call, bypassing the full Agent/ReAct loop."""
        identity = self._runtime._get_identity(org.id)
        role_prompt = ""
        if identity:
            try:
                resolved = identity.resolve(node, org)
                role_prompt = (resolved.role or "")[:400]
            except Exception:
                pass

        context_parts = [
            f"You are the {node.role_title} ({node.department or ''}) of '{org.name}'.",
        ]
        role_goal = getattr(node, "role_goal", "") or ""
        if role_goal:
            context_parts.append(f"Your goal: {role_goal[:200]}")
        if role_prompt:
            context_parts.append(role_prompt)

        system_prompt = "\n".join(context_parts)

        user_parts = [
            f"You are attending an internal meeting on '{topic}' (round {round_num}/{max_rounds}).",
        ]
        if prev_round_summary:
            user_parts.append(f"\nPrevious round summary:\n{prev_round_summary[:800]}\n")
        user_parts.append(
            "Based on your responsibilities and expertise, share a concise opinion (100-200 words). "
            "State core points directly without pleasantries."
        )

        try:
            text = await self._llm_simple_call(
                system_prompt, "\n".join(user_parts), max_tokens=400,
            )
            return text[:500] if text else "(No content)"
        except Exception as e:
            logger.error(f"[Meeting] LLM call failed for {node.id}: {e}")
            return f"(Speech failed: {e})"

    async def _meeting_summarize(
        self, org_id: str, topic: str, meeting_record: list[str],
    ) -> str:
        """Generate meeting conclusion via LLM."""
        full_record = "\n".join(meeting_record)
        if len(full_record) > 3000:
            full_record = full_record[:3000] + "\n...(truncated)"

        user_msg = (
            f"The following is the meeting discussion record on '{topic}':\n\n{full_record}\n\n"
            "Summarize the conclusion including: 1) consensus reached 2) pending items 3) action plan. "
            "Use 150-300 words for a concise summary."
        )
        try:
            text = await self._llm_simple_call(
                "You are a professional meeting note-taker.", user_msg, max_tokens=500,
            )
            return (text or "")[:600]
        except Exception as e:
            logger.error(f"[Meeting] Summary LLM failed: {e}")
            return ""

    async def _llm_simple_call(
        self, system: str, user_content: str, max_tokens: int = 400,
    ) -> str:
        """Unified lightweight LLM call: compatible with both Message-type and dict-type responses."""
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
            title=f"{node_id} is requesting to create a scheduled task '{args['name']}'",
            body=f"Task prompt: {args['prompt'][:_LIM_WS]}\nType: {args.get('schedule_type', 'interval')}",
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
        return f"Scheduled task '{args['name']}' submitted for approval; will be created automatically once approved."

    async def _handle_org_list_my_schedules(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        schedules = self._runtime._manager.get_node_schedules(org_id, node_id)
        if not schedules:
            return "You currently have no scheduled tasks"
        lines = []
        for s in schedules:
            status = "Enabled" if s.enabled else "Paused"
            freq = s.cron or (f"every {s.interval_s}s" if s.interval_s else s.run_at or "unset")
            last = s.last_run_at or "never"
            lines.append(f"- [{status}] {s.name} | freq: {freq} | last: {last}")
        return "\n".join(lines)

    async def _handle_org_assign_schedule(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "Organization not found"
        target_id = args["target_node_id"]
        target = org.get_node(target_id)
        if not target:
            return f"Node not found: {target_id}"

        caller = org.get_node(node_id)
        if caller and caller.level >= target.level:
            parent = org.get_parent(target_id)
            if not parent or parent.id != node_id:
                return "You can only assign scheduled tasks to direct reports"

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
        return f"Assigned scheduled task '{sched.name}' to {target.role_title} (ID: {sched.id})"

    # ------------------------------------------------------------------
    # Policy proposal tool
    # ------------------------------------------------------------------

    async def _handle_org_propose_policy(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        inbox = self._runtime.get_inbox(org_id)
        inbox.push_approval_request(
            org_id, node_id,
            title=f"Policy proposal: {args['title']}",
            body=f"Proposer: {node_id}\nReason: {args['reason']}\nFile: {args['filename']}\n\n{args['content'][:500]}",
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
        return f"Policy proposal '{args['title']}' submitted for approval."

    # ------------------------------------------------------------------
    # Tool request / grant / revoke
    # ------------------------------------------------------------------

    async def _handle_org_request_tools(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "Organization not found"
        parent = org.get_parent(node_id)
        if not parent:
            return "You are the top-level node; cannot request from a supervisor. Configure external_tools directly."

        tools = args.get("tools", [])
        reason = args.get("reason", "")
        if not tools:
            return "Incomplete arguments: please specify the tools list to request."

        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "Messaging system not ready"

        from .tool_categories import TOOL_CATEGORIES
        ", ".join(tools)
        cat_details = []
        for t in tools:
            if t in TOOL_CATEGORIES:
                cat_details.append(f"{t}({', '.join(TOOL_CATEGORIES[t])})")
            else:
                cat_details.append(t)

        content = (
            f"[Tool request] {node_id} is requesting additional external tools: {', '.join(cat_details)}\n"
            f"Reason: {reason}\n\n"
            f"If approved, use org_grant_tools(node_id=\"{node_id}\", tools={tools}) to grant."
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
        return f"Tool request sent to {parent.role_title} ({parent.id}), awaiting approval."

    async def _handle_org_grant_tools(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "Organization not found"

        target_id = args.get("node_id", "")
        tools = args.get("tools", [])
        if not target_id or not tools:
            return "Incomplete arguments: node_id and tools are required"

        target = org.get_node(target_id)
        if not target:
            return f"Node not found: {target_id}"

        children = org.get_children(node_id)
        child_ids = {c.id for c in children}
        if target_id not in child_ids:
            return f"{target_id} is not your direct report; cannot grant."

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
                content=f"Your tool permissions have been updated; added: {', '.join(tools)}. Takes effect at next activation.",
                metadata={"_tool_grant": True, "granted_tools": tools},
            )
            await messenger.send(notify)

        self._runtime.get_event_store(org_id).emit(
            "tools_granted", node_id,
            {"target": target_id, "tools": tools},
        )
        return f"Granted {target.role_title} ({target_id}) access to: {', '.join(tools)}"

    async def _handle_org_revoke_tools(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "Organization not found"

        target_id = args.get("node_id", "")
        tools = args.get("tools", [])
        if not target_id or not tools:
            return "Incomplete arguments: node_id and tools are required"

        target = org.get_node(target_id)
        if not target:
            return f"Node not found: {target_id}"

        children = org.get_children(node_id)
        child_ids = {c.id for c in children}
        if target_id not in child_ids:
            return f"{target_id} is not your direct report; cannot operate."

        removed = []
        for t in tools:
            if t in target.external_tools:
                target.external_tools.remove(t)
                removed.append(t)

        if not removed:
            return f"{target.role_title} does not have any of these tools to revoke."

        await self._runtime._save_org(org)
        self._runtime.evict_node_agent(org_id, target_id)

        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            notify = OrgMessage(
                org_id=org_id,
                from_node=node_id,
                to_node=target_id,
                msg_type=MsgType.FEEDBACK,
                content=f"Some of your tool permissions have been revoked: {', '.join(removed)}. Takes effect at next activation.",
                metadata={"_tool_revoke": True, "revoked_tools": removed},
            )
            await messenger.send(notify)

        self._runtime.get_event_store(org_id).emit(
            "tools_revoked", node_id,
            {"target": target_id, "tools": removed},
        )
        return f"Revoked {target.role_title}'s ({target_id}) tools: {', '.join(removed)}"

    # ------------------------------------------------------------------
    # Project task tools
    # ------------------------------------------------------------------

    async def _handle_org_report_progress(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        chain_id = args.get("task_chain_id", "")
        if not chain_id:
            return "Missing task_chain_id"
        try:
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            existing = store.find_task_by_chain(chain_id)
            if not existing:
                return f"Task chain {chain_id[:12]} not found"
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
            msg = f"Progress reported: {updates.get('progress_pct', '')}%"
            if "status" in updates:
                msg += f" (status auto-updated to {updates['status'].value})"
            return msg
        except Exception as e:
            logger.debug("org_report_progress failed: %s", e)
            return f"Report failed: {e}"

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
                return {"error": "Task not found"}
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
            return "Need task_id or task_chain_id"
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
                return "Task not found"
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
            return "Updated"
        except Exception as e:
            logger.debug("org_update_project_task failed: %s", e)
            return f"Update failed: {e}"

    async def _handle_org_create_project_task(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        project_id = args.get("project_id", "")
        title = args.get("title", "")
        if not project_id or not title:
            return "Need project_id and title"
        try:
            from openakita.orgs.models import ProjectTask, TaskStatus
            from openakita.orgs.project_store import ProjectStore

            mgr = self._runtime._manager
            store = ProjectStore(mgr._org_dir(org_id))
            proj = store.get_project(project_id)
            if not proj:
                return f"Project {project_id} does not exist"
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
            return f"Created task {task.id}: {title[:50]}"
        except Exception as e:
            logger.debug("org_create_project_task failed: %s", e)
            return f"Creation failed: {e}"
