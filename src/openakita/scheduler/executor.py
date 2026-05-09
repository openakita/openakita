"""
任务执行器

负责实际执行定时任务:
- 创建 Agent session
- 发送 prompt 给 Agent
- 收集执行结果
- 发送结果通知
"""

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ..memory.json_utils import coerce_text
from .task import ScheduledTask

logger = logging.getLogger(__name__)


class TaskExecutor:
    """
    任务执行器

    将定时任务转换为 Agent 调用
    """

    def __init__(
        self,
        agent_factory: Callable[[], Any] | None = None,
        gateway: Any | None = None,
        timeout_seconds: int = 1200,  # 20 分钟超时
    ):
        """
        Args:
            agent_factory: Agent 工厂函数
            gateway: 消息网关（用于发送结果通知）
            timeout_seconds: 执行超时（秒），默认 1200 秒（20分钟）
        """
        self.agent_factory = agent_factory
        self.gateway = gateway
        self.timeout_seconds = timeout_seconds
        # 可选：由 Agent 设置，用于活人感心跳等系统任务
        self.persona_manager = None
        self.memory_manager = None
        self.proactive_engine = None  # 复用 agent 上的实例，保留 _last_user_interaction 状态

    @staticmethod
    def _metadata_bool(task: ScheduledTask, key: str, default: bool) -> bool:
        """Read bool-like scheduler metadata values from persisted JSON safely."""

        value = (task.metadata or {}).get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    def _escape_telegram_chars(self, text: str) -> str:
        """
        转义 Telegram MarkdownV2 全部特殊字符

        官方文档规定必须转义的 18 个字符:
        _ * [ ] ( ) ~ ` > # + - = | { } . !

        策略: 全部转义，确保消息能正常发送
        """
        # MarkdownV2 必须转义的全部字符
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
        执行任务

        根据任务类型采用不同的执行策略:
        - REMINDER: 简单提醒，直接发送消息
        - TASK: 复杂任务，先通知开始 → LLM 执行 → 通知结束

        Args:
            task: 要执行的任务

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

        # 根据任务类型选择执行策略
        if task.is_reminder:
            return await self._execute_reminder(task)
        else:
            return await self._execute_complex_task(task)

    async def _execute_reminder(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        执行简单提醒任务

        流程:
        1. 先发送提醒消息（只发送一次！）— silent 模式跳过
        2. 让 LLM 判断是否需要执行额外操作（防止误判）

        注意：简单提醒只发送一条消息，不发送"任务完成"通知
        """
        logger.info(f"TaskExecutor: executing reminder {task.id}")

        try:
            message = task.reminder_message or task.prompt or f"⏰ 提醒: {task.name}"

            if task.silent:
                logger.info(f"TaskExecutor: reminder {task.id} in silent mode, skipping delivery")
                return True, f"[SILENT] {message}"

            message_sent = False

            if task.channel_id and task.chat_id and self.gateway:
                message_sent = await self._deliver_reminder_message(task, message)
            elif self.gateway:
                # 有网关但任务未配置通道，尝试所有已知通道
                message_sent = await self._deliver_via_fallback_channels(task, message)
            # else: 无网关，无法发送

            if not message_sent:
                # 最后的兜底：尝试桌面通知
                desktop_sent = await self._try_desktop_notify_fallback(task, message)
                if not desktop_sent:
                    return (
                        False,
                        f"提醒投递失败: 所有通道均不可用，提醒内容「{message[:50]}」未能送达",
                    )

                return True, f"提醒已通过桌面通知送达（IM 通道不可用）: {message[:80]}"

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
        向任务配置的主通道投递提醒消息。

        Returns:
            True 如果消息很可能已送达（含 msg_id=None 但通道活跃的情况）
        """
        channel_id = task.channel_id
        chat_id = task.chat_id

        # 检查主通道适配器是否存在且运行中
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
        """尝试通过所有已知的备用 IM 通道投递提醒"""
        targets = self._find_all_im_targets()
        primary = (task.channel_id, task.chat_id)

        for channel, chat_id in targets:
            if (channel, chat_id) == primary:
                continue  # 主通道已失败，跳过

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
        """当所有 IM 通道失败时，尝试桌面通知作为最后兜底"""
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
        让 LLM 判断提醒任务是否需要执行额外操作

        防止设定任务时误判，把复杂任务变成了简单提醒

        注意：这个方法只用于判断，不应该发送任何消息
        """
        try:
            # 清除 IM 上下文，防止判断时发送消息
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
                # 使用 Brain 直接判断，不创建完整 Agent（更轻量、不会发消息）
                from ..core.brain import Brain

                brain = Brain()

                check_prompt = f"""请判断以下定时提醒是否需要执行额外的操作：

任务名称: {task.name}
任务描述: {task.description}
提醒内容: {task.reminder_message or task.prompt}

判断标准：
- 简单提醒：只需要提醒用户（如：喝水、休息、站立、开会提醒）→ NO_ACTION
- 复杂任务：需要 AI 执行具体操作（如：查询天气并告知、执行脚本、分析数据）→ NEEDS_ACTION

只回复 NO_ACTION 或 NEEDS_ACTION，不要有其他内容。"""

                response = await brain.think(
                    check_prompt, enable_thinking=False, max_tokens=16
                )
                result = response.content.strip().upper()

                needs_action = "NEEDS_ACTION" in result
                logger.info(f"LLM decision for reminder {task.id}: {result}")

                return needs_action

            finally:
                # 恢复 IM 上下文
                reset_im_context(tokens)

        except Exception as e:
            logger.warning(f"Failed to check reminder execution: {e}, assuming no action needed")
            return False

    async def _execute_complex_task(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        执行复杂任务

        流程:
        1. 发送开始通知（silent 模式跳过）
        2. 执行任务核心逻辑
        """
        logger.info(f"TaskExecutor: executing complex task {task.id}")

        if not task.silent:
            await self._send_start_notification(task)

        return await self._execute_complex_task_core(task, skip_end_notification=task.silent)

    async def _execute_complex_task_core(
        self, task: ScheduledTask, skip_end_notification: bool = False
    ) -> tuple[bool, str]:
        """
        复杂任务的核心执行逻辑

        可被 _execute_complex_task 和 _execute_reminder（升级时）调用

        Args:
            task: 要执行的任务
            skip_end_notification: 是否跳过结束通知（用于从提醒升级的情况）
        """
        agent = None
        im_context_set = False
        try:
            # 系统任务只负责产出结果，通知仍走统一路径，避免完成结果只写历史不发 IM。
            if task.action and task.action.startswith("system:"):
                system_success, system_result = await self._execute_system_task(task)
                if not skip_end_notification:
                    delivered = await self._send_end_notification(
                        task,
                        success=system_success,
                        message=system_result,
                    )
                    if not delivered:
                        error_msg = "任务已完成，但结果通知发送失败，请检查 IM 通道连接状态。"
                        logger.warning(f"TaskExecutor: system task {task.id} result delivery failed")
                        return False, error_msg
                return system_success, system_result

            # 1. 创建 Agent
            agent = await self._create_agent(task.agent_profile_id)

            # 1.5. 防递归：禁止任务内再创建定时任务
            if task.no_schedule_tools:
                agent._cron_disabled_tools = {
                    "schedule_task", "update_scheduled_task",
                    "cancel_scheduled_task", "trigger_scheduled_task",
                }

            # 2. 如果任务有 IM 通道信息，注入 IM 上下文
            if task.channel_id and task.chat_id and self.gateway:
                im_context_set = await self._setup_im_context(agent, task)

            # 3. 构建执行 prompt（简化版，不让 Agent 自己发消息）
            prompt = self._build_prompt(task, suppress_send_to_chat=True)

            # 4. 执行（带超时，支持任务级 metadata.timeout_seconds 覆盖）
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
                agent_success, result = await asyncio.wait_for(
                    self._run_agent(agent, prompt), timeout=task_timeout
                )
            except TimeoutError:
                timeout_display = (
                    f"{task_timeout // 60} 分钟" if task_timeout >= 60 else f"{task_timeout} 秒"
                )
                error_msg = f"任务执行超时（超过 {timeout_display} 未完成）"
                logger.error(f"TaskExecutor: task {task.id} timed out after {task_timeout}s")
                if not skip_end_notification:
                    await self._send_end_notification(task, success=False, message=error_msg)
                return False, error_msg

            # 5. 发送结果通知（如果需要）
            if not agent_success:
                if not skip_end_notification:
                    await self._send_end_notification(task, success=False, message=result)
                logger.warning(f"TaskExecutor: task {task.id} failed via agent result: {result}")
                return False, result

            agent_sent = getattr(agent, "_task_message_sent", False)
            if not agent_sent and not skip_end_notification:
                delivered = await self._send_end_notification(task, success=True, message=result)
                if not delivered:
                    error_msg = "任务已完成，但结果通知发送失败，请检查 IM 通道连接状态。"
                    logger.warning(f"TaskExecutor: task {task.id} result delivery failed")
                    return False, error_msg

            logger.info(f"TaskExecutor: task {task.id} completed successfully")
            return True, result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"TaskExecutor: task {task.id} failed: {error_msg}", exc_info=True)
            if not skip_end_notification:
                await self._send_end_notification(task, success=False, message=error_msg)
            return False, error_msg
        finally:
            # 清理 IM 上下文
            if agent and im_context_set:
                self._cleanup_im_context(agent)
            # 清理 Agent（确保超时/异常路径也会执行）
            if agent:
                with contextlib.suppress(Exception):
                    await self._cleanup_agent(agent)

    async def _send_start_notification(self, task: ScheduledTask) -> None:
        """发送任务开始通知"""
        if not task.channel_id or not task.chat_id or not self.gateway:
            return

        # 检查是否启用开始通知
        if not self._metadata_bool(task, "notify_on_start", True):
            logger.debug(f"Task {task.id} has start notification disabled")
            return

        try:
            notification = f"🚀 开始执行任务: {task.name}\n\n请稍候，我正在处理中..."

            delivered = await self._send_gateway_text(
                channel=task.channel_id,
                chat_id=task.chat_id,
                text=notification,
                reliable=False,
            )
            if delivered:
                logger.info(f"Sent start notification for task {task.id}")
            else:
                logger.info(f"Start notification for task {task.id} was not immediately delivered")

        except Exception as e:
            logger.error(f"Failed to send start notification: {e}")

    async def _send_end_notification(
        self,
        task: ScheduledTask,
        success: bool,
        message: str,
    ) -> bool:
        """发送任务结束通知（IM 通道 + 桌面通知）"""
        notify_on_complete = self._metadata_bool(task, "notify_on_complete", True)
        if not notify_on_complete:
            logger.debug(f"Task {task.id} has completion notification disabled")
            return True

        # 桌面通知（独立于 IM 通道，但仍尊重 notify_on_complete）
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

        # IM 通道通知。没有配置目标通道时只做桌面通知不视为失败；但已经配置
        # channel/chat_id 却缺少 gateway 时，不能把“未发送”记录成成功。
        if not task.channel_id or not task.chat_id:
            logger.debug(f"Task {task.id} has no notification channel configured")
            return True

        if not self.gateway:
            logger.warning(
                f"TaskExecutor: task {task.id} has target channel "
                f"{task.channel_id}/{task.chat_id} but no gateway is attached"
            )
            return False

        status = "✅ 任务完成" if success else "❌ 任务失败"
        notification = f"""{status}: {task.name}

结果:
{message}
"""

        delivered = await self._deliver_task_notification(task, notification)
        if delivered:
            logger.info(f"Sent end notification for task {task.id}")
            return True

        logger.warning(
            f"TaskExecutor: end notification for task {task.id} was not delivered "
            f"({task.channel_id}/{task.chat_id})"
        )
        return False

    async def _deliver_task_notification(self, task: ScheduledTask, text: str) -> bool:
        """投递任务通知；主通道失败时尝试已知 IM 目标，保持单一通知入口。"""
        primary = (task.channel_id, task.chat_id)
        if task.channel_id and task.chat_id:
            if await self._send_gateway_text(
                channel=task.channel_id,
                chat_id=task.chat_id,
                text=text,
                user_id=task.user_id or "system",
            ):
                return True

        for channel, chat_id in self._find_all_im_targets():
            if (channel, chat_id) == primary:
                continue
            if await self._send_gateway_text(channel=channel, chat_id=chat_id, text=text):
                logger.info(
                    f"TaskExecutor: notification for {task.id} delivered via fallback "
                    f"{channel}/{chat_id}"
                )
                return True

        return False

    async def _send_gateway_text(
        self,
        *,
        channel: str,
        chat_id: str,
        text: str,
        user_id: str = "system",
        reliable: bool = True,
    ) -> bool:
        """Return True only for immediate delivery, not platform-side queued sends."""
        if not self.gateway:
            return False
        try:
            if reliable and hasattr(self.gateway, "send_text_reliably"):
                return bool(
                    await self.gateway.send_text_reliably(
                        channel=channel,
                        chat_id=chat_id,
                        text=text,
                        user_id=user_id,
                    )
                )

            result = await self.gateway.send(channel=channel, chat_id=chat_id, text=text)
            if isinstance(result, str):
                return bool(result)
            return result is not None
        except Exception as e:
            logger.warning(f"TaskExecutor: send failed for {channel}/{chat_id}: {e}")
            return False

    async def _setup_im_context(self, agent: Any, task: ScheduledTask) -> bool:
        """
        为定时任务注入 IM 上下文，让 Agent 可以使用 IM 工具（如 deliver_artifacts / get_chat_history）。
        返回 True 表示设置成功（调用方应在 finally 中 _cleanup_im_context）。
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
            # 保存 token 到 agent 上以便对称 reset
            agent._im_context_tokens = tokens

            logger.info(f"Set up IM context for task {task.id}: {task.channel_id}/{task.chat_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to set up IM context: {e}", exc_info=True)
            return False

    def _cleanup_im_context(self, agent: Any) -> None:
        """对称清理 IM 上下文（使用 reset_im_context 恢复到原始状态）"""
        try:
            tokens = getattr(agent, "_im_context_tokens", None)
            if tokens:
                from ..core.im_context import reset_im_context

                reset_im_context(tokens)
                agent._im_context_tokens = None
        except Exception as e:
            logger.warning(f"Failed to cleanup IM context: {e}")

    async def _create_agent(self, agent_profile_id: str = "default") -> Any:
        """创建 Agent 实例（不启动 scheduler，避免重复执行任务）"""
        if self.agent_factory:
            try:
                params = inspect.signature(self.agent_factory).parameters
                if params:
                    return self.agent_factory(agent_profile_id)
            except (TypeError, ValueError):
                pass
            return self.agent_factory()

        profile_id = agent_profile_id or "default"
        if profile_id != "default":
            profile = self._resolve_agent_profile(profile_id)
            if profile is not None:
                from ..agents.factory import AgentFactory

                return await AgentFactory().create(profile)
            logger.warning("Unknown scheduled task agent_profile_id=%r, using default", profile_id)

        from ..core.agent import Agent

        agent = Agent()
        await agent.initialize(start_scheduler=False)
        return agent

    def _resolve_agent_profile(self, profile_id: str) -> Any | None:
        """Resolve an AgentProfile for scheduled task execution."""
        from ..agents.presets import SYSTEM_PRESETS
        from ..agents.profile import get_profile_store

        for preset in SYSTEM_PRESETS:
            if preset.id == profile_id:
                return preset
        try:
            return get_profile_store().get(profile_id)
        except Exception:
            logger.debug("Failed to load agent profile %r", profile_id, exc_info=True)
            return None

    async def _run_agent(self, agent: Any, prompt: str) -> tuple[bool, str]:
        """
        运行 Agent（使用 Ralph 模式）

        优先使用 execute_task_from_message（Ralph 循环模式），
        这样可以支持多轮工具调用，直到任务完成。
        """
        # 优先使用 Ralph 模式（execute_task_from_message）
        if hasattr(agent, "execute_task_from_message"):
            # Scheduler owns start/end delivery. Prevent Agent's generic
            # desktop completion toast from producing a second notification.
            with contextlib.suppress(Exception):
                agent._suppress_desktop_task_notification = True
            result = await agent.execute_task_from_message(prompt)
            if isinstance(result, str):
                return True, result
            if result.success:
                return True, str(result.data or "")
            return False, result.error or "Unknown error"
        # 降级到普通 chat
        elif hasattr(agent, "chat"):
            return True, await agent.chat(prompt)
        else:
            raise ValueError("Agent does not have execute_task_from_message or chat method")

    async def _cleanup_agent(self, agent: Any) -> None:
        """清理 Agent"""
        if hasattr(agent, "shutdown"):
            await agent.shutdown()

    async def _execute_system_task(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        执行系统内置任务（带超时保护）

        直接调用相应的系统方法，不通过 LLM

        支持的系统任务:
        - system:daily_memory - 每日记忆整理
        - system:daily_selfcheck - 每日系统自检
        - system:proactive_heartbeat - 活人感心跳
        - system:workspace_backup - 定时工作区备份
        - system:memory_nudge_review - 周期性记忆回顾
        """
        action = task.action
        logger.info(f"Executing system task: {action}")

        from ..config import settings
        from ..core.token_tracking import (
            TokenBudgetState,
            reset_token_budget,
            set_token_budget,
            token_budget_status,
        )

        # 系统任务也需要超时保护，避免 selfcheck 等任务无限运行
        SYSTEM_TASK_TIMEOUTS = {
            "system:daily_selfcheck": max(settings.scheduler_task_timeout, 1200),
            "system:daily_memory": 1800,  # 30 分钟（含 LLM review 大量记忆）
            "system:workspace_backup": 300,  # 5 分钟
            "system:memory_nudge_review": 120,  # 2 分钟（轻量 LLM 审视）
        }
        timeout = SYSTEM_TASK_TIMEOUTS.get(action)
        budget_tokens = (
            settings.scheduler_background_token_budget
            if action
            in {"system:daily_selfcheck", "system:daily_memory", "system:memory_nudge_review"}
            else 0
        )
        budget_token = set_token_budget(
            TokenBudgetState(name=action or "system_task", max_tokens=budget_tokens)
            if budget_tokens > 0
            else None
        )

        try:
            if action == "system:daily_memory":
                coro = self._system_daily_memory()
            elif action == "system:daily_selfcheck":
                soft_timeout = max(timeout - 30, 1) if timeout else None
                coro = self._system_daily_selfcheck(soft_timeout)
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
                except TimeoutError:
                    if action == "system:daily_memory":
                        result_msg = (
                            "记忆整理超过系统保护时长，已停止本轮整理。"
                            "如果之前已有进度，下次会从已保存的位置继续。"
                        )
                        logger.warning(f"TaskExecutor: {result_msg}")
                        return True, result_msg
                    if action == "system:daily_selfcheck":
                        result_msg = (
                            "系统自检超过后台保护时长，已停止本轮检查。"
                            "已保存的部分报告会保留，后续问题下次继续处理。"
                        )
                        logger.warning(f"TaskExecutor: {result_msg}")
                        return True, result_msg
                    error_msg = f"System task {action} timed out after {timeout}s"
                    logger.error(f"TaskExecutor: {error_msg}")
                    return False, error_msg
            else:
                return await coro

        except Exception as e:
            logger.error(f"System task {action} failed: {e}")
            return False, str(e)
        finally:
            status = token_budget_status()
            if status.get("enabled"):
                logger.info(
                    "System task token budget: action=%s used=%s max=%s exceeded=%s",
                    action,
                    status.get("used_tokens"),
                    status.get("max_tokens"),
                    status.get("exceeded"),
                )
            reset_token_budget(budget_token)

    async def _system_daily_memory(self) -> tuple[bool, str]:
        """
        执行记忆整理

        优先复用 agent 上的 MemoryManager（参数完整），
        仅在实例不存在时 fallback 新建。

        使用 ConsolidationTracker 记录整理时间点，
        确保处理的是"上次整理到当前时间"的记录。
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

            result = await mm.consolidate_daily(
                checkpoint=tracker.get_memory_consolidation_checkpoint(),
                checkpoint_callback=tracker.record_memory_consolidation_checkpoint,
                time_budget_seconds=1500,
            )

            if result.get("partial"):
                llm_review = result.get("llm_review") or {}
                processed_batches = llm_review.get("processed_batches", 0)
                total_batches = llm_review.get("total_batches", 0)
                summary = (
                    "记忆整理已安全暂停，进度已保存，下次会继续:\n"
                    f"- 已提取: {result.get('unextracted_processed', 0)}\n"
                    f"- 已去重: {result.get('duplicates_removed', 0)}\n"
                    f"- 记忆审查批次: {processed_batches}/{total_batches}\n"
                    f"- 说明: {result.get('reason', '本轮时间预算已用完')}\n"
                    f"- 时间范围: {since.strftime('%m-%d %H:%M') if since else '全部'} → {until.strftime('%m-%d %H:%M')}"
                )
                logger.info(f"Memory consolidation paused with checkpoint: {result}")
                return True, summary

            tracker.record_memory_consolidation(result)

            v2_keys = ["unextracted_processed", "duplicates_removed", "memories_decayed"]
            _v1_keys = ["sessions_processed", "memories_extracted", "memories_added"]

            if any(result.get(k) for k in v2_keys):
                summary = (
                    f"记忆整理完成 (v2):\n"
                    f"- 提取: {result.get('unextracted_processed', 0)}\n"
                    f"- 去重: {result.get('duplicates_removed', 0)}\n"
                    f"- 衰减: {result.get('memories_decayed', 0)}\n"
                    f"- 时间范围: {since.strftime('%m-%d %H:%M') if since else '全部'} → {until.strftime('%m-%d %H:%M')}"
                )
            else:
                summary = (
                    f"记忆整理完成:\n"
                    f"- 处理会话: {result.get('sessions_processed', 0)}\n"
                    f"- 提取记忆: {result.get('memories_extracted', 0)}\n"
                    f"- 新增记忆: {result.get('memories_added', 0)}\n"
                    f"- 去重: {result.get('duplicates_removed', 0)}\n"
                    f"- MEMORY.md: {'已刷新' if result.get('memory_md_refreshed') else '未刷新'}\n"
                    f"- 时间范围: {since.strftime('%m-%d %H:%M') if since else '全部'} → {until.strftime('%m-%d %H:%M')}"
                )

            logger.info(f"Memory consolidation completed: {result}")
            return True, summary

        except Exception as e:
            logger.error(f"Memory consolidation failed: {e}")
            return False, str(e)

    async def _system_memory_nudge_review(self) -> tuple[bool, str]:
        """
        周期性记忆回顾（Memory Nudge）

        用 LLM 审视最近对话轮次，提取可能遗漏的长期记忆。
        与 daily_memory 互补：daily 是端到端全量整理，
        nudge 是轻量实时补漏，确保重要信息不因上下文压缩而丢失。
        """
        try:
            from ..config import settings
            from ..core.token_tracking import (
                TokenTrackingContext,
                reset_tracking_context,
                set_tracking_context,
            )

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
                f"[{t.get('role', 'unknown')}]: {coerce_text(t.get('content'))[:500]}"
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

            _tracking_token = set_tracking_context(
                TokenTrackingContext(
                    session_id="system_memory_nudge",
                    request_id="system_memory_nudge",
                    turn_id=f"system_memory_nudge:{int(time.time() * 1000)}",
                    operation_type="background",
                    operation_detail="system_memory_nudge",
                    channel="scheduler",
                    user_id="system",
                    agent_profile_id="system",
                )
            )
            try:
                response = await brain.think_lightweight(review_prompt, max_tokens=2048)
            finally:
                reset_tracking_context(_tracking_token)
            raw = response.content.strip()

            import json
            import re as _re

            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            # Fix-7：best-effort JSON 解析。
            # 旧实现 json.loads(raw) 一遇到 LLM 返回的非法 JSON（哪怕只是
            # 多了一行 prose/单条尾随逗号）就抛 JSONDecodeError，导致整个
            # nudge 任务失败 → fail_count + missed_count 持续累积。
            #
            # 新策略（仍然保守）：
            #   1. 直接 loads 成功 → 用结果；
            #   2. 失败 → 抓出第一个 [ ... ] JSON 数组重新尝试；
            #   3. 仍失败 → 不视为任务失败，记 warning 并返回成功 +
            #      "skipped" 信息，让 scheduler 不再累积失败计数。
            memories: list | None = None
            try:
                memories = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.warning(
                    "[memory_nudge] LLM returned non-JSON, attempting array "
                    "extraction (err=%s, raw_preview=%r)",
                    str(e)[:120],
                    raw[:200],
                )
                m = _re.search(r"\[\s*(?:\{.*?\}\s*,?\s*)*\]", raw, _re.DOTALL)
                if m:
                    try:
                        memories = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        memories = None
                if memories is None:
                    return (
                        True,
                        "LLM returned malformed JSON; skipping this round "
                        "(no failure count, will retry next interval).",
                    )
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
        执行活人感心跳

        每 30 分钟触发一次，大多数时候只是检查然后跳过。
        只有满足所有条件时才真正生成并发送消息。

        优先复用 agent 上的 ProactiveEngine 实例（保留 _last_user_interaction 状态），
        仅在实例不存在时 fallback 新建（此时 idle_chat 不可用）。
        """
        try:
            from ..config import settings
            from ..core.token_tracking import (
                TokenTrackingContext,
                reset_tracking_context,
                set_tracking_context,
            )

            engine = self.proactive_engine
            if not engine:
                # 无 engine 实例时，先检查 settings 决定是否值得新建
                if not settings.proactive_enabled:
                    return True, "Proactive mode disabled, skipping heartbeat"

                # fallback: 新建实例（idle_chat 不可用）
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

            # 执行心跳
            _tracking_token = set_tracking_context(
                TokenTrackingContext(
                    session_id=task.id or "system_proactive_heartbeat",
                    request_id=task.id or "system_proactive_heartbeat",
                    turn_id=f"{task.id or 'system_proactive_heartbeat'}:{int(time.time() * 1000)}",
                    operation_type="background",
                    operation_detail="system_proactive_heartbeat",
                    channel="scheduler",
                    user_id="system",
                    agent_profile_id="system",
                )
            )
            try:
                result = await engine.heartbeat()
            finally:
                reset_tracking_context(_tracking_token)

            if not result:
                return True, "Heartbeat check passed, no message needed"

            # 发送消息
            msg_content = result.get("content", "")
            msg_type = result.get("type", "unknown")

            if msg_content and self.gateway:
                # 查找活跃的 IM 通道
                targets = self._find_all_im_targets()
                for channel, chat_id in targets:
                    try:
                        await self.gateway.send(
                            channel=channel,
                            chat_id=chat_id,
                            text=msg_content,
                        )

                        # 如果需要发送表情包
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

    async def _system_daily_selfcheck(
        self,
        max_runtime_seconds: int | None = None,
    ) -> tuple[bool, str]:
        """
        执行系统自检

        使用 ConsolidationTracker 记录自检时间点，
        确保分析的是"上次自检到当前时间"的日志。
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

            # 1. 清理旧日志
            log_cleaner = LogCleaner(
                log_dir=settings.log_dir_path,
                retention_days=settings.log_retention_days,
            )
            cleanup_result = log_cleaner.cleanup()

            # 2. 执行自检（传入时间范围，复用 agent 的 memory_manager 避免 DB 锁冲突）
            brain = Brain()
            checker = SelfChecker(brain=brain, memory_manager=self.memory_manager)
            report = await checker.run_daily_check(
                since=since,
                max_runtime_seconds=max_runtime_seconds,
            )

            # 2.1 生成 Markdown 报告文本（用于 IM 推送）
            report_md = None
            try:
                report_md = report.to_markdown() if hasattr(report, "to_markdown") else str(report)
            except Exception as e:
                logger.warning(f"Failed to render report markdown: {e}")
                report_md = None

            # 2.2 推送报告到最后活跃的 IM 通道（不限制时间，逐个尝试）
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
                        break  # 发送成功，停止尝试
                    except Exception as e:
                        logger.warning(
                            f"Failed to push selfcheck report via {channel}/{chat_id}: {e}"
                        )
                        continue  # 尝试下一个通道

                if pushed > 0:
                    with contextlib.suppress(Exception):
                        checker.mark_report_as_reported(getattr(report, "date", None))

            # 3. 记录自检时间
            tracker.record_selfcheck(
                {
                    "total_errors": report.total_errors,
                    "fix_success": report.fix_success,
                }
            )

            # 4. 格式化结果
            push_info = push_target if pushed else "无可用通道（将在用户下次发消息时补推）"
            time_range_info = (
                f"{since.strftime('%m-%d %H:%M')} → {until.strftime('%m-%d %H:%M')}"
                if since
                else "首次运行"
            )

            summary = (
                f"系统自检完成:\n"
                f"- 总错误数: {report.total_errors}\n"
                f"- 核心组件错误: {report.core_errors} (需人工处理)\n"
                f"- 工具错误: {report.tool_errors}\n"
                f"- 尝试修复: {report.fix_attempted}\n"
                f"- 修复成功: {report.fix_success}\n"
                f"- 修复失败: {report.fix_failed}\n"
                f"- 日志清理: 删除 {cleanup_result.get('by_age', 0) + cleanup_result.get('by_size', 0)} 个旧文件\n"
                f"- 分析范围: {time_range_info}\n"
                f"- 报告推送: {push_info}"
            )
            if getattr(report, "partial", False):
                summary += f"\n- 状态: 部分完成（{getattr(report, 'status_note', '')}）"

            logger.info(
                f"Selfcheck completed: {report.total_errors} errors, {report.fix_success} fixed"
            )
            return True, summary

        except Exception as e:
            logger.error(f"Daily selfcheck failed: {e}")
            return False, str(e)

    async def _system_workspace_backup(self) -> tuple[bool, str]:
        """执行定时工作区备份。"""
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
            summary = f"工作区备份完成: {zip_path.name} ({size_mb:.1f} MB)"
            logger.info(summary)
            return True, summary

        except Exception as e:
            logger.error(f"Workspace backup failed: {e}")
            return False, str(e)

    def _find_all_im_targets(self) -> list[tuple[str, str]]:
        """
        找到所有可用的 IM 通道（按活跃度降序，去重）

        优先从内存中的会话查找；然后从 sessions.json 持久化文件补充。
        返回去重后的 (channel, chat_id) 列表，供调用方逐个尝试。

        Returns:
            [(channel, chat_id), ...] 按活跃度降序
        """
        import json
        from datetime import datetime

        seen: set[tuple[str, str]] = set()
        targets: list[tuple[str, str]] = []

        if not self.gateway:
            return targets

        # 1. 先从内存中的会话找
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

        # 2. 从 sessions.json 文件补充
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
        """分段发送自检报告（兼容 Telegram 4096 字符限制）"""
        header = f"📋 每日系统自检报告（{report_date}）\n\n"
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
        构建执行 prompt

        Args:
            task: 任务
        suppress_send_to_chat: 兼容旧参数；当前仅用于提示系统会兜底转发文本。
        """
        # 基础 prompt
        prompt = task.prompt

        # 添加上下文信息
        context_parts = [
            "[定时任务执行]",
            f"任务名称: {task.name}",
            f"任务描述: {task.description}",
            "",
            "请执行以下任务:",
            prompt,
        ]

        # 如果任务有 IM 通道
        if task.channel_id and task.chat_id:
            context_parts.append("")
            if suppress_send_to_chat:
                context_parts.append(
                    "请优先把用户需要看到的最终结果完整写在回复正文中，系统会尝试转发；"
                    "如果任务明确需要主动告知、交付附件或结合 IM 上下文互动，可以使用可用的 IM/交付工具。"
                )
            else:
                context_parts.append(
                    "提示: 文本将由系统自动发送；如需交付附件，请使用 deliver_artifacts。"
                )

        # 如果有脚本路径，添加提示
        if task.script_path:
            context_parts.append("")
            context_parts.append(f"相关脚本: {task.script_path}")
            context_parts.append("请先读取并执行该脚本")

        # Skill 绑定：将指定技能内容注入 prompt
        if task.skill_ids:
            skill_content = self._load_bound_skills(task.skill_ids)
            if skill_content:
                context_parts.append("")
                context_parts.append("## 绑定技能")
                context_parts.append(skill_content)

        return "\n".join(context_parts)

    def _load_bound_skills(self, skill_ids: list[str]) -> str:
        """加载绑定的技能内容（用于注入到 Cron 任务的 prompt）"""
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
        发送结果通知（兼容旧代码）

        现在主要使用 _send_end_notification
        """
        await self._send_end_notification(task, success, message)


# 便捷函数：创建默认执行器
def create_default_executor(
    gateway: Any | None = None,
    timeout_seconds: int = 1200,  # 20 分钟超时
) -> Callable[[ScheduledTask], Awaitable[tuple[bool, str]]]:
    """
    创建默认执行器函数

    Args:
        gateway: 消息网关
        timeout_seconds: 超时时间（秒），默认 600 秒（10分钟）

    Returns:
        可用于 TaskScheduler 的执行器函数
    """
    executor = TaskExecutor(gateway=gateway, timeout_seconds=timeout_seconds)
    return executor.execute
