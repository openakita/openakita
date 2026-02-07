"""
系统技能处理器注册表

管理系统技能（system: true）的执行处理器。
每个处理器对应一类系统工具（如 browser, filesystem, memory 等）。
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


# 处理器类型：同步或异步函数
HandlerFunc = Callable[[str, dict], str | Awaitable[str]]


class SystemHandlerRegistry:
    """
    系统技能处理器注册表

    注册和管理系统技能的执行处理器。

    使用方式:
    ```python
    registry = SystemHandlerRegistry()

    # 注册处理器
    registry.register("browser", browser_handler)
    registry.register("filesystem", filesystem_handler)

    # 执行
    result = await registry.execute("browser", "browser_navigate", {"url": "..."})
    ```
    """

    def __init__(self):
        self._handlers: dict[str, HandlerFunc] = {}
        self._tool_to_handler: dict[str, str] = {}  # tool_name -> handler_name

    def register(
        self,
        handler_name: str,
        handler: HandlerFunc,
        tool_names: list[str] | None = None,
    ) -> None:
        """
        注册处理器

        Args:
            handler_name: 处理器名称（如 'browser', 'filesystem'）
            handler: 处理器函数，签名为 (tool_name, params) -> str
            tool_names: 该处理器处理的工具名称列表（可选，用于快速查找）
        """
        self._handlers[handler_name] = handler

        if tool_names:
            for tool_name in tool_names:
                self._tool_to_handler[tool_name] = handler_name

        logger.info(f"Registered system handler: {handler_name}")

    def unregister(self, handler_name: str) -> bool:
        """
        注销处理器

        Args:
            handler_name: 处理器名称

        Returns:
            是否成功
        """
        if handler_name in self._handlers:
            del self._handlers[handler_name]
            # 清理 tool_to_handler 映射
            self._tool_to_handler = {
                k: v for k, v in self._tool_to_handler.items() if v != handler_name
            }
            logger.info(f"Unregistered system handler: {handler_name}")
            return True
        return False

    def get_handler(self, handler_name: str) -> HandlerFunc | None:
        """获取处理器"""
        return self._handlers.get(handler_name)

    def get_handler_for_tool(self, tool_name: str) -> HandlerFunc | None:
        """根据工具名获取处理器"""
        handler_name = self._tool_to_handler.get(tool_name)
        if handler_name:
            return self._handlers.get(handler_name)
        return None

    def map_tool_to_handler(self, tool_name: str, handler_name: str) -> None:
        """
        建立工具名到处理器的映射

        Args:
            tool_name: 工具名称
            handler_name: 处理器名称
        """
        if handler_name not in self._handlers:
            logger.warning(
                f"Handler '{handler_name}' not registered, but mapping tool '{tool_name}'"
            )
        self._tool_to_handler[tool_name] = handler_name

    async def execute(
        self,
        handler_name: str,
        tool_name: str,
        params: dict[str, Any],
    ) -> str:
        """
        执行处理器

        Args:
            handler_name: 处理器名称
            tool_name: 工具名称
            params: 参数字典

        Returns:
            执行结果字符串

        Raises:
            ValueError: 处理器不存在
        """
        handler = self._handlers.get(handler_name)
        if not handler:
            raise ValueError(f"Handler not found: {handler_name}")

        logger.debug(f"Executing {handler_name}.{tool_name} with {params}")

        # 执行处理器（支持同步和异步）
        import asyncio

        result = handler(tool_name, params)

        if asyncio.iscoroutine(result):
            result = await result

        return result

    async def execute_by_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> str:
        """
        根据工具名执行

        Args:
            tool_name: 工具名称
            params: 参数字典

        Returns:
            执行结果字符串

        Raises:
            ValueError: 工具未映射到处理器
        """
        handler_name = self._tool_to_handler.get(tool_name)
        if not handler_name:
            raise ValueError(f"No handler mapped for tool: {tool_name}")

        return await self.execute(handler_name, tool_name, params)

    def has_handler(self, handler_name: str) -> bool:
        """检查处理器是否存在"""
        return handler_name in self._handlers

    def has_tool(self, tool_name: str) -> bool:
        """检查工具是否已映射"""
        return tool_name in self._tool_to_handler

    def list_handlers(self) -> list[str]:
        """列出所有处理器名称"""
        return list(self._handlers.keys())

    def list_tools(self) -> list[str]:
        """列出所有已映射的工具名称"""
        return list(self._tool_to_handler.keys())

    def get_handler_tools(self, handler_name: str) -> list[str]:
        """获取某个处理器处理的所有工具"""
        return [tool for tool, handler in self._tool_to_handler.items() if handler == handler_name]

    def get_handler_name_for_tool(self, tool_name: str) -> str | None:
        """获取工具对应的处理器名称（用于并发/互斥策略等）"""
        return self._tool_to_handler.get(tool_name)

    @property
    def handler_count(self) -> int:
        """处理器数量"""
        return len(self._handlers)

    @property
    def tool_count(self) -> int:
        """已映射工具数量"""
        return len(self._tool_to_handler)


# 全局处理器注册表
default_handler_registry = SystemHandlerRegistry()


def register_handler(
    handler_name: str,
    handler: HandlerFunc,
    tool_names: list[str] | None = None,
) -> None:
    """注册处理器到默认注册表"""
    default_handler_registry.register(handler_name, handler, tool_names)


def get_handler(handler_name: str) -> HandlerFunc | None:
    """从默认注册表获取处理器"""
    return default_handler_registry.get_handler(handler_name)


async def execute_tool(tool_name: str, params: dict[str, Any]) -> str:
    """通过默认注册表执行工具"""
    return await default_handler_registry.execute_by_tool(tool_name, params)
