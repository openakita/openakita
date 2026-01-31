"""
任务执行器

负责实际执行定时任务:
- 创建 Agent session
- 发送 prompt 给 Agent
- 收集执行结果
- 发送结果通知
"""

import asyncio
import logging
from typing import Optional, Callable, Awaitable, Any

from .task import ScheduledTask

logger = logging.getLogger(__name__)


class TaskExecutor:
    """
    任务执行器
    
    将定时任务转换为 Agent 调用
    """
    
    def __init__(
        self,
        agent_factory: Optional[Callable[[], Any]] = None,
        gateway: Optional[Any] = None,
        timeout_seconds: int = 300,
    ):
        """
        Args:
            agent_factory: Agent 工厂函数
            gateway: 消息网关（用于发送结果通知）
            timeout_seconds: 执行超时（秒）
        """
        self.agent_factory = agent_factory
        self.gateway = gateway
        self.timeout_seconds = timeout_seconds
    
    def _escape_telegram_chars(self, text: str) -> str:
        """
        转义 Telegram MarkdownV2 全部特殊字符
        
        官方文档规定必须转义的 18 个字符:
        _ * [ ] ( ) ~ ` > # + - = | { } . !
        
        策略: 全部转义，确保消息能正常发送
        """
        # MarkdownV2 必须转义的全部字符
        escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        
        for char in escape_chars:
            text = text.replace(char, '\\' + char)
        
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
        logger.info(f"TaskExecutor: executing task {task.id} ({task.name}) [type={task.task_type.value}]")
        
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
                # 转义 Telegram 特殊字符
                safe_message = self._escape_telegram_chars(message)
                
                await self.gateway.send(
                    channel=task.channel_id,
                    chat_id=task.chat_id,
                    text=safe_message,
                )
                message_sent = True
                logger.info(f"TaskExecutor: reminder {task.id} message sent")
            
            # 2. 让 LLM 判断是否需要执行额外操作
            # 这是为了防止设定任务时误判，把复杂任务变成了提醒
            should_execute = await self._check_if_needs_execution(task)
            
            if should_execute:
                logger.info(f"TaskExecutor: reminder {task.id} needs additional execution, upgrading to task")
                # 转为复杂任务执行（注意：不要再发开始通知，因为提醒消息已发）
                return await self._execute_complex_task_core(task, skip_end_notification=message_sent)
            
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
            from ..core.agent import Agent
            old_session = getattr(Agent, '_current_im_session', None)
            old_gateway = getattr(Agent, '_current_im_gateway', None)
            Agent._current_im_session = None
            Agent._current_im_gateway = None
            
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
                Agent._current_im_session = old_session
                Agent._current_im_gateway = old_gateway
            
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
        self, 
        task: ScheduledTask,
        skip_end_notification: bool = False
    ) -> tuple[bool, str]:
        """
        复杂任务的核心执行逻辑
        
        可被 _execute_complex_task 和 _execute_reminder（升级时）调用
        
        Args:
            task: 要执行的任务
            skip_end_notification: 是否跳过结束通知（用于从提醒升级的情况）
        """
        try:
            # 1. 创建 Agent
            agent = await self._create_agent()
            
            # 2. 如果任务有 IM 通道信息，注入 IM 上下文
            im_context_set = False
            if task.channel_id and task.chat_id and self.gateway:
                im_context_set = await self._setup_im_context(agent, task)
            
            # 3. 构建执行 prompt（简化版，不让 Agent 自己发消息）
            prompt = self._build_prompt(task, suppress_send_to_chat=True)
            
            # 4. 执行（带超时）
            try:
                result = await asyncio.wait_for(
                    self._run_agent(agent, prompt),
                    timeout=self.timeout_seconds
                )
            except asyncio.TimeoutError:
                error_msg = f"Task execution timed out after {self.timeout_seconds}s"
                logger.error(f"TaskExecutor: {error_msg}")
                if not skip_end_notification:
                    await self._send_end_notification(task, success=False, message=error_msg)
                return False, error_msg
            finally:
                # 清理 IM 上下文
                if im_context_set:
                    self._cleanup_im_context(agent)
            
            # 5. 发送结果通知（如果需要）
            # - 如果 Agent 已经通过 send_to_chat 发送，不重复发送
            # - 如果是从提醒升级的，也不重复发送
            agent_sent = getattr(agent, '_task_message_sent', False)
            if not agent_sent and not skip_end_notification:
                await self._send_end_notification(task, success=True, message=result)
            
            # 6. 清理 Agent
            await self._cleanup_agent(agent)
            
            logger.info(f"TaskExecutor: task {task.id} completed successfully")
            return True, result
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"TaskExecutor: task {task.id} failed: {error_msg}")
            if not skip_end_notification:
                await self._send_end_notification(task, success=False, message=error_msg)
            return False, error_msg
    
    async def _send_start_notification(self, task: ScheduledTask) -> None:
        """发送任务开始通知"""
        if not task.channel_id or not task.chat_id or not self.gateway:
            return
        
        try:
            notification = f"🚀 开始执行任务: {task.name}\n\n请稍候，我正在处理中..."
            safe_notification = self._escape_telegram_chars(notification)
            
            await self.gateway.send(
                channel=task.channel_id,
                chat_id=task.chat_id,
                text=safe_notification,
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
        """发送任务结束通知"""
        if not task.channel_id or not task.chat_id or not self.gateway:
            logger.debug(f"Task {task.id} has no notification channel configured")
            return
        
        try:
            status = "✅ 任务完成" if success else "❌ 任务失败"
            notification = f"""{status}: {task.name}

结果:
{message[:1000]}{"..." if len(message) > 1000 else ""}
"""
            
            # 转义 Telegram 特殊字符
            safe_notification = self._escape_telegram_chars(notification)
            
            await self.gateway.send(
                channel=task.channel_id,
                chat_id=task.chat_id,
                text=safe_notification,
            )
            
            logger.info(f"Sent end notification for task {task.id}")
            
        except Exception as e:
            logger.error(f"Failed to send end notification: {e}")
    
    async def _setup_im_context(self, agent: Any, task: ScheduledTask) -> bool:
        """
        为定时任务注入 IM 上下文，让 Agent 可以使用 send_to_chat
        """
        try:
            from ..core.agent import Agent
            from ..sessions import Session
            
            # 创建虚拟 Session（用于 send_to_chat）
            virtual_session = Session.create(
                channel=task.channel_id,
                chat_id=task.chat_id,
                user_id=task.user_id or "scheduled_task",
            )
            
            # 注入到 Agent 类变量
            Agent._current_im_session = virtual_session
            Agent._current_im_gateway = self.gateway
            
            logger.info(f"Set up IM context for task {task.id}: {task.channel_id}/{task.chat_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to set up IM context: {e}", exc_info=True)
            return False
    
    def _cleanup_im_context(self, agent: Any) -> None:
        """清理 IM 上下文"""
        try:
            from ..core.agent import Agent
            Agent._current_im_session = None
            Agent._current_im_gateway = None
        except Exception:
            pass
    
    async def _create_agent(self) -> Any:
        """
        创建 Agent 实例（不启动 scheduler，避免重复执行任务）
        
        如果启用了多 Agent 协同模式，将使用 MasterAgent 处理任务。
        """
        if self.agent_factory:
            return self.agent_factory()
        
        # 检查是否启用协同模式
        from ..config import settings
        
        if settings.orchestration_enabled:
            # 使用 MasterAgent（需要确保 MasterAgent 已经在主进程启动）
            # 定时任务执行时，我们创建一个轻量的 Agent 而不是 MasterAgent
            # 因为 MasterAgent 应该在主进程中运行
            logger.info("Orchestration enabled, but using local agent for scheduled task")
            from ..core.agent import Agent
            agent = Agent()
            await agent.initialize(start_scheduler=False)
            return agent
        else:
            # 单 Agent 模式
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
    
    def _build_prompt(self, task: ScheduledTask, suppress_send_to_chat: bool = False) -> str:
        """
        构建执行 prompt
        
        Args:
            task: 任务
            suppress_send_to_chat: 是否禁止使用 send_to_chat（避免重复发消息）
        """
        # 基础 prompt
        prompt = task.prompt
        
        # 添加上下文信息
        context_parts = [
            f"[定时任务执行]",
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
                context_parts.append("注意: 不要使用 send_to_chat，系统会自动发送结果通知。请直接返回执行结果。")
            else:
                context_parts.append("提示: 你可以使用 send_to_chat 工具直接发送消息给用户。")
        
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
    gateway: Optional[Any] = None,
    timeout_seconds: int = 300,
) -> Callable[[ScheduledTask], Awaitable[tuple[bool, str]]]:
    """
    创建默认执行器函数
    
    Returns:
        可用于 TaskScheduler 的执行器函数
    """
    executor = TaskExecutor(gateway=gateway, timeout_seconds=timeout_seconds)
    return executor.execute
