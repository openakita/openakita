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
import logging
from collections.abc import Awaitable, Callable
from typing import Any

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
        timeout_seconds: int = 600,  # 10 分钟超时
    ):
        """
        Args:
            agent_factory: Agent 工厂函数
            gateway: 消息网关（用于发送结果通知）
            timeout_seconds: 执行超时（秒），默认 600 秒（10分钟）
        """
        self.agent_factory = agent_factory
        self.gateway = gateway
        self.timeout_seconds = timeout_seconds
        # 可选：由 Agent 设置，用于活人感心跳等系统任务
        self.persona_manager = None
        self.memory_manager = None
        self.proactive_engine = None  # 复用 agent 上的实例，保留 _last_user_interaction 状态

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

        # 根据任务类型选择执行策略
        if task.is_reminder:
            return await self._execute_reminder(task)
        else:
            return await self._execute_complex_task(task)

    async def _execute_reminder(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        执行简单提醒任务

        流程:
        1. 先发送提醒消息（只发送一次！）
        2. 让 LLM 判断是否需要执行额外操作（防止误判）

        注意：简单提醒只发送一条消息，不发送"任务完成"通知
        """
        logger.info(f"TaskExecutor: executing reminder {task.id}")

        try:
            # 1. 发送提醒消息（这是唯一的消息）
            message = task.reminder_message or task.prompt or f"⏰ 提醒: {task.name}"
            message_sent = False

            if task.channel_id and task.chat_id and self.gateway:
                msg_id = await self.gateway.send(
                    channel=task.channel_id,
                    chat_id=task.chat_id,
                    text=message,
                )
                if msg_id is None:
                    raise RuntimeError(
                        f"Reminder send failed (no message_id) for {task.channel_id}/{task.chat_id}"
                    )
                message_sent = True
                logger.info(f"TaskExecutor: reminder {task.id} message sent (message_id={msg_id})")

            # 2. 让 LLM 判断是否需要执行额外操作
            # 这是为了防止设定任务时误判，把复杂任务变成了提醒
            should_execute = await self._check_if_needs_execution(task)

            if should_execute:
                logger.info(
                    f"TaskExecutor: reminder {task.id} needs additional execution, upgrading to task"
                )
                # 转为复杂任务执行（注意：不要再发开始通知，因为提醒消息已发）
                return await self._execute_complex_task_core(
                    task, skip_end_notification=message_sent
                )

            # 简单提醒完成，不发送"任务完成"通知
            logger.info(f"TaskExecutor: reminder {task.id} completed (no additional action needed)")
            return True, message

        except Exception as e:
            error_msg = str(e)
            logger.error(f"TaskExecutor: reminder {task.id} failed: {error_msg}")
            return False, error_msg

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

                response = await brain.think(check_prompt)
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
        1. 发送开始通知
        2. 执行任务核心逻辑
        """
        logger.info(f"TaskExecutor: executing complex task {task.id}")

        # 发送开始通知
        await self._send_start_notification(task)

        # 执行核心逻辑
        return await self._execute_complex_task_core(task)

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
        # 检查是否是系统任务（需要特殊处理）
        if task.action and task.action.startswith("system:"):
            return await self._execute_system_task(task)

        agent = None
        im_context_set = False
        try:
            # 1. 创建 Agent
            agent = await self._create_agent()

            # 2. 如果任务有 IM 通道信息，注入 IM 上下文
            if task.channel_id and task.chat_id and self.gateway:
                im_context_set = await self._setup_im_context(agent, task)

            # 3. 构建执行 prompt（简化版，不让 Agent 自己发消息）
            prompt = self._build_prompt(task, suppress_send_to_chat=True)

            # 4. 执行（带超时）
            try:
                result = await asyncio.wait_for(
                    self._run_agent(agent, prompt), timeout=self.timeout_seconds
                )
            except TimeoutError:
                error_msg = f"Task execution timed out after {self.timeout_seconds}s"
                logger.error(f"TaskExecutor: {error_msg}")
                if not skip_end_notification:
                    await self._send_end_notification(task, success=False, message=error_msg)
                return False, error_msg

            # 5. 发送结果通知（如果需要）
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
        if not task.metadata.get("notify_on_start", True):
            logger.debug(f"Task {task.id} has start notification disabled")
            return

        try:
            notification = f"🚀 开始执行任务: {task.name}\n\n请稍候，我正在处理中..."

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
        """发送任务结束通知（IM 通道 + 桌面通知）"""
        # 桌面通知（独立于 IM 通道，始终尝试）
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

        # IM 通道通知
        if not task.channel_id or not task.chat_id or not self.gateway:
            logger.debug(f"Task {task.id} has no notification channel configured")
            return

        if not task.metadata.get("notify_on_complete", True):
            logger.debug(f"Task {task.id} has completion notification disabled")
            return

        try:
            status = "✅ 任务完成" if success else "❌ 任务失败"
            notification = f"""{status}: {task.name}

结果:
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
        为定时任务注入 IM 上下文，让 Agent 可以使用 IM 工具（如 deliver_artifacts / get_chat_history）
        """
        try:
            from ..core.im_context import set_im_context
            from ..sessions import Session

            # 创建虚拟 Session（用于 IM 工具上下文）
            virtual_session = Session.create(
                channel=task.channel_id,
                chat_id=task.chat_id,
                user_id=task.user_id or "scheduled_task",
            )

            # 注入到协程上下文（避免并发串台）
            set_im_context(session=virtual_session, gateway=self.gateway)

            logger.info(f"Set up IM context for task {task.id}: {task.channel_id}/{task.chat_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to set up IM context: {e}", exc_info=True)
            return False

    def _cleanup_im_context(self, agent: Any) -> None:
        """清理 IM 上下文"""
        try:
            from ..core.im_context import set_im_context

            set_im_context(session=None, gateway=None)
        except Exception:
            pass

    async def _create_agent(self) -> Any:
        """创建 Agent 实例（不启动 scheduler，避免重复执行任务）"""
        if self.agent_factory:
            return self.agent_factory()

        from ..core.agent import Agent

        agent = Agent()
        await agent.initialize(start_scheduler=False)
        return agent

    async def _run_agent(self, agent: Any, prompt: str) -> str:
        """
        运行 Agent（使用 Ralph 模式）

        优先使用 execute_task_from_message（Ralph 循环模式），
        这样可以支持多轮工具调用，直到任务完成。
        """
        # 优先使用 Ralph 模式（execute_task_from_message）
        if hasattr(agent, "execute_task_from_message"):
            result = await agent.execute_task_from_message(prompt)
            return result.data if result.success else result.error
        # 降级到普通 chat
        elif hasattr(agent, "chat"):
            return await agent.chat(prompt)
        else:
            raise ValueError("Agent does not have execute_task_from_message or chat method")

    async def _cleanup_agent(self, agent: Any) -> None:
        """清理 Agent"""
        if hasattr(agent, "shutdown"):
            await agent.shutdown()

    async def _execute_system_task(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        执行系统内置任务

        直接调用相应的系统方法，不通过 LLM

        支持的系统任务:
        - system:daily_memory - 每日记忆整理
        - system:daily_selfcheck - 每日系统自检
        - system:proactive_heartbeat - 活人感心跳
        - system:workspace_backup - 定时工作区备份
        """
        action = task.action
        logger.info(f"Executing system task: {action}")

        try:
            if action == "system:daily_memory":
                return await self._system_daily_memory()

            elif action == "system:daily_selfcheck":
                return await self._system_daily_selfcheck()

            elif action == "system:proactive_heartbeat":
                return await self._system_proactive_heartbeat(task)

            elif action == "system:workspace_backup":
                return await self._system_workspace_backup()

            else:
                return False, f"Unknown system action: {action}"

        except Exception as e:
            logger.error(f"System task {action} failed: {e}")
            return False, str(e)

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
                logger.info(f"Memory consolidation time range: {since.isoformat()} → {until.isoformat()}")
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
                logger.debug("ProactiveEngine fallback: created new instance (idle_chat unavailable)")

            # 执行心跳
            result = await engine.heartbeat()

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

                                sticker_engine = StickerEngine(settings.sticker_data_path)
                                await sticker_engine.initialize()
                                sticker = await sticker_engine.get_random_by_mood(sticker_mood)
                                if sticker:
                                    local_path = await sticker_engine.download_and_cache(sticker["url"])
                                    if local_path:
                                        adapter = self.gateway.get_adapter(channel)
                                        if adapter:
                                            await adapter.send_image(chat_id, str(local_path))
                            except Exception as e:
                                logger.debug(f"Failed to send sticker with proactive message: {e}")

                        logger.info(f"Sent proactive message ({msg_type}) to {channel}/{chat_id}")
                        return True, f"Sent {msg_type} message: {msg_content[:50]}..."
                    except Exception as e:
                        logger.warning(f"Failed to send proactive message to {channel}/{chat_id}: {e}")

            return True, f"Generated {msg_type} message but no active IM channel"

        except Exception as e:
            logger.error(f"Proactive heartbeat failed: {e}")
            return False, str(e)

    async def _system_daily_selfcheck(self) -> tuple[bool, str]:
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
            report = await checker.run_daily_check(since=since)

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
                        await self._send_report_chunks(
                            adapter, chat_id, report_md, report_date
                        )
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
            tracker.record_selfcheck({
                "total_errors": report.total_errors,
                "fix_success": report.fix_success,
            })

            # 4. 格式化结果
            push_info = push_target if pushed else "无可用通道（将在用户下次发消息时补推）"
            time_range_info = (
                f"{since.strftime('%m-%d %H:%M')} → {until.strftime('%m-%d %H:%M')}"
                if since else "首次运行"
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
            sessions.sort(
                key=lambda s: getattr(s, "last_active", datetime.min), reverse=True
            )
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
        suppress_send_to_chat: 是否禁止通过旧范式“工具发消息”（兼容旧参数；文本由网关自动发送）
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
                # 禁止发消息，由系统统一处理
                context_parts.append(
                    "注意: 不要尝试通过工具发送文本消息；系统会自动发送结果通知。请直接返回执行结果。"
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

        return "\n".join(context_parts)

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
    timeout_seconds: int = 600,  # 10 分钟超时
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
