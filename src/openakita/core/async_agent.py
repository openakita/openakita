"""
异步 Agent 混入类 - 为 Agent 提供异步能力

作为混入类提供，不破坏现有 Agent 继承链：
- 异步 LLM 调用方法（线程池包装同步 Brain 调用）
- 异步工具执行方法（支持并行执行多个工具）
- 异步 ReAct 循环（在等待 LLM 响应时可处理其他任务）
- 异步 Brain 包装器（支持异步补全和流式输出）
- 同步回退机制：异步调用失败时自动降级到同步模式
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from ..config import settings

if TYPE_CHECKING:
    from .brain import Brain

logger = logging.getLogger(__name__)


class AsyncAgentMixin:
    """异步 Agent 混入类 - 不破坏现有继承链"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        max_workers = getattr(settings, "agent_thread_pool_size", 4)
        self._async_thread_pool = ThreadPoolExecutor(max_workers=max_workers)
        self._async_enabled = getattr(settings, "async_mode_enabled", True)
        self._async_tool_executor = None
        logger.info(
            f"[AsyncAgentMixin] Initialized: async_enabled={self._async_enabled}, max_workers={max_workers}"
        )

    def _init_async_components(self, tool_executor: Any = None) -> None:
        """初始化异步组件"""
        if tool_executor:
            from .async_tool_executor import AsyncToolExecutor

            self._async_tool_executor = AsyncToolExecutor(tool_executor)
            logger.info("[AsyncAgentMixin] Async tool executor initialized")

    async def brain_async(self, *args: Any, **kwargs: Any) -> Any:
        """异步 LLM 调用 - 用线程池包装同步 Brain 调用"""
        if not self._async_enabled:
            return self.brain(*args, **kwargs)

        loop = asyncio.get_event_loop()
        try:
            logger.debug("[AsyncAgentMixin] Executing brain call asynchronously")
            return await loop.run_in_executor(
                self._async_thread_pool, lambda: self.brain(*args, **kwargs)
            )
        except Exception as e:
            logger.warning(f"Async brain call failed, falling back to sync: {e}")
            return self.brain(*args, **kwargs)

    async def brain_async_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[dict]:
        """异步流式 LLM 调用"""
        if not self._async_enabled:
            for chunk in self.brain.think(*args, **kwargs):
                yield chunk
            return

        loop = asyncio.get_event_loop()

        def sync_iter():
            try:
                yield from self.brain.think(*args, **kwargs)
            except Exception as e:
                logger.error(f"Stream error: {e}")
                raise

        try:
            future = loop.run_in_executor(self._async_thread_pool, lambda: list(sync_iter()))
            result = await future
            for item in result:
                yield item
        except Exception as e:
            logger.warning(f"Async stream failed, falling back: {e}")
            for chunk in self.brain.think(*args, **kwargs):
                yield chunk

    async def execute_tools_async(
        self,
        tool_calls: list[dict],
        **kwargs: Any,
    ) -> tuple[list[dict], list[str], list | None]:
        """异步工具执行 - 支持并行执行多个工具"""
        if not getattr(settings, "async_tool_enabled", True):
            return await self.tool_executor.execute_batch(tool_calls, **kwargs)

        if self._async_tool_executor:
            return await self._async_tool_executor.execute_batch_async(tool_calls, **kwargs)

        return await self.tool_executor.execute_batch(tool_calls, **kwargs)

    async def react_loop_async(
        self,
        messages: list[dict],
        system: str = "",
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> Any:
        """异步 ReAct 循环"""
        while True:
            thought = await self.brain_async(
                messages=messages, system=system, tools=tools, **kwargs
            )

            if not hasattr(thought, "tool_calls") or not thought.tool_calls:
                return thought

            tool_results = await self.execute_tools_async(thought.tool_calls)

            messages.extend(
                [
                    {"role": "assistant", "content": thought.content},
                    {
                        "role": "user",
                        "content": f"Tool results: {tool_results[0]}",
                    },
                ]
            )

    def create_async_brain_wrapper(self, brain_instance: "Brain") -> "AsyncBrainWrapper":
        """创建异步 Brain 包装器"""
        return AsyncBrainWrapper(brain_instance)

    def shutdown_async(self) -> None:
        """关闭异步组件"""
        if self._async_tool_executor:
            self._async_tool_executor.shutdown()
        self._async_thread_pool.shutdown(wait=True)


class AsyncBrainWrapper:
    """异步 Brain 包装器 - 支持异步补全和流式输出"""

    def __init__(self, sync_brain: "Brain"):
        self._brain = sync_brain
        self._loop = asyncio.get_event_loop()
        self._executor = ThreadPoolExecutor(max_workers=2)

    async def create_messages_async(self, *args: Any, **kwargs: Any) -> Any:
        """异步消息创建"""
        return await self._loop.run_in_executor(
            self._executor, lambda: self._brain.messages_create(*args, **kwargs)
        )

    async def create_messages_async_stream(self, *args: Any, **kwargs: Any) -> Any:
        """异步流式响应"""
        return self._brain.messages_create_async(*args, **kwargs)

    async def think_async(self, *args: Any, **kwargs: Any) -> Any:
        """异步思考"""
        return await self._loop.run_in_executor(
            self._executor, lambda: self._brain.think(*args, **kwargs)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._brain, name)
