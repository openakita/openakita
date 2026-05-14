"""
系统技能处理器注册表

管理系统技能（system: true）的执行处理器。
每个处理器对应一类系统工具（如 browser, filesystem, memory 等）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openakita.core.policy_v2 import ApprovalClass, DecisionSource

logger = logging.getLogger(__name__)


# 处理器类型：同步或异步函数
HandlerFunc = Callable[[str, dict], str | Awaitable[str]]

# Per-tool permission callback: (tool_name, tool_input) → PermissionDecision | None
# Returning None means "no opinion" (defer to other layers).
ToolPermissionCheck = Callable[[str, dict], Any]


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

    # Type for handler-level concurrency safety callbacks:
    #   (tool_name, tool_input) -> bool | None
    # Return True/False to override, None to fall back to default.
    ConcurrencyCheck = Callable[[str, dict], bool | None]

    def __init__(self):
        self._handlers: dict[str, HandlerFunc] = {}
        self._tool_to_handler: dict[str, str] = {}  # tool_name -> handler_name
        self._permission_checks: dict[str, ToolPermissionCheck] = {}  # tool_name -> check fn
        self._concurrency_checks: dict[str, SystemHandlerRegistry.ConcurrencyCheck] = {}
        # tool_name -> (ApprovalClass, DecisionSource) — populated from
        # explicit register(tool_classes=) param and handler.TOOL_CLASSES attr.
        # Used by policy_v2.ApprovalClassifier via .get_tool_class() lookup.
        self._tool_classes: dict[str, tuple[Any, Any]] = {}

    def register(
        self,
        handler_name: str,
        handler: HandlerFunc,
        tool_names: list[str] | None = None,
        check_permissions: ToolPermissionCheck | None = None,
        tool_classes: dict[str, ApprovalClass] | None = None,
    ) -> None:
        """
        注册处理器

        Args:
            handler_name: 处理器名称（如 'browser', 'filesystem'）
            handler: 处理器函数，签名为 (tool_name, params) -> str
            tool_names: 该处理器处理的工具名称列表。
                如果为 None，自动从 handler 所属实例的 TOOLS 属性读取
                （handler 是 bound method 时通过 __self__.TOOLS 获取）。
            check_permissions: Optional per-tool permission callback.
                Invoked by ToolExecutor.check_permission() after mode+policy
                checks pass.  Returns PermissionDecision or None.
            tool_classes: Optional explicit ApprovalClass per tool. Takes
                priority over handler's TOOL_CLASSES class attribute (if both
                are present, ``most_strict`` is used to pick the safer one).
                When omitted, the registry will try ``handler.__self__.TOOL_CLASSES``.
                See docs/policy_v2_research.md §4.21 for the developer cookbook.
        """
        self._handlers[handler_name] = handler

        if tool_names is None:
            owner = getattr(handler, "__self__", None)
            tool_names = getattr(owner, "TOOLS", None)

        if tool_names:
            for tool_name in tool_names:
                existing = self._tool_to_handler.get(tool_name)
                if existing and existing != handler_name:
                    logger.warning(
                        f"[Registry] 工具名冲突: '{tool_name}' 已注册到 '{existing}'，"
                        f"现被 '{handler_name}' 覆盖"
                    )
                self._tool_to_handler[tool_name] = handler_name
        else:
            logger.warning(
                "Handler '%s' registered with 0 tools — "
                "add a TOOLS class attribute to the handler class",
                handler_name,
            )

        if check_permissions and tool_names:
            for tool_name in tool_names:
                self._permission_checks[tool_name] = check_permissions

        # ApprovalClass collection — opt-in: only does anything when caller
        # passes tool_classes= or handler defines TOOL_CLASSES. Importing
        # policy_v2 lazily to avoid bootstrap cycle from handlers/__init__.py.
        self._collect_tool_classes(handler, tool_names or [], tool_classes)

        # C19-D2: WARN for tools without an explicit ApprovalClass declaration.
        # Falls back to ApprovalClassifier heuristics at decision time, which
        # works but is opaque (cookbook §4.21). The check is the same data
        # source as the CI completeness gate, so dev WARN ↔ CI red are aligned.
        if tool_names:
            unclassified = [
                t for t in tool_names if t not in self._tool_classes
            ]
            for tool in unclassified:
                logger.warning(
                    "[Policy] Tool %r in handler %r has no explicit "
                    "ApprovalClass (will fall back to classifier heuristics). "
                    "Add an entry under TOOL_CLASSES in the handler class, or "
                    "pass tool_classes={...} to register(); see "
                    "docs/policy_v2_research.md §4.21",
                    tool,
                    handler_name,
                )

        logger.info(
            "Registered handler: %s (%d tools)",
            handler_name,
            len(tool_names or []),
        )

    def _collect_tool_classes(
        self,
        handler: HandlerFunc,
        tool_names: list[str],
        register_param: dict[str, ApprovalClass] | None,
    ) -> None:
        """Merge register(tool_classes=) + handler.TOOL_CLASSES into ``self._tool_classes``.

        If both sources name the same tool, ``most_strict`` is applied so the
        safer classification wins. Repeated registrations of the same handler
        (rare, but possible) also fold via ``most_strict`` against the existing
        entry — never accidentally relax an already-strict label.
        """
        owner = getattr(handler, "__self__", None)
        handler_attr = (
            getattr(owner, "TOOL_CLASSES", None) if owner is not None else None
        )

        if not register_param and not handler_attr:
            return

        # Lazy import to avoid handlers ↔ policy_v2 bootstrap cycle.
        from openakita.core.policy_v2 import DecisionSource, most_strict

        new_entries: dict[str, tuple[Any, Any]] = {}

        if register_param:
            for tool, klass in register_param.items():
                new_entries[tool] = (klass, DecisionSource.EXPLICIT_REGISTER_PARAM)

        if handler_attr:
            for tool, klass in handler_attr.items():
                attr_entry = (klass, DecisionSource.EXPLICIT_HANDLER_ATTR)
                existing = new_entries.get(tool)
                new_entries[tool] = (
                    most_strict([existing, attr_entry]) if existing else attr_entry
                )

        # Filter out tools that don't actually belong to this handler — likely
        # a typo or stale entry. Storing them anyway would cause a silent
        # "phantom" classification that a future plugin could inadvertently
        # inherit. WARN + drop is safer than WARN + keep.
        if tool_names:
            tool_names_set = set(tool_names)
            stray = [t for t in new_entries if t not in tool_names_set]
            for tool in stray:
                logger.warning(
                    "[Registry] tool_classes lists %r which is not in this "
                    "handler's TOOLS list — dropping (possible typo)",
                    tool,
                )
                new_entries.pop(tool, None)

        for tool, entry in new_entries.items():
            existing = self._tool_classes.get(tool)
            self._tool_classes[tool] = (
                most_strict([existing, entry]) if existing else entry
            )

    def get_tool_class(
        self, tool_name: str
    ) -> tuple[ApprovalClass, DecisionSource] | None:
        """ApprovalClassifier explicit_lookup callback."""
        return self._tool_classes.get(tool_name)

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
            removed_tools = {
                tool
                for tool, mapped in self._tool_to_handler.items()
                if mapped == handler_name
            }
            self._tool_to_handler = {
                k: v for k, v in self._tool_to_handler.items() if v != handler_name
            }
            for tool in removed_tools:
                self._tool_classes.pop(tool, None)
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

    def unmap_tool(self, tool_name: str) -> bool:
        """移除单个工具名到处理器的映射。

        Returns:
            是否成功移除（不存在时返回 False）
        """
        if tool_name in self._tool_to_handler:
            del self._tool_to_handler[tool_name]
            self._tool_classes.pop(tool_name, None)
            return True
        return False

    def has_tool(self, tool_name: str) -> bool:
        """检查工具是否已映射"""
        return tool_name in self._tool_to_handler

    def get_permission_check(self, tool_name: str) -> ToolPermissionCheck | None:
        """Return the per-tool permission callback, if any."""
        return self._permission_checks.get(tool_name)

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

    def set_concurrency_check(
        self, handler_name: str, check: SystemHandlerRegistry.ConcurrencyCheck
    ) -> None:
        """Register a per-handler concurrency safety callback.

        The callback ``(tool_name, tool_input) -> bool | None`` lets a
        handler override the static ``_CONCURRENCY_SAFE_TOOLS`` set in
        ``ToolExecutor``.  Return *None* to fall back to the default.
        """
        self._concurrency_checks[handler_name] = check

    def check_concurrency_safe(self, tool_name: str, tool_input: dict) -> bool | None:
        """Query the handler-level concurrency callback for *tool_name*.

        Returns ``True`` / ``False`` if the handler explicitly overrides,
        or ``None`` when there is no registered check (caller should use
        its own default logic).
        """
        handler_name = self._tool_to_handler.get(tool_name)
        if handler_name is None:
            return None
        check = self._concurrency_checks.get(handler_name)
        if check is None:
            return None
        try:
            return check(tool_name, tool_input)
        except Exception:
            return None

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
