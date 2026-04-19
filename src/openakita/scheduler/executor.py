"""
Task executor

Responsible for actually running scheduled tasks:
- Create an Agent session
- Send the prompt to the Agent
- Collect execution results
- Send result notifications
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .task import ScheduledTask

logger = logging.getLogger(__name__)


class TaskExecutor:
    """
    Task executor

    Converts scheduled tasks into Agent invocations.
    """

    def __init__(
        self,
        agent_factory: Callable[[], Any] | None = None,
        gateway: Any | None = None,
        timeout_seconds: int = 1200,  # 20-minute timeout
    ):
        """
        Args:
            agent_factory: Agent factory function
            gateway: message gateway (used to send result notifications)
            timeout_seconds: execution timeout in seconds, default 1200 (20 minutes)
        """
        self.agent_factory = agent_factory
        self.gateway = gateway
        self.timeout_seconds = timeout_seconds
        # Optional: set by the Agent, used for system tasks such as the proactive heartbeat
        self.persona_manager = None
        self.memory_manager = None
        self.proactive_engine = None  # Reuse the instance on agent to preserve _last_user_interaction state

    def _escape_telegram_chars(self, text: str) -> str:
        """
        Escape every special character required by Telegram MarkdownV2.

        Per the official docs, these 18 characters must be escaped:
        _ * [ ] ( ) ~ ` > # + - = | { } . !

        Strategy: escape all of them so the message can be sent successfully.
        """
        # All characters that must be escaped in MarkdownV2
        escape_chars = [
            "_",
            "*",
            "[",
            "]",
            "(",
            ")",
            "~",
            "`",
            ">",
            "#",
            "+",
            "-",
            "=",
            "|",
            "{",
            "}",
            ".",
            "!",
        ]

        for char in escape_chars:
            text = text.replace(char, "\\" + char)

        return text

    async def execute(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        Execute a task.

        Uses different strategies depending on the task type:
        - REMINDER: simple reminder, sends a message directly
        - TASK: complex task, notify start → LLM executes → notify end

        Args:
            task: task to execute

        Returns:
            (success, result_or_error)
        """
        logger.info(
            f"TaskExecutor: executing task {task.id} ({task.name}) [type={task.task_type.value}]"
        )

        # Resolve chat_id at runtime if the task has a channel but no chat_id
        if task.channel_id and not task.chat_id and self.gateway:
            sm = getattr(self.gateway, "session_manager", None)
            if sm:
                target = sm.get_known_channel_target(task.channel_id)
                if target:
                    task.chat_id = target[1]
                    logger.info(
                        f"TaskExecutor: resolved chat_id for {task.channel_id} → {task.chat_id}"
                    )

        # Pick execution strategy by task type
        if task.is_reminder:
            return await self._execute_reminder(task)
        else:
            return await self._execute_complex_task(task)

    async def _execute_reminder(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        Execute a simple reminder task.

        Flow:
        1. Send the reminder message first (only once!) — skipped in silent mode
        2. Let the LLM decide whether additional action is required (to avoid misclassification)

        Note: a simple reminder only sends one message; it does not send a "task completed" notification.
        """
        logger.info(f"TaskExecutor: executing reminder {task.id}")

        try:
            message = task.reminder_message or task.prompt or f"⏰ Reminder: {task.name}"

            if task.silent:
                logger.info(f"TaskExecutor: reminder {task.id} in silent mode, skipping delivery")
                return True, f"[SILENT] {message}"

            message_sent = False

            if task.channel_id and task.chat_id and self.gateway:
                message_sent = await self._deliver_reminder_message(task, message)
            elif self.gateway:
                # Gateway available but the task has no channel configured; try all known channels
                message_sent = await self._deliver_via_fallback_channels(task, message)
            # else: no gateway, cannot send

            if not message_sent:
                # Final fallback: try desktop notification
                desktop_sent = await self._try_desktop_notify_fallback(task, message)
                if not desktop_sent:
                    return (
                        False,
                        f"Reminder delivery failed: all channels unavailable, reminder content \"{message[:50]}\" was not delivered",
                    )

                return True, f"Reminder delivered via desktop notification (IM channel unavailable): {message[:80]}"

            should_execute = await self._check_if_needs_execution(task)

            if should_execute:
                logger.info(
                    f"TaskExecutor: reminder {task.id} needs additional execution, upgrading to task"
                )
                return await self._execute_complex_task_core(
                    task, skip_end_notification=message_sent
                )

            logger.info(f"TaskExecutor: reminder {task.id} completed (no additional action needed)")
            return True, message

        except Exception as e:
            error_msg = str(e)
            logger.error(f"TaskExecutor: reminder {task.id} failed: {error_msg}")
            return False, error_msg

    async def _deliver_reminder_message(self, task: ScheduledTask, message: str) -> bool:
        """
        Deliver the reminder message to the task's primary channel.

        Returns:
            True if the message was likely delivered (including the case where msg_id=None but the channel is active)
        """
        channel_id = task.channel_id
        chat_id = task.chat_id

        # Check whether the primary channel adapter exists and is running
        adapter = (
            self.gateway.get_adapter(channel_id) if hasattr(self.gateway, "get_adapter") else None
        )
        channel_active = adapter is not None and getattr(adapter, "is_running", False)

        try:
            msg_id = await self.gateway.send(
                channel=channel_id,
                chat_id=chat_id,
                text=message,
            )
        except Exception as e:
            logger.warning(f"TaskExecutor: reminder {task.id} primary send error: {e}")
            msg_id = None
            channel_active = False

        if msg_id is not None:
            logger.info(f"TaskExecutor: reminder {task.id} delivered (msg_id={msg_id})")
            return True

        if channel_active:
            logger.warning(
                f"TaskExecutor: reminder {task.id} sent to active channel "
                f"{channel_id}/{chat_id} but no msg_id returned (likely delivered)"
            )
            return True

        logger.warning(
            f"TaskExecutor: reminder {task.id} failed on primary channel "
            f"{channel_id}/{chat_id} (inactive), trying fallback channels"
        )
        return await self._deliver_via_fallback_channels(task, message)

    async def _deliver_via_fallback_channels(self, task: ScheduledTask, message: str) -> bool:
        """Try to deliver the reminder through every known fallback IM channel"""
        targets = self._find_all_im_targets()
        primary = (task.channel_id, task.chat_id)

        for channel, chat_id in targets:
            if (channel, chat_id) == primary:
                continue  # Primary channel already failed, skip

            adapter = (
                self.gateway.get_adapter(channel) if hasattr(self.gateway, "get_adapter") else None
            )
            if not adapter or not getattr(adapter, "is_running", False):
                continue

            try:
                msg_id = await self.gateway.send(
                    channel=channel,
                    chat_id=chat_id,
                    text=message,
                )
                if msg_id is not None or (adapter and getattr(adapter, "is_running", False)):
                    logger.info(
                        f"TaskExecutor: reminder {task.id} delivered via fallback "
                        f"{channel}/{chat_id} (msg_id={msg_id})"
                    )
                    return True
            except Exception as e:
                logger.warning(f"TaskExecutor: fallback send failed for {channel}/{chat_id}: {e}")
                continue

        return False

    async def _try_desktop_notify_fallback(self, task: ScheduledTask, message: str) -> bool:
        """When all IM channels fail, try desktop notification as a last-resort fallback"""
        try:
            from ..config import settings

            if settings.desktop_notify_enabled:
                from ..core.desktop_notify import notify_task_completed_async

                await notify_task_completed_async(
                    f"⏰ {task.name}: {message[:100]}",
                    success=True,
                    sound=settings.desktop_notify_sound,
                )
                logger.info(f"TaskExecutor: reminder {task.id} delivered via desktop notification")
                return True
        except Exception as e:
            logger.debug(f"Desktop notification fallback failed for {task.id}: {e}")

        return False

    async def _check_if_needs_execution(self, task: ScheduledTask) -> bool:
        """
        Let the LLM decide whether a reminder task requires additional execution.

        Guards against misclassifications at task-creation time where a complex task was
        set up as a simple reminder.

        Note: this method is only for classification and must not send any messages.
        """
        try:
            # Clear IM context to prevent accidental message sends during classification
            from ..core.im_context import (
                get_im_gateway,
                get_im_session,
                reset_im_context,
                set_im_context,
            )

            _ = get_im_session()
            _ = get_im_gateway()
            tokens = set_im_context(session=None, gateway=None)

            try:
                # Use Brain directly for classification instead of creating a full Agent (lighter and won't send messages)
                from ..core.brain import Brain

                brain = Brain()

                check_prompt = f"""Please decide whether the following scheduled reminder requires additional action:

Task name: {task.name}
Task description: {task.description}
Reminder content: {task.reminder_message or task.prompt}

Criteria:
- Simple reminder: only notify the user (e.g. drink water, take a break, stand up, meeting reminder) → NO_ACTION
- Complex task: requires the AI to perform a concrete action (e.g. look up the weather and report it, run a script, analyze data) → NEEDS_ACTION

Reply with only NO_ACTION or NEEDS_ACTION, nothing else."""

                response = await brain.think(check_prompt)
                result = response.content.strip().upper()

                needs_action = "NEEDS_ACTION" in result
                logger.info(f"LLM decision for reminder {task.id}: {result}")

                return needs_action

            finally:
                # Restore IM context
                reset_im_context(tokens)

        except Exception as e:
            logger.warning(f"Failed to check reminder execution: {e}, assuming no action needed")
            return False

    async def _execute_complex_task(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        Execute a complex task.

        Flow:
        1. Send start notification (skipped in silent mode)
        2. Run the core task logic
        """
        logger.info(f"TaskExecutor: executing complex task {task.id}")

        if not task.silent:
            await self._send_start_notification(task)

        return await self._execute_complex_task_core(task, skip_end_notification=task.silent)

    async def _execute_complex_task_core(
        self, task: ScheduledTask, skip_end_notification: bool = False
    ) -> tuple[bool, str]:
        """
        Core execution logic for a complex task.

        Can be called by _execute_complex_task and by _execute_reminder (on upgrade).

        Args:
            task: task to execute
            skip_end_notification: whether to skip the end notification (used when upgrading from a reminder)
        """
        # Check whether this is a system task (needs special handling)
        if task.action and task.action.startswith("system:"):
            return await self._execute_system_task(task)

        agent = None
        im_context_set = False
        try:
            # 1. Create the Agent
            agent = await self._create_agent()

            # 1.5. Recursion guard: forbid tasks from creating more scheduled tasks
            if task.no_schedule_tools:
                agent._cron_disabled_tools = {
                    "schedule_task", "update_scheduled_task",
                    "cancel_scheduled_task", "trigger_scheduled_task",
                }

            # 2. If the task has IM channel info, inject the IM context
            if task.channel_id and task.chat_id and self.gateway:
                im_context_set = await self._setup_im_context(agent, task)

            # 3. Build the execution prompt (simplified; the Agent should not send messages itself)
            prompt = self._build_prompt(task, suppress_send_to_chat=True)

            # 4. Execute (with timeout, supports per-task metadata.timeout_seconds override)
            task_timeout = self.timeout_seconds
            if task.metadata and isinstance(task.metadata, dict):
                custom_timeout = task.metadata.get("timeout_seconds")
                if (
                    custom_timeout
                    and isinstance(custom_timeout, (int, float))
                    and custom_timeout > 0
                ):
                    task_timeout = int(custom_timeout)
                    logger.info(
                        f"TaskExecutor: using task-level timeout {task_timeout}s "
                        f"(default: {self.timeout_seconds}s)"
                    )
            try:
                result = await asyncio.wait_for(
                    self._run_agent(agent, prompt), timeout=task_timeout
                )
            except (asyncio.TimeoutError, TimeoutError):
                timeout_display = (
                    f"{task_timeout // 60} minutes" if task_timeout >= 60 else f"{task_timeout} seconds"
                )
                error_msg = f"Task execution timed out (not finished after {timeout_display})"
                logger.error(f"TaskExecutor: task {task.id} timed out after {task_timeout}s")
                if not skip_end_notification:
                    await self._send_end_notification(task, success=False, message=error_msg)
                return False, error_msg

            # 5. Send result notification (if needed)
            agent_sent = getattr(agent, "_task_message_sent", False)
            if not agent_sent and not skip_end_notification:
                await self._send_end_notification(task, success=True, message=result)

            logger.info(f"TaskExecutor: task {task.id} completed successfully")
            return True, result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"TaskExecutor: task {task.id} failed: {error_msg}", exc_info=True)
            if not skip_end_notification:
                await self._send_end_notification(task, success=False, message=error_msg)
            return False, error_msg
        finally:
            # Clean up IM context
            if agent and im_context_set:
                self._cleanup_im_context(agent)
            # Clean up Agent (ensure timeout/exception paths also run this)
            if agent:
                with contextlib.suppress(Exception):
                    await self._cleanup_agent(agent)

    async def _send_start_notification(self, task: ScheduledTask) -> None:
        """Send the task-start notification"""
        if not task.channel_id or not task.chat_id or not self.gateway:
            return

        # Check whether start notifications are enabled
        if not task.metadata.get("notify_on_start", True):
            logger.debug(f"Task {task.id} has start notification disabled")
            return

        try:
            notification = f"🚀 Starting task: {task.name}\n\nPlease wait, I'm working on it..."

            await self.gateway.send(
                channel=task.channel_id,
                chat_id=task.chat_id,
                text=notification,
            )
            logger.info(f"Sent start notification for task {task.id}")

        except Exception as e:
            logger.error(f"Failed to send start notification: {e}")

    async def _send_end_notification(
        self,
        task: ScheduledTask,
        success: bool,
        message: str,
    ) -> None:
        """Send the task-end notification (IM channel + desktop notification)"""
        # Desktop notification (independent of IM channel; always attempted)
        try:
            from ..config import settings

            if settings.desktop_notify_enabled:
                from ..core.desktop_notify import notify_task_completed_async

                await notify_task_completed_async(
                    task.name,
                    success=success,
                    sound=settings.desktop_notify_sound,
                )
        except Exception as e:
            logger.debug(f"Desktop notification failed for task {task.id}: {e}")

        # IM channel notification
        if not task.channel_id or not task.chat_id or not self.gateway:
            logger.debug(f"Task {task.id} has no notification channel configured")
            return

        if not task.metadata.get("notify_on_complete", True):
            logger.debug(f"Task {task.id} has completion notification disabled")
            return

        try:
            status = "✅ Task completed" if success else "❌ Task failed"
            notification = f"""{status}: {task.name}

Result:
{message}
"""

            await self.gateway.send(
                channel=task.channel_id,
                chat_id=task.chat_id,
                text=notification,
            )

            logger.info(f"Sent end notification for task {task.id}")

        except Exception as e:
            logger.error(f"Failed to send end notification: {e}")

    async def _setup_im_context(self, agent: Any, task: ScheduledTask) -> bool:
        """
        Inject IM context for a scheduled task so the Agent can use IM tools
        (e.g. deliver_artifacts / get_chat_history).
        Returns True on successful setup (the caller should run _cleanup_im_context in finally).
        """
        try:
            from ..core.im_context import set_im_context
            from ..sessions import Session

            virtual_session = Session.create(
                channel=task.channel_id,
                chat_id=task.chat_id,
                user_id=task.user_id or "scheduled_task",
            )

            tokens = set_im_context(session=virtual_session, gateway=self.gateway)
            # Store the token on the agent so we can reset symmetrically
            agent._im_context_tokens = tokens

            logger.info(f"Set up IM context for task {task.id}: {task.channel_id}/{task.chat_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to set up IM context: {e}", exc_info=True)
            return False

    def _cleanup_im_context(self, agent: Any) -> None:
        """Symmetrically clean up IM context (use reset_im_context to restore the original state)"""
        try:
            tokens = getattr(agent, "_im_context_tokens", None)
            if tokens:
                from ..core.im_context import reset_im_context

                reset_im_context(tokens)
                agent._im_context_tokens = None
        except Exception as e:
            logger.warning(f"Failed to cleanup IM context: {e}")

    async def _create_agent(self) -> Any:
        """Create an Agent instance (scheduler not started, to avoid re-executing tasks)"""
        if self.agent_factory:
            return self.agent_factory()

        from ..core.agent import Agent

        agent = Agent()
        await agent.initialize(start_scheduler=False)
        return agent

    async def _run_agent(self, agent: Any, prompt: str) -> str:
        """
        Run the Agent (using Ralph mode).

        Prefer execute_task_from_message (Ralph-loop mode), which supports multi-turn
        tool calls until the task is complete.
        """
        # Prefer Ralph mode (execute_task_from_message)
        if hasattr(agent, "execute_task_from_message"):
            result = await agent.execute_task_from_message(prompt)
            if isinstance(result, str):
                return result
            return result.data if result.success else (result.error or "Unknown error")
        # Fall back to plain chat
        elif hasattr(agent, "chat"):
            return await agent.chat(prompt)
        else:
            raise ValueError("Agent does not have execute_task_from_message or chat method")

    async def _cleanup_agent(self, agent: Any) -> None:
        """Clean up the Agent"""
        if hasattr(agent, "shutdown"):
            await agent.shutdown()

    async def _execute_system_task(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        Execute a built-in system task (with timeout protection).

        Calls the relevant system method directly rather than going through the LLM.

        Supported system tasks:
        - system:daily_memory - daily memory consolidation
        - system:daily_selfcheck - daily system self-check
        - system:proactive_heartbeat - proactive heartbeat
        - system:workspace_backup - scheduled workspace backup
        - system:memory_nudge_review - periodic memory review
        """
        action = task.action
        logger.info(f"Executing system task: {action}")

        # System tasks also need timeout protection, so tasks like selfcheck don't run forever
        SYSTEM_TASK_TIMEOUTS = {
            "system:daily_selfcheck": 300,  # 5 minutes
            "system:daily_memory": 1800,  # 30 minutes (LLM reviews a lot of memories)
            "system:workspace_backup": 300,  # 5 minutes
            "system:memory_nudge_review": 120,  # 2 minutes (lightweight LLM review)
        }
        timeout = SYSTEM_TASK_TIMEOUTS.get(action)

        try:
            if action == "system:daily_memory":
                coro = self._system_daily_memory()
            elif action == "system:daily_selfcheck":
                coro = self._system_daily_selfcheck()
            elif action == "system:proactive_heartbeat":
                return await self._system_proactive_heartbeat(task)
            elif action == "system:workspace_backup":
                coro = self._system_workspace_backup()
            elif action == "system:memory_nudge_review":
                coro = self._system_memory_nudge_review()
            else:
                return False, f"Unknown system action: {action}"

            if timeout:
                try:
                    return await asyncio.wait_for(coro, timeout=timeout)
                except (asyncio.TimeoutError, TimeoutError):
                    error_msg = f"System task {action} timed out after {timeout}s"
                    logger.error(f"TaskExecutor: {error_msg}")
                    return False, error_msg
            else:
                return await coro

        except Exception as e:
            logger.error(f"System task {action} failed: {e}")
            return False, str(e)

    async def _system_daily_memory(self) -> tuple[bool, str]:
        """
        Run memory consolidation.

        Prefer reusing the MemoryManager on the agent (complete configuration);
        only fall back to creating a new instance when none is available.

        Uses ConsolidationTracker to record the consolidation timestamps so the
        processing always covers records from "last consolidation up to now".
        """
        try:
            from ..config import settings
            from .consolidation_tracker import ConsolidationTracker

            tracker = ConsolidationTracker(settings.project_root / "data" / "scheduler")
            since, until = tracker.get_memory_consolidation_time_range()

            if since:
                logger.info(
                    f"Memory consolidation time range: {since.isoformat()} → {until.isoformat()}"
                )
            else:
                logger.info("Memory consolidation: first run, processing all records")

            mm = self.memory_manager
            if not mm:
                from ..core.brain import Brain
                from ..memory import MemoryManager

                brain = Brain()
                mm = MemoryManager(
                    data_dir=settings.project_root / "data" / "memory",
                    memory_md_path=settings.memory_path,
                    brain=brain,
                    embedding_model=settings.embedding_model,
                    embedding_device=settings.embedding_device,
                    model_download_source=settings.model_download_source,
                    search_backend=settings.search_backend,
                    embedding_api_provider=settings.embedding_api_provider,
                    embedding_api_key=settings.embedding_api_key,
                    embedding_api_model=settings.embedding_api_model,
                )
                logger.debug("Created fallback MemoryManager for consolidation")

            result = await mm.consolidate_daily()

            tracker.record_memory_consolidation(result)

            v2_keys = ["unextracted_processed", "duplicates_removed", "memories_decayed"]
            _v1_keys = ["sessions_processed", "memories_extracted", "memories_added"]

            if any(result.get(k) for k in v2_keys):
                summary = (
                    f"Memory consolidation completed (v2):\n"
                    f"- Extracted: {result.get('unextracted_processed', 0)}\n"
                    f"- Duplicates removed: {result.get('duplicates_removed', 0)}\n"
                    f"- Decayed: {result.get('memories_decayed', 0)}\n"
                    f"- Time range: {since.strftime('%m-%d %H:%M') if since else 'all'} → {until.strftime('%m-%d %H:%M')}"
                )
            else:
                summary = (
                    f"Memory consolidation completed:\n"
                    f"- Sessions processed: {result.get('sessions_processed', 0)}\n"
                    f"- Memories extracted: {result.get('memories_extracted', 0)}\n"
                    f"- Memories added: {result.get('memories_added', 0)}\n"
                    f"- Duplicates removed: {result.get('duplicates_removed', 0)}\n"
                    f"- MEMORY.md: {'refreshed' if result.get('memory_md_refreshed') else 'not refreshed'}\n"
                    f"- Time range: {since.strftime('%m-%d %H:%M') if since else 'all'} → {until.strftime('%m-%d %H:%M')}"
                )

            logger.info(f"Memory consolidation completed: {result}")
            return True, summary

        except Exception as e:
            logger.error(f"Memory consolidation failed: {e}")
            return False, str(e)

    async def _system_memory_nudge_review(self) -> tuple[bool, str]:
        """
        Periodic memory review (Memory Nudge).

        Use the LLM to review recent conversation turns and extract any long-term
        memories that may have been missed. Complements daily_memory: daily does
        end-to-end full consolidation, while nudge is a lightweight, near-real-time
        top-up that ensures important information isn't lost to context compression.
        """
        try:
            from ..config import settings

            if not settings.memory_nudge_enabled or settings.memory_nudge_interval <= 0:
                return True, "Memory nudge disabled, skipping"

            mm = self.memory_manager
            if not mm:
                return True, "No MemoryManager available, skipping nudge"

            store = getattr(mm, "store", None)
            if not store:
                return True, "No memory store available, skipping nudge"

            nudge_interval = settings.memory_nudge_interval

            recent_turns = store.get_global_recent_turns(limit=nudge_interval)
            if not recent_turns:
                return True, "No recent conversation turns to review"

            conversation_text = "\n".join(
                f"[{t.get('role', 'unknown')}]: {t.get('content', '')[:500]}"
                for t in recent_turns
                if t.get("content")
            )

            if not conversation_text.strip():
                return True, "Recent turns have no meaningful content"

            from ..core.brain import Brain

            brain = Brain()

            review_prompt = (
                "You are a memory extraction assistant. Review the following recent "
                "conversation and identify any facts, preferences, skills, rules, or "
                "important context that should be remembered long-term. "
                "Return ONLY a JSON array of objects with keys: "
                '"type" (fact/preference/skill/rule/context), '
                '"content" (the memory text), '
                '"importance" (1-5). '
                "If nothing worth remembering, return an empty array [].\n\n"
                f"Conversation:\n{conversation_text}"
            )

            response = await brain.think_lightweight(review_prompt, max_tokens=2048)
            raw = response.content.strip()

            import json

            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            memories = json.loads(raw)
            if not isinstance(memories, list):
                return True, "LLM returned non-list response, skipping"

            from ..memory.types import Memory, MemoryPriority, MemoryType

            type_map = {
                "fact": MemoryType.FACT,
                "preference": MemoryType.PREFERENCE,
                "skill": MemoryType.SKILL,
                "rule": MemoryType.RULE,
                "context": MemoryType.CONTEXT,
                "experience": MemoryType.EXPERIENCE,
            }
            importance_map = {
                1: MemoryPriority.TRANSIENT,
                2: MemoryPriority.SHORT_TERM,
                3: MemoryPriority.SHORT_TERM,
                4: MemoryPriority.LONG_TERM,
                5: MemoryPriority.PERMANENT,
            }

            added = 0
            for mem in memories:
                if not isinstance(mem, dict) or "content" not in mem:
                    continue
                importance = mem.get("importance", 3)
                if importance < 2:
                    continue
                try:
                    m = Memory(
                        type=type_map.get(mem.get("type", "fact"), MemoryType.FACT),
                        priority=importance_map.get(importance, MemoryPriority.SHORT_TERM),
                        content=mem["content"],
                        source="memory_nudge",
                    )
                    mm.add_memory(m)
                    added += 1
                except Exception as e:
                    logger.debug(f"Memory nudge: failed to add memory: {e}")

            summary = f"Memory nudge completed: reviewed {len(recent_turns)} turns, extracted {added} memories"
            logger.info(summary)
            return True, summary

        except Exception as e:
            logger.error(f"Memory nudge review failed: {e}")
            return False, str(e)

    async def _system_proactive_heartbeat(self, task: "ScheduledTask") -> tuple[bool, str]:
        """
        Execute the proactive heartbeat.

        Fires every 30 minutes; most of the time it just checks and skips.
        Only when all conditions are met does it actually generate and send a message.

        Prefer reusing the ProactiveEngine instance on the agent (retains _last_user_interaction state);
        only fall back to creating a new instance when none is available (in which case idle_chat is unavailable).
        """
        try:
            from ..config import settings

            engine = self.proactive_engine
            if not engine:
                # No engine instance; check settings first to decide whether creating one is worthwhile
                if not settings.proactive_enabled:
                    return True, "Proactive mode disabled, skipping heartbeat"

                # Fallback: create a new instance (idle_chat unavailable)
                from ..core.proactive import ProactiveConfig, ProactiveEngine

                config = ProactiveConfig(
                    enabled=settings.proactive_enabled,
                    max_daily_messages=settings.proactive_max_daily_messages,
                    min_interval_minutes=settings.proactive_min_interval_minutes,
                    quiet_hours_start=settings.proactive_quiet_hours_start,
                    quiet_hours_end=settings.proactive_quiet_hours_end,
                    idle_threshold_hours=settings.proactive_idle_threshold_hours,
                )

                feedback_file = settings.project_root / "data" / "proactive_feedback.json"
                engine = ProactiveEngine(
                    config=config,
                    feedback_file=feedback_file,
                    persona_manager=self.persona_manager,
                    memory_manager=self.memory_manager,
                )
                logger.debug(
                    "ProactiveEngine fallback: created new instance (idle_chat unavailable)"
                )

            # Execute the heartbeat
            result = await engine.heartbeat()

            if not result:
                return True, "Heartbeat check passed, no message needed"

            # Send the message
            msg_content = result.get("content", "")
            msg_type = result.get("type", "unknown")

            if msg_content and self.gateway:
                # Look for an active IM channel
                targets = self._find_all_im_targets()
                for channel, chat_id in targets:
                    try:
                        await self.gateway.send(
                            channel=channel,
                            chat_id=chat_id,
                            text=msg_content,
                        )

                        # Send a sticker if required
                        sticker_mood = result.get("sticker_mood")
                        if sticker_mood and settings.sticker_enabled:
                            try:
                                from ..tools.sticker import StickerEngine

                                sticker_engine = StickerEngine(
                                    settings.sticker_data_path,
                                    mirrors=settings.sticker_mirrors or None,
                                )
                                await sticker_engine.initialize()
                                sticker = await sticker_engine.get_random_by_mood(sticker_mood)
                                if sticker:
                                    local_path = await sticker_engine.download_and_cache(
                                        sticker["url"]
                                    )
                                    if local_path:
                                        adapter = self.gateway.get_adapter(channel)
                                        if adapter:
                                            await adapter.send_image(chat_id, str(local_path))
                            except Exception as e:
                                logger.debug(f"Failed to send sticker with proactive message: {e}")

                        logger.info(f"Sent proactive message ({msg_type}) to {channel}/{chat_id}")
                        return True, f"Sent {msg_type} message: {msg_content[:50]}..."
                    except Exception as e:
                        logger.warning(
                            f"Failed to send proactive message to {channel}/{chat_id}: {e}"
                        )

            return True, f"Generated {msg_type} message but no active IM channel"

        except Exception as e:
            logger.error(f"Proactive heartbeat failed: {e}")
            return False, str(e)

    async def _system_daily_selfcheck(self) -> tuple[bool, str]:
        """
        Run the system self-check.

        Uses ConsolidationTracker to record self-check timestamps so the analysis
        always covers logs from "last self-check up to now".
        """
        try:
            from datetime import datetime

            from ..config import settings
            from ..core.brain import Brain
            from ..evolution import SelfChecker
            from ..logging import LogCleaner
            from .consolidation_tracker import ConsolidationTracker

            tracker = ConsolidationTracker(settings.project_root / "data" / "scheduler")
            since, until = tracker.get_selfcheck_time_range()

            if since:
                logger.info(f"Selfcheck time range: {since.isoformat()} → {until.isoformat()}")
            else:
                logger.info("Selfcheck: first run")

            # 1. Clean up old logs
            log_cleaner = LogCleaner(
                log_dir=settings.log_dir_path,
                retention_days=settings.log_retention_days,
            )
            cleanup_result = log_cleaner.cleanup()

            # 2. Run the self-check (pass the time range, reuse the agent's memory_manager to avoid DB lock conflicts)
            brain = Brain()
            checker = SelfChecker(brain=brain, memory_manager=self.memory_manager)
            report = await checker.run_daily_check(since=since)

            # 2.1 Render Markdown report text (for IM delivery)
            report_md = None
            try:
                report_md = report.to_markdown() if hasattr(report, "to_markdown") else str(report)
            except Exception as e:
                logger.warning(f"Failed to render report markdown: {e}")
                report_md = None

            # 2.2 Push the report to the most recently active IM channel (no time limit, try them one by one)
            pushed = 0
            push_target = ""
            if report_md and self.gateway and getattr(self.gateway, "session_manager", None):
                report_date = getattr(report, "date", "") or datetime.now().strftime("%Y-%m-%d")
                targets = self._find_all_im_targets()
                for channel, chat_id in targets:
                    try:
                        adapter = self.gateway.get_adapter(channel)
                        if not adapter or not adapter.is_running:
                            continue
                        await self._send_report_chunks(adapter, chat_id, report_md, report_date)
                        pushed = 1
                        push_target = f"{channel}/{chat_id}"
                        break  # Delivered successfully, stop trying
                    except Exception as e:
                        logger.warning(
                            f"Failed to push selfcheck report via {channel}/{chat_id}: {e}"
                        )
                        continue  # Try the next channel

                if pushed > 0:
                    with contextlib.suppress(Exception):
                        checker.mark_report_as_reported(getattr(report, "date", None))

            # 3. Record the self-check timestamp
            tracker.record_selfcheck(
                {
                    "total_errors": report.total_errors,
                    "fix_success": report.fix_success,
                }
            )

            # 4. Format the result
            push_info = push_target if pushed else "No channel available (will retry on the user's next message)"
            time_range_info = (
                f"{since.strftime('%m-%d %H:%M')} → {until.strftime('%m-%d %H:%M')}"
                if since
                else "First run"
            )

            summary = (
                f"System self-check completed:\n"
                f"- Total errors: {report.total_errors}\n"
                f"- Core component errors: {report.core_errors} (require manual attention)\n"
                f"- Tool errors: {report.tool_errors}\n"
                f"- Fix attempted: {report.fix_attempted}\n"
                f"- Fix succeeded: {report.fix_success}\n"
                f"- Fix failed: {report.fix_failed}\n"
                f"- Log cleanup: removed {cleanup_result.get('by_age', 0) + cleanup_result.get('by_size', 0)} old files\n"
                f"- Analysis range: {time_range_info}\n"
                f"- Report push: {push_info}"
            )

            logger.info(
                f"Selfcheck completed: {report.total_errors} errors, {report.fix_success} fixed"
            )
            return True, summary

        except Exception as e:
            logger.error(f"Daily selfcheck failed: {e}")
            return False, str(e)

    async def _system_workspace_backup(self) -> tuple[bool, str]:
        """Run a scheduled workspace backup."""
        try:
            from ..config import settings
            from ..workspace.backup import create_backup, read_backup_settings

            ws_path = settings.project_root
            bs = read_backup_settings(ws_path)

            backup_path = bs.get("backup_path", "")
            if not backup_path:
                return False, "Backup path not configured"

            zip_path = create_backup(
                workspace_path=ws_path,
                output_dir=backup_path,
                include_userdata=bs.get("include_userdata", True),
                include_media=bs.get("include_media", False),
                max_backups=bs.get("max_backups", 5),
            )

            size_mb = zip_path.stat().st_size / 1024 / 1024
            summary = f"Workspace backup completed: {zip_path.name} ({size_mb:.1f} MB)"
            logger.info(summary)
            return True, summary

        except Exception as e:
            logger.error(f"Workspace backup failed: {e}")
            return False, str(e)

    def _find_all_im_targets(self) -> list[tuple[str, str]]:
        """
        Find all available IM channels (sorted by recency, deduplicated).

        Prefers sessions from memory; then fills in from the persisted sessions.json file.
        Returns a deduplicated list of (channel, chat_id) pairs so the caller can try them in order.

        Returns:
            [(channel, chat_id), ...] sorted by recency (most recent first)
        """
        import json
        from datetime import datetime

        seen: set[tuple[str, str]] = set()
        targets: list[tuple[str, str]] = []

        if not self.gateway:
            return targets

        # 1. First look up sessions in memory
        session_manager = getattr(self.gateway, "session_manager", None)
        if not session_manager:
            return targets
        sessions = session_manager.list_sessions()
        if sessions:
            sessions.sort(key=lambda s: getattr(s, "last_active", datetime.min), reverse=True)
            for session in sessions:
                if getattr(session, "state", None) and str(session.state.value) == "closed":
                    continue
                pair = (session.channel, session.chat_id)
                if pair not in seen:
                    seen.add(pair)
                    targets.append(pair)

        # 2. Fill in from the sessions.json file
        sessions_file = session_manager.storage_path / "sessions.json"
        if sessions_file.exists():
            try:
                with open(sessions_file, encoding="utf-8") as f:
                    raw_sessions = json.load(f)

                raw_sessions.sort(key=lambda s: s.get("last_active", ""), reverse=True)

                for s in raw_sessions:
                    channel = s.get("channel")
                    chat_id = s.get("chat_id")
                    state = s.get("state", "")
                    if not channel or not chat_id or state == "closed":
                        continue
                    pair = (channel, chat_id)
                    if pair not in seen:
                        seen.add(pair)
                        targets.append(pair)
            except Exception as e:
                logger.error(f"Failed to read sessions file for IM targets: {e}")

        if targets:
            logger.info(f"Found {len(targets)} IM target(s) for report push")

        return targets

    async def _send_report_chunks(
        self,
        adapter: Any,
        chat_id: str,
        report_md: str,
        report_date: str,
    ) -> None:
        """Send the self-check report in chunks (compatible with Telegram's 4096-character limit)"""
        header = f"📋 Daily system self-check report ({report_date})\n\n"
        full_text = header + report_md

        max_len = 3500
        text = full_text
        while text:
            if len(text) <= max_len:
                await adapter.send_text(chat_id, text)
                break
            cut = text.rfind("\n", 0, max_len)
            if cut < 1000:
                cut = max_len
            await adapter.send_text(chat_id, text[:cut].rstrip())
            text = text[cut:].lstrip()

    def _build_prompt(self, task: ScheduledTask, suppress_send_to_chat: bool = False) -> str:
        """
        Build the execution prompt.

        Args:
            task: the task
        suppress_send_to_chat: whether to forbid the legacy "send message via tool" pattern
            (kept for backward compatibility; text is sent automatically by the gateway)
        """
        # Base prompt
        prompt = task.prompt

        # Add context
        context_parts = [
            "[Scheduled task execution]",
            f"Task name: {task.name}",
            f"Task description: {task.description}",
            "",
            "Please execute the following task:",
            prompt,
        ]

        # If the task has an IM channel
        if task.channel_id and task.chat_id:
            context_parts.append("")
            if suppress_send_to_chat:
                # Do not send messages; the system handles this centrally
                context_parts.append(
                    "Note: do not attempt to send text messages via tools; the system will send "
                    "the result notification automatically. Just return the execution result."
                )
            else:
                context_parts.append(
                    "Hint: text will be sent automatically by the system; to deliver attachments, use deliver_artifacts."
                )

        # If there is a script path, add a hint
        if task.script_path:
            context_parts.append("")
            context_parts.append(f"Related script: {task.script_path}")
            context_parts.append("Please read and execute this script first")

        # Skill bindings: inject the specified skill content into the prompt
        if task.skill_ids:
            skill_content = self._load_bound_skills(task.skill_ids)
            if skill_content:
                context_parts.append("")
                context_parts.append("## Bound skills")
                context_parts.append(skill_content)

        return "\n".join(context_parts)

    def _load_bound_skills(self, skill_ids: list[str]) -> str:
        """Load bound skill content (for injecting into the prompt of a Cron task)"""
        try:
            from ..config import settings
            from ..skills.loader import SkillLoader

            loader = SkillLoader()
            loader.load_all(settings.project_root)
            parts = []
            for sid in skill_ids:
                entry = loader.get_skill(sid)
                if entry and entry.body:
                    parts.append(f"### {entry.metadata.name or sid}\n{entry.body}")
                else:
                    logger.debug(f"Bound skill '{sid}' not found or empty")
            return "\n\n".join(parts)
        except Exception as e:
            logger.warning(f"Failed to load bound skills {skill_ids}: {e}")
            return ""

    async def _send_notification(
        self,
        task: ScheduledTask,
        success: bool,
        message: str,
    ) -> None:
        """
        Send the result notification (kept for backward compatibility).

        Prefer _send_end_notification now.
        """
        await self._send_end_notification(task, success, message)


# Convenience function: create the default executor
def create_default_executor(
    gateway: Any | None = None,
    timeout_seconds: int = 1200,  # 20-minute timeout
) -> Callable[[ScheduledTask], Awaitable[tuple[bool, str]]]:
    """
    Create the default executor function.

    Args:
        gateway: message gateway
        timeout_seconds: timeout in seconds, default 600 (10 minutes)

    Returns:
        an executor function usable by TaskScheduler
    """
    executor = TaskExecutor(gateway=gateway, timeout_seconds=timeout_seconds)
    return executor.execute
