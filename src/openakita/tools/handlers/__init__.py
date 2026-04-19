"""
System skill handler registry.

Manages execution handlers for system skills (system: true).
Each handler corresponds to a category of system tools (e.g., browser, filesystem, memory, etc.).
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


# Handler type: sync or async function
HandlerFunc = Callable[[str, dict], str | Awaitable[str]]

# Per-tool permission callback: (tool_name, tool_input) → PermissionDecision | None
# Returning None means "no opinion" (defer to other layers).
ToolPermissionCheck = Callable[[str, dict], Any]


class SystemHandlerRegistry:
    """
    System skill handler registry.

    Registers and manages execution handlers for system skills.

    Usage:
    ```python
    registry = SystemHandlerRegistry()

    # Register handlers
    registry.register("browser", browser_handler)
    registry.register("filesystem", filesystem_handler)

    # Execute
    result = await registry.execute("browser", "browser_navigate", {"url": "..."})
    ```
    """

    # Type for handler-level concurrency safety callbacks:
    #   (tool_name, tool_input) -> bool | None
    # Return True/False to override, None to fall back to default.
    ConcurrencyCheck = Callable[[str, dict], bool | None]

    def __init__(self):
        self._handlers: dict[str, HandlerFunc] = {}
        self._tool_to_handler: dict[str, str] = {}  # tool_name -> handler_name mapping
        self._permission_checks: dict[str, ToolPermissionCheck] = {}  # tool_name -> check fn
        self._concurrency_checks: dict[str, "SystemHandlerRegistry.ConcurrencyCheck"] = {}

    def register(
        self,
        handler_name: str,
        handler: HandlerFunc,
        tool_names: list[str] | None = None,
        check_permissions: ToolPermissionCheck | None = None,
    ) -> None:
        """
        Register a handler.

        Args:
            handler_name: Handler name (e.g., 'browser', 'filesystem').
            handler: Handler function with signature (tool_name, params) -> str.
            tool_names: List of tool names handled by this handler.
                If None, automatically read from the TOOLS attribute of the
                handler's owner instance (via __self__.TOOLS when handler is
                a bound method).
            check_permissions: Optional per-tool permission callback.
                Invoked by ToolExecutor.check_permission() after mode+policy
                checks pass.  Returns PermissionDecision or None.
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
                        f"[Registry] Tool name conflict: '{tool_name}' was registered to '{existing}', "
                        f"now overridden by '{handler_name}'"
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

        logger.info(
            "Registered handler: %s (%d tools)",
            handler_name,
            len(tool_names or []),
        )

    def unregister(self, handler_name: str) -> bool:
        """
        Unregister a handler.

        Args:
            handler_name: Handler name.

        Returns:
            True if successfully unregistered, False otherwise.
        """
        if handler_name in self._handlers:
            del self._handlers[handler_name]
            # Clean up tool_to_handler mapping
            self._tool_to_handler = {
                k: v for k, v in self._tool_to_handler.items() if v != handler_name
            }
            logger.info(f"Unregistered system handler: {handler_name}")
            return True
        return False

    def get_handler(self, handler_name: str) -> HandlerFunc | None:
        """Get a handler by name."""
        return self._handlers.get(handler_name)

    def get_handler_for_tool(self, tool_name: str) -> HandlerFunc | None:
        """Get a handler by tool name."""
        handler_name = self._tool_to_handler.get(tool_name)
        if handler_name:
            return self._handlers.get(handler_name)
        return None

    def map_tool_to_handler(self, tool_name: str, handler_name: str) -> None:
        """
        Map a tool name to a handler.

        Args:
            tool_name: Tool name.
            handler_name: Handler name.
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
        Execute a handler.

        Args:
            handler_name: Handler name.
            tool_name: Tool name.
            params: Parameter dictionary.

        Returns:
            Execution result as a string.

        Raises:
            ValueError: Handler not found.
        """
        handler = self._handlers.get(handler_name)
        if not handler:
            raise ValueError(f"Handler not found: {handler_name}")

        logger.debug(f"Executing {handler_name}.{tool_name} with {params}")

        # Execute handler (supports both sync and async)
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
        Execute by tool name.

        Args:
            tool_name: Tool name.
            params: Parameter dictionary.

        Returns:
            Execution result as a string.

        Raises:
            ValueError: Tool not mapped to any handler.
        """
        handler_name = self._tool_to_handler.get(tool_name)
        if not handler_name:
            raise ValueError(f"No handler mapped for tool: {tool_name}")

        return await self.execute(handler_name, tool_name, params)

    def has_handler(self, handler_name: str) -> bool:
        """Check if a handler exists."""
        return handler_name in self._handlers

    def unmap_tool(self, tool_name: str) -> bool:
        """Remove a single tool-to-handler mapping.

        Returns:
            True if removed, False if the tool was not mapped.
        """
        if tool_name in self._tool_to_handler:
            del self._tool_to_handler[tool_name]
            return True
        return False

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is mapped."""
        return tool_name in self._tool_to_handler

    def get_permission_check(self, tool_name: str) -> ToolPermissionCheck | None:
        """Return the per-tool permission callback, if any."""
        return self._permission_checks.get(tool_name)

    def list_handlers(self) -> list[str]:
        """List all handler names."""
        return list(self._handlers.keys())

    def list_tools(self) -> list[str]:
        """List all mapped tool names."""
        return list(self._tool_to_handler.keys())

    def get_handler_tools(self, handler_name: str) -> list[str]:
        """Get all tools handled by a given handler."""
        return [tool for tool, handler in self._tool_to_handler.items() if handler == handler_name]

    def get_handler_name_for_tool(self, tool_name: str) -> str | None:
        """Get the handler name for a tool (used for concurrency/mutex policies, etc.)."""
        return self._tool_to_handler.get(tool_name)

    def set_concurrency_check(
        self, handler_name: str, check: "SystemHandlerRegistry.ConcurrencyCheck"
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
        """Number of handlers."""
        return len(self._handlers)

    @property
    def tool_count(self) -> int:
        """Number of mapped tools."""
        return len(self._tool_to_handler)


# Global handler registry
default_handler_registry = SystemHandlerRegistry()


def register_handler(
    handler_name: str,
    handler: HandlerFunc,
    tool_names: list[str] | None = None,
) -> None:
    """Register a handler to the default registry."""
    default_handler_registry.register(handler_name, handler, tool_names)


def get_handler(handler_name: str) -> HandlerFunc | None:
    """Get a handler from the default registry."""
    return default_handler_registry.get_handler(handler_name)


async def execute_tool(tool_name: str, params: dict[str, Any]) -> str:
    """Execute a tool via the default registry."""
    return await default_handler_registry.execute_by_tool(tool_name, params)
