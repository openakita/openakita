"""
Hook extension system

Modeled after Claude Code's 28 lifecycle hook events.
Supports:
- Shell script hooks
- Python callback hooks
- HTTP webhook hooks
- Configuration file declaration + runtime dynamic registration
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class HookEvent(StrEnum):
    """Lifecycle hook events"""

    # Tool lifecycle
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_USE_FAILURE = "post_tool_use_failure"

    # Session lifecycle
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # Agent lifecycle
    STOP = "stop"
    STOP_FAILURE = "stop_failure"
    SUB_AGENT_START = "sub_agent_start"
    SUB_AGENT_STOP = "sub_agent_stop"

    # Context management
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"

    # Permission
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_DENIED = "permission_denied"

    # Notification
    NOTIFICATION = "notification"
    USER_PROMPT_SUBMIT = "user_prompt_submit"

    # Task management
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"

    # Configuration
    CONFIG_CHANGE = "config_change"

    # File system
    FILE_CHANGED = "file_changed"
    CWD_CHANGED = "cwd_changed"

    # Worktree
    WORKTREE_CREATE = "worktree_create"
    WORKTREE_REMOVE = "worktree_remove"

    # Custom
    CUSTOM = "custom"


@dataclass
class HookResult:
    """Hook execution result"""

    hook_id: str
    event: str
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: float = 0


class HookHandler:
    """Base class for hook handlers"""

    def __init__(self, hook_id: str, events: list[HookEvent]) -> None:
        self.hook_id = hook_id
        self.events = events

    async def execute(self, event: HookEvent, context: dict) -> HookResult:
        raise NotImplementedError


class CallbackHook(HookHandler):
    """Python callback hook"""

    def __init__(
        self,
        hook_id: str,
        events: list[HookEvent],
        callback: Callable[..., Any] | Callable[..., Coroutine],
    ) -> None:
        super().__init__(hook_id, events)
        self._callback = callback

    async def execute(self, event: HookEvent, context: dict) -> HookResult:
        import time

        start = time.monotonic()
        try:
            result = self._callback(event, context)
            if asyncio.iscoroutine(result):
                result = await result
            duration = (time.monotonic() - start) * 1000
            return HookResult(
                hook_id=self.hook_id,
                event=event.value,
                success=True,
                output=str(result) if result else "",
                duration_ms=duration,
            )
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            return HookResult(
                hook_id=self.hook_id,
                event=event.value,
                success=False,
                error=str(e),
                duration_ms=duration,
            )


class ShellHook(HookHandler):
    """Shell script hook"""

    def __init__(
        self,
        hook_id: str,
        events: list[HookEvent],
        command: str,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(hook_id, events)
        self._command = command
        self._timeout = timeout

    async def execute(self, event: HookEvent, context: dict) -> HookResult:
        import json
        import time

        start = time.monotonic()
        env_context = json.dumps(context, default=str, ensure_ascii=False)
        try:
            proc = await asyncio.create_subprocess_shell(
                self._command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"HOOK_EVENT": event.value, "HOOK_CONTEXT": env_context},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            duration = (time.monotonic() - start) * 1000
            return HookResult(
                hook_id=self.hook_id,
                event=event.value,
                success=proc.returncode == 0,
                output=stdout.decode(errors="replace") if stdout else "",
                error=stderr.decode(errors="replace") if stderr else "",
                duration_ms=duration,
            )
        except (asyncio.TimeoutError, TimeoutError):
            duration = (time.monotonic() - start) * 1000
            return HookResult(
                hook_id=self.hook_id,
                event=event.value,
                success=False,
                error=f"Shell hook timed out after {self._timeout}s",
                duration_ms=duration,
            )
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            return HookResult(
                hook_id=self.hook_id,
                event=event.value,
                success=False,
                error=str(e),
                duration_ms=duration,
            )


class HookExecutor:
    """Hook executor"""

    def __init__(self) -> None:
        self._handlers: list[HookHandler] = []

    def register(self, handler: HookHandler) -> None:
        """Register a hook handler."""
        self._handlers.append(handler)
        logger.debug(
            "Registered hook %s for events: %s",
            handler.hook_id,
            [e.value for e in handler.events],
        )

    def unregister(self, hook_id: str) -> None:
        """Unregister a hook handler."""
        self._handlers = [h for h in self._handlers if h.hook_id != hook_id]

    def register_callback(
        self,
        hook_id: str,
        events: list[HookEvent],
        callback: Callable,
    ) -> None:
        """Convenience method: register a Python callback hook."""
        self.register(CallbackHook(hook_id, events, callback))

    def register_shell(
        self,
        hook_id: str,
        events: list[HookEvent],
        command: str,
        timeout: float = 30.0,
    ) -> None:
        """Convenience method: register a shell script hook."""
        self.register(ShellHook(hook_id, events, command, timeout))

    async def execute(
        self,
        event: HookEvent,
        context: dict | None = None,
    ) -> list[HookResult]:
        """Execute all matching hooks for the given event."""
        context = context or {}
        matching = [h for h in self._handlers if event in h.events]

        if not matching:
            return []

        results = []
        for handler in matching:
            try:
                result = await handler.execute(event, context)
                results.append(result)
                if not result.success:
                    logger.warning(
                        "Hook %s failed for %s: %s",
                        handler.hook_id,
                        event.value,
                        result.error,
                    )
            except Exception as e:
                logger.error(
                    "Hook %s crashed for %s: %s",
                    handler.hook_id,
                    event.value,
                    e,
                )
                results.append(
                    HookResult(
                        hook_id=handler.hook_id,
                        event=event.value,
                        success=False,
                        error=str(e),
                    )
                )

        return results

    @property
    def handler_count(self) -> int:
        return len(self._handlers)


# Global hook executor instance
_global_executor: HookExecutor | None = None


def get_hook_executor() -> HookExecutor:
    """Get the global hook executor."""
    global _global_executor
    if _global_executor is None:
        _global_executor = HookExecutor()
    return _global_executor


def set_hook_executor(executor: HookExecutor) -> None:
    """Replace the global hook executor (for testing)."""
    global _global_executor
    _global_executor = executor
