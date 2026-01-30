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
            
            # 2. 构建执行 prompt
            prompt = self._build_prompt(task)
            
            # 3. 执行（带超时）
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
            
            # 4. 发送结果通知
            await self._send_notification(task, success=True, message=result)
            
            # 5. 清理 Agent
            await self._cleanup_agent(agent)
            
            logger.info(f"TaskExecutor: task {task.id} completed successfully")
            return True, result
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"TaskExecutor: task {task.id} failed: {error_msg}")
            await self._send_notification(task, success=False, message=error_msg)
            return False, error_msg
    
    async def _create_agent(self) -> Any:
        """创建 Agent 实例"""
        if not self.agent_factory:
            # 延迟导入，避免循环依赖
            from ..core.agent import Agent
            agent = Agent()
            await agent.initialize()
            return agent
        
        return self.agent_factory()
    
    async def _run_agent(self, agent: Any, prompt: str) -> str:
        """运行 Agent"""
        # 调用 Agent 的 chat 方法
        if hasattr(agent, "chat"):
            return await agent.chat(prompt)
        elif hasattr(agent, "execute_task_from_message"):
            result = await agent.execute_task_from_message(prompt)
            return result.data if result.success else result.error
        else:
            raise ValueError("Agent does not have chat or execute_task_from_message method")
    
    async def _cleanup_agent(self, agent: Any) -> None:
        """清理 Agent"""
        if hasattr(agent, "shutdown"):
            await agent.shutdown()
    
    def _build_prompt(self, task: ScheduledTask) -> str:
        """构建执行 prompt"""
        # 基础 prompt
        prompt = task.prompt
        
        # 判断是否是提醒类任务
        is_reminder = any(keyword in task.name.lower() or keyword in task.description.lower() 
                        for keyword in ['提醒', '通知', 'remind', 'alert', 'notify'])
        
        # 添加上下文信息
        context_parts = [
            f"[定时任务执行]",
            f"任务名称: {task.name}",
            f"任务描述: {task.description}",
            "",
        ]
        
        if is_reminder:
            # 提醒类任务：直接输出内容，系统会自动发送给用户
            context_parts.extend([
                "这是一个提醒任务。请直接生成提醒内容，系统会自动发送给用户。",
                "**重要：不要调用 send_to_chat 工具，直接输出提醒文字即可。**",
                "",
                "提醒内容要求:",
                prompt,
            ])
        else:
            # 其他任务：正常执行
            context_parts.extend([
                "请执行以下任务:",
                prompt,
            ])
        
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
