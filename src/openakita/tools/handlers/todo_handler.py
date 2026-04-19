"""
PlanHandler class + create_todo_handler factory function

Split from plan.py, responsible for:
- PlanHandler class (tool call handling, plan file persistence, progress display)
- create_todo_handler factory function
"""

import json
import logging
import secrets
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .todo_state import (
    _session_handlers,
    force_close_plan,
    has_active_todo,
    register_active_todo,
    register_plan_handler,
    unregister_active_todo,
)
from .todo_store import TodoStore

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

__all__ = ["PlanHandler", "create_todo_handler"]


class PlanHandler:
    """Plan mode handler"""

    TOOLS = [
        "create_todo",
        "update_todo_step",
        "get_todo_status",
        "complete_todo",
        "create_plan_file",
        "exit_plan_mode",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self.current_todo: dict | None = None
        self._todos_by_session: dict[str, dict] = {}
        self.plan_dir = Path("data/plans")
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        self._store = TodoStore(self.plan_dir / "todo_store.json")

    def _get_conversation_id(self) -> str:
        return (
            getattr(self.agent, "_current_conversation_id", None)
            or getattr(self.agent, "_current_session_id", None)
            or ""
        )

    def _get_current_todo(self) -> dict | None:
        """Get the Todo for the current session (session isolated).

        Recovery priority:
        1. This instance's _todos_by_session (fastest path)
        2. Old handler in module-level _session_handlers (typical case after tool system hot reload)
        3. TodoStore persistence layer (fallback recovery after process restart / handler rebuild)
        """
        cid = self._get_conversation_id()
        if cid:
            todo = self._todos_by_session.get(cid)
            if todo is not None:
                return todo
            old_handler = _session_handlers.get(cid)
            if old_handler is not None and old_handler is not self:
                old_todo = old_handler._todos_by_session.get(cid)
                if old_todo is not None:
                    self._todos_by_session[cid] = old_todo
                    logger.info(
                        f"[Todo] Recovered todo {old_todo.get('id')} from previous handler for {cid}"
                    )
                    return old_todo
            # Fallback: recover from persistent store
            stored = self._store.get(cid)
            if stored is not None and stored.get("status") == "in_progress":
                self._todos_by_session[cid] = stored
                register_plan_handler(cid, self)
                register_active_todo(cid, stored.get("id", ""))
                logger.info(f"[Todo] Recovered todo {stored.get('id')} from TodoStore for {cid}")
                return stored
            return None
        return self.current_todo

    def _set_current_todo(self, plan: dict | None) -> None:
        """Set the Todo for the current session (session isolated)"""
        cid = self._get_conversation_id()
        if cid:
            if plan is not None:
                plan["conversation_id"] = cid
                self._todos_by_session[cid] = plan
            else:
                self._todos_by_session.pop(cid, None)
        else:
            self.current_todo = plan

    def get_plan_for(self, conversation_id: str) -> dict | None:
        """Get Todo by conversation_id (does not depend on agent state, for external callers)"""
        if conversation_id:
            plan = self._todos_by_session.get(conversation_id)
            if plan is not None:
                return plan
            stored = self._store.get(conversation_id)
            if stored is not None and stored.get("status") == "in_progress":
                self._todos_by_session[conversation_id] = stored
                register_plan_handler(conversation_id, self)
                register_active_todo(conversation_id, stored.get("id", ""))
                return stored
            return None
        return self.current_todo

    def finalize_plan(self, plan: dict, session_id: str, action: str = "auto_close") -> None:
        """Plan finalization (called by the todo_state module).

        Encapsulates all access to handler private members in auto_close_todo / cancel_todo,
        including step status rewrites, logging, persistence, and memory cleanup.
        Note: unregister_active_todo and _emit_todo_lifecycle_event are still handled by the caller
        (they live in todo_state).
        """
        steps = plan.get("steps", [])
        now = datetime.now().isoformat()

        if action == "cancel":
            for step in steps:
                if step.get("status") in ("in_progress", "pending"):
                    step["status"] = "cancelled"
                    step["result"] = step.get("result") or "(cancelled by user)"
                    step["completed_at"] = now
            plan["status"] = "cancelled"
            plan["completed_at"] = now
            if not plan.get("summary"):
                plan["summary"] = "Cancelled by user"
            self._add_log("Plan cancelled by user", plan=plan)
        else:  # auto_close
            for step in steps:
                status = step.get("status", "pending")
                if status == "in_progress":
                    step["status"] = "completed"
                    step["result"] = step.get("result") or "(auto-marked complete)"
                    step["completed_at"] = now
                elif status == "pending":
                    step["status"] = "skipped"
                    step["result"] = "(not reached before task ended)"
            plan["status"] = "completed"
            plan["completed_at"] = now
            if not plan.get("summary"):
                plan["summary"] = "Task ended, plan auto-closed"
            self._add_log("Plan auto-closed (complete_todo was not explicitly called before task end)", plan=plan)

        self._save_plan_markdown(plan=plan)
        self._todos_by_session.pop(session_id, None)
        if self.current_todo is plan:
            self.current_todo = None
        self._store.remove(session_id)

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle tool call"""
        if tool_name == "create_todo":
            return await self._create_todo(params)
        elif tool_name == "update_todo_step":
            return await self._update_step(params)
        elif tool_name == "get_todo_status":
            return self._get_status()
        elif tool_name == "complete_todo":
            return await self._complete_todo(params)
        elif tool_name == "create_plan_file":
            return await self._create_plan_file(params)
        elif tool_name == "exit_plan_mode":
            return await self._exit_plan_mode(params)
        else:
            return f"❌ Unknown plan tool: {tool_name}"

    async def _create_todo(self, params: dict) -> str:
        """Create a task plan (supports multiple coexisting plans)"""
        if "task_summary" not in params and "goal" in params:
            params["task_summary"] = params.pop("goal")

        _plan = self._get_current_todo()
        if _plan and _plan.get("status") == "in_progress":
            existing_plan_id = _plan["id"]
            logger.info(
                f"[Plan] Creating new plan while existing plan {existing_plan_id} "
                f"is still in progress (multi-plan mode)"
            )

        cid = self._get_conversation_id()
        if cid and has_active_todo(cid) and _plan is None:
            logger.warning(
                f"[Plan] Inconsistent state: active_todo registered but no plan data for {cid}, force-closing"
            )
            force_close_plan(cid)

        plan_id = f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"

        steps = params.get("steps", [])
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except (json.JSONDecodeError, TypeError):
                return "❌ Invalid format for 'steps' parameter, a JSON array is required"
        if not isinstance(steps, list):
            return "❌ Invalid format for 'steps' parameter, a JSON array is required"
        if len(steps) == 0:
            return "❌ At least one step is required to create a plan"

        normalized_steps: list[dict] = []
        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                return f"❌ steps[{index}] has invalid format, an object is required"

            step = dict(raw_step)

            if "id" not in step or not str(step["id"]).strip():
                step["id"] = f"step_{index + 1}"
            else:
                step["id"] = str(step["id"]).strip()[:64]
            if "description" not in step or not str(step["description"]).strip():
                step["description"] = step["id"]
            else:
                step["description"] = str(step["description"]).strip()[:512]

            for field_name in ("skills", "depends_on"):
                field_value = step.get(field_name)
                if isinstance(field_value, str):
                    try:
                        field_value = json.loads(field_value)
                    except (json.JSONDecodeError, TypeError):
                        return f"❌ Invalid format for steps[{index}].{field_name}, a JSON array is required"
                    if not isinstance(field_value, list):
                        return f"❌ Invalid format for steps[{index}].{field_name}, a JSON array is required"
                    step[field_name] = field_value
                elif field_value is not None and not isinstance(field_value, list):
                    return f"❌ Invalid format for steps[{index}].{field_name}, a JSON array is required"

            step["status"] = "pending"
            step["result"] = ""
            step["started_at"] = None
            step["completed_at"] = None
            step.setdefault("skills", [])
            step["skills"] = self._ensure_step_skills(step)
            normalized_steps.append(step)

        steps = normalized_steps

        _new_plan = {
            "id": plan_id,
            "plan_type": "todo",
            "task_summary": params.get("task_summary", ""),
            "steps": steps,
            "status": "in_progress",
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "logs": [],
        }
        self._set_current_todo(_new_plan)

        self._save_plan_markdown()

        conversation_id = self._get_conversation_id()
        if conversation_id:
            register_active_todo(conversation_id, plan_id)
            register_plan_handler(conversation_id, self)

        self._add_log(f"Plan created: {params.get('task_summary', '')}")

        if conversation_id:
            self._store.upsert(conversation_id, _new_plan)
        for step in steps:
            logger.info(
                f"[Plan] Step {step.get('id')} tool={step.get('tool', '-')} skills={step.get('skills', [])}"
            )

        plan_message = self._format_plan_message()

        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                await gateway.emit_progress_event(
                    session, f"📋 Plan created: {params.get('task_summary', '')}\n{plan_message}"
                )
        except Exception as e:
            logger.warning(f"Failed to emit plan progress: {e}")

        return f"✅ Created todo: {plan_id}\n\n{plan_message}"

    async def _update_step(self, params: dict) -> str:
        """Update step status"""
        _plan = self._get_current_todo()
        if not _plan:
            cid = self._get_conversation_id()
            if cid and has_active_todo(cid):
                logger.warning(
                    f"[Todo] update_step: todo data lost for {cid}, force-closing stale registration"
                )
                force_close_plan(cid)
            return "❌ No active plan; please create a task plan first"

        # TD2: stamp a unique turn_id on every update_step call so that
        # auto_close_todo can distinguish "just set in_progress this turn"
        # from "was in_progress from a previous turn".
        _plan["_current_turn_id"] = datetime.now().isoformat()

        step_id = str(params.get("step_id", "")).strip()
        status = str(params.get("status", "")).strip()
        result = params.get("result", "")

        if not step_id:
            return "❌ Please specify the step to update"
        if not status:
            return "❌ Please specify the target status for the step (e.g. in_progress, completed, failed, skipped)"

        _VALID_TRANSITIONS: dict[str, set[str]] = {
            "pending": {"in_progress", "completed", "skipped", "cancelled"},
            "in_progress": {"completed", "failed", "skipped", "cancelled"},
            "completed": set(),
            "failed": {"in_progress"},
            "skipped": {"in_progress"},
            "cancelled": set(),
        }

        step_found = False
        for step in _plan["steps"]:
            if step["id"] == step_id:
                old_status = step.get("status", "pending")
                allowed = _VALID_TRANSITIONS.get(old_status, set())
                if status != old_status and status not in allowed:
                    return (
                        f"⚠️ Step {step_id} is currently in status {old_status}; "
                        f"direct change to {status} is not allowed. "
                        f"Allowed target statuses: {', '.join(sorted(allowed)) or 'none (terminal state)'}"
                    )
                if status == "in_progress":
                    deps = step.get("depends_on", [])
                    if deps:
                        _DONE = {"completed", "skipped", "cancelled"}
                        steps_map = {s["id"]: s for s in _plan["steps"]}
                        blocked = [
                            d for d in deps if steps_map.get(d, {}).get("status") not in _DONE
                        ]
                        if blocked:
                            return (
                                f"⚠️ Step {step_id} depends on {', '.join(blocked)}; "
                                f"those steps are not yet complete, please finish them first."
                            )
                step["status"] = status
                step["result"] = result
                step.setdefault("skills", [])
                step["skills"] = self._ensure_step_skills(step)

                if status == "in_progress" and not step.get("started_at"):
                    step["started_at"] = datetime.now().isoformat()
                if status == "in_progress":
                    step["_last_updated_turn"] = _plan.get("_current_turn_id", "")
                elif status in ["completed", "failed", "skipped", "cancelled"]:
                    step["completed_at"] = datetime.now().isoformat()

                step_found = True
                logger.info(
                    f"[Plan] Step update {step_id} status={status} tool={step.get('tool', '-')} skills={step.get('skills', [])}"
                )
                break

        if not step_found:
            return f"❌ Step not found: {step_id}"

        self._save_plan_markdown()
        cid_for_store = self._get_conversation_id()
        if cid_for_store:
            self._store.upsert(cid_for_store, _plan)

        status_emoji = {"in_progress": "🔄", "completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(
            status, "📌"
        )

        self._add_log(f"{status_emoji} {step_id}: {result or status}")

        steps = _plan["steps"]
        total_count = len(steps)

        step_number = next(
            (i + 1 for i, s in enumerate(steps) if s["id"] == step_id),
            0,
        )

        step_desc = ""
        for s in steps:
            if s["id"] == step_id:
                step_desc = s.get("description", "")
                break

        message = f"{status_emoji} **[{step_number}/{total_count}]** {step_desc or step_id}"
        if status == "completed" and result:
            message += f"\n   Result: {result}"
        elif status == "failed":
            message += f"\n   ❌ Error: {result or 'unknown error'}"

        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                await gateway.emit_progress_event(session, message)
        except Exception as e:
            logger.warning(f"Failed to emit step progress: {e}")

        response = f"Step {step_id} status updated to {status}"

        if status == "completed":
            pending_steps = [s for s in steps if s.get("status") in ("pending", "in_progress")]
            if pending_steps:
                next_step = pending_steps[0]
                response += f"\n\n💡 Next step: {next_step.get('description', next_step['id'])}"
            else:
                response += "\n\n✅ All steps complete, please finalize this plan."
        elif status == "failed":
            response += "\n\n⚠️ This step failed; please investigate the cause and decide whether to retry or skip."

        return response

    def _get_status(self) -> str:
        """Get plan status"""
        plan = self._get_current_todo()
        if not plan:
            return "No active plan"
        steps = plan["steps"]

        completed = sum(1 for s in steps if s["status"] == "completed")
        failed = sum(1 for s in steps if s["status"] == "failed")
        pending = sum(1 for s in steps if s["status"] == "pending")
        in_progress = sum(1 for s in steps if s["status"] == "in_progress")

        status_text = f"""## Plan status: {plan["task_summary"]}

**Plan ID**: {plan["id"]}
**Status**: {plan["status"]}
**Progress**: {completed}/{len(steps)} done

### Steps

| Step | Description | Skills | Status | Result |
|------|-------------|--------|--------|--------|
"""

        for step in steps:
            status_emoji = {
                "pending": "⬜",
                "in_progress": "🔄",
                "completed": "✅",
                "failed": "❌",
                "skipped": "⏭️",
            }.get(step["status"], "❓")

            skills = ", ".join(step.get("skills", []) or [])
            status_text += f"| {step['id']} | {step['description']} | {skills or '-'} | {status_emoji} | {step.get('result', '-')} |\n"

        status_text += f"\n**Summary**: ✅ {completed} done, ❌ {failed} failed, ⬜ {pending} pending, 🔄 {in_progress} in progress"

        return status_text

    async def _complete_todo(self, params: dict) -> str:
        """Complete the plan"""
        _plan = self._get_current_todo()
        if not _plan:
            cid = self._get_conversation_id()
            if cid and has_active_todo(cid):
                logger.warning(
                    f"[Plan] complete_todo: plan data lost for {cid}, force-closing stale registration"
                )
                force_close_plan(cid)
                return "⚠️ Previous plan data was lost; the deadlocked state has been force-cleared. You may start a new task."
            return "❌ No active plan"

        summary = params.get("summary", "")

        steps = _plan["steps"]
        still_active = [s for s in steps if s.get("status") in ("pending", "in_progress")]
        if still_active:
            active_ids = [s.get("id", "?") for s in still_active[:5]]
            return (
                f"⚠️ {len(still_active)} step(s) are not yet complete: {', '.join(active_ids)}.\n"
                "Please complete or skip them before marking the plan as complete."
            )

        _plan["status"] = "completed"
        _plan["completed_at"] = datetime.now().isoformat()
        _plan["summary"] = summary

        completed = sum(1 for s in steps if s["status"] == "completed")
        failed = sum(1 for s in steps if s["status"] == "failed")

        self._save_plan_markdown()
        self._add_log(f"Plan completed: {summary}")

        complete_message = f"""🎉 **Task complete!**

{summary}

**Execution summary**:
- Total steps: {len(steps)}
- Succeeded: {completed}
- Failed: {failed}
"""

        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                await gateway.emit_progress_event(session, complete_message)
        except Exception as e:
            logger.warning(f"Failed to emit complete progress: {e}")

        plan_id = _plan["id"]
        self._set_current_todo(None)

        conversation_id = self._get_conversation_id()
        if conversation_id:
            unregister_active_todo(conversation_id)
            self._store.remove(conversation_id)

        return f"✅ Plan {plan_id} completed\n\n{complete_message}"

    async def _create_plan_file(self, params: dict) -> str:
        """Create a Cursor-style .plan.md file (YAML frontmatter + Markdown body).

        Used to generate a structured plan file in Plan mode.
        """
        name = params.get("name", "Untitled Plan")
        overview = params.get("overview", "")
        todos = params.get("todos", [])
        body = params.get("body", "")

        if isinstance(todos, str):
            try:
                todos = json.loads(todos)
            except (json.JSONDecodeError, TypeError):
                return "❌ Invalid format for 'todos' parameter, a JSON array is required"

        import hashlib as _hashlib

        _slug = name[:30].replace(" ", "_").replace("/", "_")
        _hash = _hashlib.md5(name.encode()).hexdigest()[:8]
        filename = f"{_slug}_{_hash}.plan.md"

        plan_file = self.plan_dir / filename
        if plan_file.exists():
            for _seq in range(2, 100):
                _candidate = self.plan_dir / f"{_slug}_{_hash}_{_seq}.plan.md"
                if not _candidate.exists():
                    plan_file = _candidate
                    break

        def _yaml_escape(val: str) -> str:
            """YAML-safe escape: quote if special chars are present and escape inner double quotes"""
            if not val:
                return '""'
            needs_quote = any(
                c in val
                for c in (
                    ":",
                    "#",
                    '"',
                    "'",
                    "\n",
                    "{",
                    "}",
                    "[",
                    "]",
                    ",",
                    "&",
                    "*",
                    "?",
                    "|",
                    "-",
                    "<",
                    ">",
                    "=",
                    "!",
                    "%",
                    "@",
                    "`",
                )
            )
            if needs_quote:
                return (
                    '"' + val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
                )
            return val

        yaml_lines = ["---"]
        yaml_lines.append(f"name: {_yaml_escape(name)}")
        if overview:
            yaml_lines.append(f"overview: {_yaml_escape(overview)}")
        if todos:
            yaml_lines.append("todos:")
            for todo in todos:
                todo_id = todo.get("id", f"step_{secrets.token_hex(3)}")
                content = todo.get("content", "")
                status = todo.get("status", "pending")
                yaml_lines.append(f"  - id: {_yaml_escape(todo_id)}")
                yaml_lines.append(f"    content: {_yaml_escape(content)}")
                yaml_lines.append(f"    status: {status}")
        yaml_lines.append("isProject: true")
        yaml_lines.append("---")

        content = "\n".join(yaml_lines) + "\n\n" + body

        plan_file.write_text(content, encoding="utf-8")
        logger.info(f"[Plan] Created plan file: {plan_file}")

        plan_id = f"planfile_{_hash}"
        steps = []
        for todo in todos:
            steps.append(
                {
                    "id": todo.get("id", f"step_{secrets.token_hex(3)}"),
                    "description": todo.get("content", ""),
                    "status": todo.get("status", "pending"),
                    "result": "",
                    "started_at": None,
                    "completed_at": None,
                    "skills": [],
                }
            )

        _new_plan = {
            "id": plan_id,
            "plan_type": "plan_file",
            "task_summary": name,
            "steps": steps,
            "status": "in_progress",
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "logs": [],
            "plan_file": str(plan_file),
        }
        self._set_current_todo(_new_plan)

        conversation_id = self._get_conversation_id()
        if conversation_id:
            register_active_todo(conversation_id, plan_id)
            register_plan_handler(conversation_id, self)
            self._store.upsert(conversation_id, _new_plan)

        self._add_log(f"Plan file created: {name}")

        return (
            f"✅ Plan file created: {plan_file}\n\n"
            f"Contains {len(todos)} step(s).\n\n"
            f"⚠️ Next step: call exit_plan_mode to notify the user that planning is complete.\n"
            f"Do not attempt to execute any steps in the plan — the user must approve the plan first."
        )

    async def _exit_plan_mode(self, params: dict) -> str:
        """Exit Plan mode — OpenCode-style mode switch.

        1. Emit SSE events to notify the frontend
        2. Set a flag on the agent to signal mode switch to "agent"
        3. Return a message asking the user to approve the plan
        """
        summary = params.get("summary", "Planning complete")

        try:
            session = getattr(self.agent, "_current_session", None)
            gateway = (
                session.get_metadata("_gateway")
                if session and hasattr(session, "get_metadata")
                else None
            )
            if gateway and hasattr(gateway, "emit_progress_event"):
                await gateway.emit_progress_event(
                    session,
                    f"📋 **Plan mode complete**\n{summary}\n\nWaiting for user approval before execution...",
                )
        except Exception as e:
            logger.warning(f"Failed to emit exit_plan_mode event: {e}")

        conversation_id = self._get_conversation_id()
        plan_id = ""
        plan_file_path = ""
        current = self._get_current_todo()
        if current:
            plan_id = current.get("id", "")
            plan_file_path = current.get("plan_file", "")

        try:
            from ...api.routes.websocket import broadcast_event

            await broadcast_event(
                "plan:ready_for_approval",
                {
                    "conversation_id": conversation_id,
                    "summary": summary,
                    "plan_id": plan_id,
                    "plan_file": plan_file_path,
                },
            )
        except Exception:
            pass

        try:
            pending_dict = getattr(self.agent, "_plan_exit_pending", None)
            if not isinstance(pending_dict, dict):
                pending_dict = {}
                self.agent._plan_exit_pending = pending_dict
            pending_dict[conversation_id] = {
                "summary": summary,
                "plan_id": plan_id,
                "plan_file": plan_file_path,
                "conversation_id": conversation_id,
            }
            logger.info(
                f"[Plan] exit_plan_mode: flagged for mode switch "
                f"(conv={conversation_id}, plan_file={plan_file_path})"
            )
        except Exception as e:
            logger.warning(f"[Plan] Failed to set _plan_exit_pending: {e}")

        return (
            f"✅ Plan completed.\n\n"
            f"{summary}\n\n"
            f"The plan is ready for user review. "
            f"STOP HERE — do NOT attempt to execute the plan. "
            f"Wait for user to approve or request changes."
        )

    def _format_plan_message(self) -> str:
        """Format the plan display message"""
        plan = self._get_current_todo()
        if not plan:
            return ""
        steps = plan["steps"]

        message = f"""📋 **Task plan**: {plan["task_summary"]}

"""
        for i, step in enumerate(steps):
            prefix = "├─" if i < len(steps) - 1 else "└─"
            skills = ", ".join(step.get("skills", []) or [])
            if skills:
                message += f"{prefix} {i + 1}. {step['description']}  (skills: {skills})\n"
            else:
                message += f"{prefix} {i + 1}. {step['description']}\n"

        message += "\nStarting execution..."

        return message

    def get_plan_prompt_section(self, conversation_id: str = "") -> str:
        """
        Generate the plan summary section to be injected into system_prompt.

        This section lives in system_prompt so it isn't lost when working_messages
        is compressed, ensuring the LLM always sees the full plan structure and
        the latest progress.

        Args:
            conversation_id: specify the session ID to precisely locate the Plan (avoids depending on agent state)

        Returns:
            A compact plan section string; an empty string if there is no active Plan or the Plan is already complete.
        """
        plan = self.get_plan_for(conversation_id) if conversation_id else self._get_current_todo()
        if not plan or plan.get("status") == "completed":
            return ""
        steps = plan["steps"]
        total = len(steps)
        completed = sum(1 for s in steps if s["status"] in ("completed", "failed", "skipped"))

        lines = [
            f"## Active Plan: {plan['task_summary']}  (id: {plan['id']})",
            f"Progress: {completed}/{total} done",
            "",
        ]

        _max_result_len = 200 if total > 20 else 300
        _char_budget = 4000
        for i, step in enumerate(steps):
            num = i + 1
            icon = {
                "pending": "  ",
                "in_progress": ">>",
                "completed": "OK",
                "failed": "XX",
                "skipped": "--",
                "cancelled": "~~",
            }.get(step["status"], "??")
            desc = step.get("description", step["id"])
            result_hint = ""
            if step["status"] == "completed" and step.get("result"):
                result_hint = f" => {step['result'][:_max_result_len]}"
            elif step["status"] == "failed" and step.get("result"):
                result_hint = f" => FAIL: {step['result'][:_max_result_len]}"
            line = f"  [{icon}] {num}. {desc}{result_hint}"
            _char_budget -= len(line)
            if _char_budget < 0:
                lines.append(f"  ... ({total - i} more steps omitted)")
                break
            lines.append(line)

        plan_file = plan.get("plan_file", "")
        if plan_file:
            lines.append(f"Plan file: {plan_file}")

        lines.append("")
        if plan_file:
            lines.append(
                "IMPORTANT: This plan already exists as a plan file. "
                "In Plan mode, you can modify the plan file using write_file. "
                "In Agent mode, use update_todo_step to track execution progress. "
                "Do NOT call create_todo or create_plan_file again."
            )
        else:
            lines.append(
                "IMPORTANT: This plan already exists. Do NOT call create_todo again. "
                "Continue from the current step using update_todo_step."
            )

        for step in steps:
            if step["status"] == "in_progress" and step.get("started_at"):
                try:
                    started = datetime.fromisoformat(step["started_at"])
                    elapsed = (datetime.now() - started).total_seconds()
                    if elapsed > 300:
                        mins = int(elapsed / 60)
                        lines.append(
                            f"\n⚠️ STALE: Step '{step['id']}' has been in_progress for {mins} min. "
                            "Consider completing, failing, or skipping it."
                        )
                except (ValueError, TypeError):
                    pass

        return "\n".join(lines)

    def _save_plan_markdown(self, plan: dict | None = None) -> None:
        """Save the plan to a Markdown file (an explicit plan reference can be passed to avoid depending on agent state)"""
        if plan is None:
            plan = self._get_current_todo()
        if not plan:
            return
        plan_file = self.plan_dir / f"{plan['id']}.md"

        def _esc(val: str) -> str:
            if not val:
                return '""'
            if any(c in val for c in (":", "#", '"', "'", "\n", "{", "}", "[", "]")):
                return (
                    '"' + val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
                )
            return val

        _name = plan.get("task_summary", "")
        content = f"""---
id: {plan["id"]}
name: {_esc(_name)}
status: {plan["status"]}
created_at: {plan["created_at"]}
completed_at: {plan.get("completed_at") or ""}
---

# Task plan: {_name}

## Steps

| ID | Description | Skills | Tool | Status | Result |
|----|-------------|--------|------|--------|--------|
"""

        def _md_escape_cell(val: str) -> str:
            return val.replace("|", "\\|").replace("\n", " ")

        for step in plan["steps"]:
            status_emoji = {
                "pending": "⬜",
                "in_progress": "🔄",
                "completed": "✅",
                "failed": "❌",
                "skipped": "⏭️",
                "cancelled": "🚫",
            }.get(step["status"], "❓")

            tool = _md_escape_cell(step.get("tool", "-"))
            skills = _md_escape_cell(", ".join(step.get("skills", []) or []) or "-")
            result = _md_escape_cell(step.get("result", "-") or "-")
            sid = _md_escape_cell(step["id"])
            desc = _md_escape_cell(step["description"])

            content += f"| {sid} | {desc} | {skills} | {tool} | {status_emoji} | {result} |\n"

        content += "\n## Execution log\n\n"
        for log in plan.get("logs", []):
            content += f"- {log}\n"

        if plan.get("summary"):
            content += f"\n## Completion summary\n\n{plan['summary']}\n"

        plan_file.write_text(content, encoding="utf-8")
        logger.info(f"[Plan] Saved to: {plan_file}")

    def _add_log(self, message: str, plan: dict | None = None) -> None:
        """Append a log entry (an explicit plan reference can be passed to avoid depending on agent state)"""
        if plan is None:
            plan = self._get_current_todo()
        if plan:
            timestamp = datetime.now().strftime("%H:%M:%S")
            plan.setdefault("logs", []).append(f"[{timestamp}] {message}")

    def _ensure_step_skills(self, step: dict) -> list[str]:
        """
        Ensure the step's skills field exists and is traceable.

        Rules:
        - If the step already provides skills, keep them and deduplicate.
        - If no skills are provided but a tool is: try to match a system skill by tool_name.
        """
        skills = step.get("skills") or []
        if not isinstance(skills, list):
            skills = []

        if not skills:
            tool = step.get("tool")
            if tool:
                try:
                    for s in self.agent.skill_registry.list_all():
                        if getattr(s, "system", False) and getattr(s, "tool_name", None) == tool:
                            skills = [s.skill_id]
                            break
                except Exception:
                    pass

        seen = set()
        normalized: list[str] = []
        for name in skills:
            if not name or not isinstance(name, str):
                continue
            if name in seen:
                continue
            seen.add(name)
            normalized.append(name)
        return normalized


def create_todo_handler(agent: "Agent"):
    """Create the Plan Handler callable"""
    handler = PlanHandler(agent)
    return handler.handle
