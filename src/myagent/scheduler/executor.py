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
    
    async def execute(self, task: ScheduledTask) -> tuple[bool, str]:
        """
        执行任务
        
        Args:
            task: 要执行的任务
        
        Returns:
            (success, result_or_error)
        """
        logger.info(f"TaskExecutor: executing task {task.id} ({task.name})")
        
        try:
            # 1. 创建 Agent
            agent = await self._create_agent()
            
            # 2. 如果任务有 IM 通道信息，注入 IM 上下文
            im_context_set = False
            if task.channel_id and task.chat_id and self.gateway:
                im_context_set = await self._setup_im_context(agent, task)
            
            # 3. 构建执行 prompt
            prompt = self._build_prompt(task)
            
            # 4. 执行（带超时）
            try:
                result = await asyncio.wait_for(
                    self._run_agent(agent, prompt),
                    timeout=self.timeout_seconds
                )
            except asyncio.TimeoutError:
                error_msg = f"Task execution timed out after {self.timeout_seconds}s"
                logger.error(f"TaskExecutor: {error_msg}")
                await self._send_notification(task, success=False, message=error_msg)
                return False, error_msg
            finally:
                # 清理 IM 上下文
                if im_context_set:
                    self._cleanup_im_context(agent)
            
            # 5. 发送结果通知（如果 Agent 没有通过 send_to_chat 发送）
            # 检查 Agent 是否已经发送过消息
            if not getattr(agent, '_task_message_sent', False):
                await self._send_notification(task, success=True, message=result)
            
            # 6. 清理 Agent
            await self._cleanup_agent(agent)
            
            logger.info(f"TaskExecutor: task {task.id} completed successfully")
            return True, result
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"TaskExecutor: task {task.id} failed: {error_msg}")
            await self._send_notification(task, success=False, message=error_msg)
            return False, error_msg
    
    async def _setup_im_context(self, agent: Any, task: ScheduledTask) -> bool:
        """
        为定时任务注入 IM 上下文，让 Agent 可以使用 send_to_chat
        """
        try:
            from ..core.agent import Agent
            from ..sessions import Session
            
            # 创建虚拟 Session（用于 send_to_chat）
            virtual_session = Session(
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
        """创建 Agent 实例（不启动 scheduler，避免重复执行任务）"""
        if not self.agent_factory:
            # 延迟导入，避免循环依赖
            from ..core.agent import Agent
            agent = Agent()
            # 不启动 scheduler，因为这是定时任务执行环境
            await agent.initialize(start_scheduler=False)
            return agent
        
        return self.agent_factory()
    
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
    
    def _build_prompt(self, task: ScheduledTask) -> str:
        """构建执行 prompt"""
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
        
        # 如果任务有 IM 通道，提示可以用 send_to_chat
        if task.channel_id and task.chat_id:
            context_parts.append("")
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
        """发送结果通知"""
        if not task.channel_id or not task.chat_id:
            logger.debug(f"Task {task.id} has no notification channel configured")
            return
        
        if not self.gateway:
            logger.debug(f"No gateway configured, skipping notification")
            return
        
        try:
            # 判断是否是提醒类任务（根据任务名称或描述）
            is_reminder = any(keyword in task.name.lower() or keyword in task.description.lower() 
                            for keyword in ['提醒', '通知', 'remind', 'alert', 'notify'])
            
            if is_reminder and success:
                # 提醒类任务：直接发送 Agent 的回复内容
                notification = message
            else:
                # 其他任务：发送执行报告
                status = "✅ 成功" if success else "❌ 失败"
                notification = f"""**定时任务执行通知**

任务: {task.name}
状态: {status}
时间: {task.last_run.strftime('%Y-%m-%d %H:%M:%S') if task.last_run else 'N/A'}

结果:
{message[:1000]}{"..." if len(message) > 1000 else ""}
"""
            
            await self.gateway.send(
                channel=task.channel_id,
                chat_id=task.chat_id,
                text=notification,
            )
            
            logger.info(f"Sent notification for task {task.id}")
            
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")


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
