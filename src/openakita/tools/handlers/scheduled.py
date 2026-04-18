"""
Scheduled task handler

Handles system skills related to scheduled tasks:
- schedule_task: Create scheduled task
- list_scheduled_tasks: List tasks
- cancel_scheduled_task: Cancel task
- update_scheduled_task: Update task
- trigger_scheduled_task: Trigger immediately
- query_task_executions: Query execution history
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class ScheduledHandler:
    """Scheduled task handler"""

    TOOLS = [
        "schedule_task",
        "list_scheduled_tasks",
        "cancel_scheduled_task",
        "update_scheduled_task",
        "trigger_scheduled_task",
        "query_task_executions",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    def _get_scheduler(self):
        """Get scheduler: prefer the agent's own, fall back to the global singleton (multi-agent mode)"""
        scheduler = getattr(self.agent, "task_scheduler", None)
        if scheduler:
            return scheduler
        from ...scheduler import get_active_scheduler

        return get_active_scheduler()

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle tool call"""
        scheduler = self._get_scheduler()
        if not scheduler:
            return "❌ Scheduled task scheduler is not running"
        self.agent.task_scheduler = scheduler

        if tool_name == "schedule_task":
            return await self._schedule_task(params)
        elif tool_name == "list_scheduled_tasks":
            return self._list_tasks(params)
        elif tool_name == "cancel_scheduled_task":
            return await self._cancel_task(params)
        elif tool_name == "update_scheduled_task":
            return await self._update_task(params)
        elif tool_name == "trigger_scheduled_task":
            return await self._trigger_task(params)
        elif tool_name == "query_task_executions":
            return self._query_executions(params)
        else:
            return f"❌ Unknown scheduled tool: {tool_name}"

    async def _schedule_task(self, params: dict) -> str:
        """Create scheduled task"""
        from ...core.im_context import get_im_session
        from ...scheduler import ScheduledTask, TriggerType
        from ...scheduler.task import TaskSource, TaskType

        # Validate required fields
        for field in ("name", "description", "trigger_type", "trigger_config"):
            if field not in params or not params[field]:
                return f"❌ Missing required parameter: {field}"

        try:
            trigger_type = TriggerType(params["trigger_type"])
        except ValueError:
            return f"❌ Unsupported trigger type: {params['trigger_type']} (supported: once, interval, cron)"

        try:
            task_type = TaskType(params.get("task_type", "reminder"))
        except ValueError:
            return f"❌ Unsupported task type: {params.get('task_type')} (supported: reminder, task)"

        trigger_config = params.get("trigger_config", {})
        if not isinstance(trigger_config, dict):
            return "❌ trigger_config must be an object"

        # ==================== Validate run_at sanity ====================
        if trigger_type == TriggerType.ONCE:
            try:
                now = datetime.now()
                run_at_raw = (params.get("trigger_config") or {}).get("run_at")
                if isinstance(run_at_raw, str):
                    parsed = datetime.fromisoformat(run_at_raw.strip())
                    delta = parsed - now

                    if delta.total_seconds() < -300:
                        return (
                            f"❌ run_at time {parsed.strftime('%Y-%m-%d %H:%M')} is already in the past. "
                            f"The current time is {now.strftime('%Y-%m-%d %H:%M')}.\n"
                            "Please recalculate the correct date and time based on the current time."
                        )

                    if delta.days > 365:
                        return (
                            f"⚠️ run_at time {parsed.strftime('%Y-%m-%d %H:%M')} is more than 1 year from now; "
                            "the date calculation may be incorrect. Please confirm the exact date with the user and retry."
                        )
            except ValueError:
                pass

        # Get current IM session info
        channel_id = chat_id = user_id = None
        session = get_im_session()
        if session:
            channel_id = session.channel
            chat_id = session.chat_id
            user_id = session.user_id

        # If the user specified target_channel, try to resolve it to a configured channel
        target_channel = params.get("target_channel")
        if target_channel:
            resolved = self._resolve_target_channel(target_channel)
            if resolved:
                channel_id, chat_id = resolved
                logger.info(f"Using target_channel={target_channel}: {channel_id}/{chat_id}")
            else:
                # Channel not configured or no available session, give a clear hint
                return (
                    f"❌ The specified channel '{target_channel}' is not configured or has no available session.\n"
                    f"Configured channels: {self._list_available_channels()}\n"
                    f"Please verify the channel name is correct and that the channel has at least one chat record."
                )

        task = ScheduledTask.create(
            name=params["name"],
            description=params["description"],
            trigger_type=trigger_type,
            trigger_config=params["trigger_config"],
            task_type=task_type,
            reminder_message=params.get("reminder_message"),
            prompt=params.get("prompt", ""),
            user_id=user_id,
            channel_id=channel_id,
            chat_id=chat_id,
            task_source=TaskSource.CHAT,
        )
        task.silent = bool(params.get("silent", False))
        task.no_schedule_tools = bool(params.get("no_schedule_tools", False))
        if params.get("skill_ids"):
            task.skill_ids = list(params["skill_ids"])
        task.metadata["notify_on_start"] = params.get("notify_on_start", True)
        task.metadata["notify_on_complete"] = params.get("notify_on_complete", True)

        try:
            task_id = await self.agent.task_scheduler.add_task(task)
        except ValueError as e:
            return f"❌ {e}"

        next_run = task.next_run.strftime("%Y-%m-%d %H:%M:%S") if task.next_run else "pending"

        type_display = "📝 Simple reminder" if task_type == TaskType.REMINDER else "🔧 Complex task"

        logger.info(
            "Scheduled task created: ID=%s, name=%s, type=%s, trigger=%s, next run=%s%s",
            task_id,
            task.name,
            type_display,
            task.trigger_type.value,
            next_run,
            f", notify channel={channel_id}/{chat_id}" if channel_id and chat_id else "",
        )

        logger.info(
            f"Created scheduled task: {task_id} ({task.name}), type={task_type.value}, next run: {next_run}"
        )

        return (
            f"✅ Created {type_display}\n- ID: {task_id}\n- Name: {task.name}\n- Next run: {next_run}"
            "\n\n[System hint] Task created successfully. Inform the user of the result directly; do not call schedule_task again."
        )

    def _list_tasks(self, params: dict) -> str:
        """List tasks"""
        enabled_only = params.get("enabled_only", False)
        tasks = self.agent.task_scheduler.list_tasks(enabled_only=enabled_only)

        if not tasks:
            return "No scheduled tasks currently"

        output = f"Total {len(tasks)} scheduled tasks:\n\n"
        for t in tasks:
            status = "✓" if t.enabled else "✗"
            next_run = t.next_run.strftime("%m-%d %H:%M") if t.next_run else "N/A"
            channel_info = f"{t.channel_id}/{t.chat_id}" if t.channel_id else "no channel"
            output += f"[{status}] {t.name} ({t.id})\n"
            output += f"    Type: {t.trigger_type.value}, Next: {next_run}, Push: {channel_info}\n"

        return output

    async def _cancel_task(self, params: dict) -> str:
        """Cancel task"""
        task_id = params.get("task_id")
        if not task_id:
            return "❌ Missing required parameter: task_id"

        result = await self.agent.task_scheduler.remove_task(task_id)

        if result == "ok":
            return f"✅ Task {task_id} has been cancelled"
        elif result == "system_task":
            return f"⚠️ '{task_id}' is a built-in system task and cannot be deleted. To pause it, use update_scheduled_task with enabled=false"
        else:
            return f"❌ Task {task_id} does not exist"

    async def _update_task(self, params: dict) -> str:
        """Update task (via scheduler public API)"""
        task_id = params.get("task_id")
        if not task_id:
            return "❌ Missing required parameter: task_id"
        task = self.agent.task_scheduler.get_task(task_id)
        if not task:
            return f"❌ Task {task_id} does not exist"

        changes = []
        updates: dict = {}

        if "notify_on_start" in params:
            metadata = dict(task.metadata)
            metadata["notify_on_start"] = params["notify_on_start"]
            updates["metadata"] = metadata
            changes.append("Start notification: " + ("on" if params["notify_on_start"] else "off"))
        if "notify_on_complete" in params:
            metadata = updates.get("metadata", dict(task.metadata))
            metadata["notify_on_complete"] = params["notify_on_complete"]
            updates["metadata"] = metadata
            changes.append("Complete notification: " + ("on" if params["notify_on_complete"] else "off"))

        if "target_channel" in params:
            target_channel = params["target_channel"]
            resolved = self._resolve_target_channel(target_channel)
            if resolved:
                updates["channel_id"] = resolved[0]
                updates["chat_id"] = resolved[1]
                changes.append(f"Push channel: {target_channel}")
            else:
                return (
                    f"❌ The specified channel '{target_channel}' is not configured or has no available session.\n"
                    f"Configured channels: {self._list_available_channels()}"
                )

        if updates:
            await self.agent.task_scheduler.update_task(task_id, updates)

        if "enabled" in params:
            if params["enabled"]:
                await self.agent.task_scheduler.enable_task(task_id)
                changes.append("Enabled")
            else:
                await self.agent.task_scheduler.disable_task(task_id)
                changes.append("Paused")

        if changes:
            return f"✅ Task {task.name} updated: " + ", ".join(changes)
        return "⚠️ No settings specified to modify"

    async def _trigger_task(self, params: dict) -> str:
        """Trigger task immediately"""
        task_id = params.get("task_id")
        if not task_id:
            return "❌ Missing required parameter: task_id"

        task = self.agent.task_scheduler.get_task(task_id)
        if not task:
            return f"❌ Task {task_id} does not exist"
        if not task.enabled:
            return f"⚠️ Task '{task.name}' is paused; please resume it before triggering"

        execution = await self.agent.task_scheduler.trigger_now(task_id)

        if execution:
            status = "success" if execution.status == "success" else "failure"
            return f"✅ Task triggered, status: {status}\nResult: {execution.result or execution.error or 'N/A'}"
        else:
            return f"❌ Task {task_id} is already running or cannot be triggered at the moment"

    def _get_gateway(self):
        """Get message gateway instance"""
        # Prefer getting from executor (executor holds the runtime gateway reference)
        executor = getattr(self.agent, "_task_executor", None)
        if executor and getattr(executor, "gateway", None):
            return executor.gateway

        # fallback: get from global executor (multi-agent mode)
        from ...scheduler import get_active_executor

        global_executor = get_active_executor()
        if global_executor and getattr(global_executor, "gateway", None):
            return global_executor.gateway

        # fallback: get from IM context
        from ...core.im_context import get_im_gateway

        return get_im_gateway()

    def _resolve_target_channel(self, target_channel: str) -> tuple[str, str] | None:
        """
        Resolve a user-specified channel name to (channel_id, chat_id)

        Strategy (fall back step by step):
        1. Check whether the gateway has an adapter for this channel (i.e. the channel is configured and started)
        2. Find the most recently active session for this channel from session_manager
        3. If no active session, try to look it up from the persisted sessions.json file
        4. Look up history from the channel registry channel_registry.json (not affected by session expiration)

        Args:
            target_channel: Channel name (e.g. wework, telegram, dingtalk)

        Returns:
            (channel_id, chat_id) or None
        """
        gateway = self._get_gateway()
        if not gateway:
            logger.warning("No gateway available to resolve target_channel")
            return None

        # 1. Check whether adapter exists
        adapters = getattr(gateway, "_adapters", {})
        if target_channel not in adapters:
            logger.warning(f"Channel '{target_channel}' not found in gateway adapters")
            return None

        adapter = adapters[target_channel]
        if not getattr(adapter, "is_running", False):
            logger.warning(f"Channel '{target_channel}' adapter is not running")
            return None

        # 2. Find the most recently active session for this channel from session_manager
        session_manager = getattr(gateway, "session_manager", None)
        if session_manager:
            sessions = session_manager.list_sessions(channel=target_channel)
            if sessions:
                # Sort by most recently active
                sessions.sort(
                    key=lambda s: getattr(s, "last_active", datetime.min),
                    reverse=True,
                )
                best = sessions[0]
                return (best.channel, best.chat_id)

        # 3. Look up from the persisted file
        if session_manager:
            import json

            sessions_file = getattr(session_manager, "storage_path", None)
            if sessions_file:
                sessions_file = sessions_file / "sessions.json"
                if sessions_file.exists():
                    try:
                        with open(sessions_file, encoding="utf-8") as f:
                            raw_sessions = json.load(f)
                        # Filter sessions of this channel
                        channel_sessions = [
                            s
                            for s in raw_sessions
                            if s.get("channel") == target_channel and s.get("chat_id")
                        ]
                        if channel_sessions:
                            channel_sessions.sort(
                                key=lambda s: s.get("last_active", ""),
                                reverse=True,
                            )
                            best = channel_sessions[0]
                            return (best["channel"], best["chat_id"])
                    except Exception as e:
                        logger.error(f"Failed to read sessions file: {e}")

        # 4. Look up history from the channel registry (not affected by session expiration)
        if session_manager and hasattr(session_manager, "get_known_channel_target"):
            known = session_manager.get_known_channel_target(target_channel)
            if known:
                logger.info(
                    f"Resolved target_channel='{target_channel}' from channel registry: "
                    f"chat_id={known[1]}"
                )
                return known

        logger.warning(
            f"Channel '{target_channel}' is configured but no session found "
            f"(neither active session nor channel registry). "
            f"Please send at least one message through this channel first."
        )
        return None

    def _list_available_channels(self) -> str:
        """List all configured and running IM channel names"""
        gateway = self._get_gateway()
        if not gateway:
            return "(unable to retrieve channel info)"

        adapters = getattr(gateway, "_adapters", {})
        if not adapters:
            return "(no configured channels)"

        running = []
        for name, adapter in adapters.items():
            status = "✓" if getattr(adapter, "is_running", False) else "✗"
            running.append(f"{name}({status})")

        return ", ".join(running) if running else "(no configured channels)"

    def _query_executions(self, params: dict) -> str:
        """Query execution history"""
        task_id = params.get("task_id")
        limit = min(params.get("limit", 10), 50)

        execs = self.agent.task_scheduler.get_executions(task_id=task_id, limit=limit)

        if not execs:
            if task_id:
                return f"Task {task_id} has no execution records yet"
            return "No task execution records yet"

        lines = []
        for e in reversed(execs):
            time_str = e.started_at.strftime("%m-%d %H:%M") if e.started_at else "?"
            status_icon = "✅" if e.status == "success" else "❌"
            duration = f"{e.duration_seconds:.1f}s" if e.duration_seconds else "-"
            line = f"  {status_icon} {time_str} | duration {duration}"
            if e.error:
                line += f" | error: {e.error[:100]}"
            lines.append(line)

        header = f"task {task_id}'s " if task_id else ""
        return f"📋 {header}most recent {len(execs)} execution records:\n" + "\n".join(lines)


def create_handler(agent: "Agent"):
    """Create scheduled task handler"""
    handler = ScheduledHandler(agent)
    return handler.handle
