"""Canonical public tool-execution surface.

``ToolExecutor`` combines the internal runtime base with focused retry,
truncation, overflow, and result-budget helpers.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from openakita.core._tool_runtime import ToolExecutor as _RuntimeToolExecutorBase
from openakita.core._tool_runtime import ToolResultWithHint as _RuntimeToolResultWithHint
from openakita.core._tool_runtime import ToolSkipped as _RuntimeToolSkipped
from openakita.runtime.io import (
    DEFAULT_TOOL_RESULT_MAX_CHARS as _V2_DEFAULT_TOOL_RESULT_MAX_CHARS,
)
from openakita.runtime.io import (
    MAX_TOOL_RESULT_CHARS as _V2_MAX_TOOL_RESULT_CHARS,
)
from openakita.runtime.io import (
    OVERFLOW_MARKER as _V2_OVERFLOW_MARKER,
)
from openakita.runtime.io import (
    cleanup_overflow_files as _v2_cleanup_overflow_files,
)
from openakita.runtime.io import (
    save_overflow as _v2_save_overflow,
)
from openakita.runtime.io import (
    smart_truncate as _v2_smart_truncate,
)
from openakita.runtime.retry_policy import (
    RetryPolicy,
    default_tool_retry_policy,
    is_retriable_tool_error,
)

__all__ = [
    "DEFAULT_TOOL_RESULT_MAX_CHARS",
    "MAX_TOOL_RESULT_CHARS",
    "OVERFLOW_MARKER",
    "ToolExecutor",
    "ToolExecutorProtocol",
    "ToolResultWithHint",
    "ToolSkipped",
    "cleanup_overflow_files",
    "default_tool_retry_policy",
    "is_retriable_tool_error",
    "save_overflow",
    "smart_truncate",
]


# ---- Re-anchored public surface ----

#: Default tool-result size cap, re-anchored on :mod:`runtime.io.truncate`.
DEFAULT_TOOL_RESULT_MAX_CHARS: int = _V2_DEFAULT_TOOL_RESULT_MAX_CHARS

#: Backward-compatible alias for the cap.
MAX_TOOL_RESULT_CHARS: int = _V2_MAX_TOOL_RESULT_CHARS

#: Sentinel embedded in truncated tool output, re-anchored on runtime.io.
OVERFLOW_MARKER: str = _V2_OVERFLOW_MARKER

#: Public type alias for the ``(text, hint)`` tuple every tool returns.
ToolResultWithHint = _RuntimeToolResultWithHint

# Public leaf helpers backed by ``runtime.io``.
save_overflow = _v2_save_overflow
smart_truncate = _v2_smart_truncate
cleanup_overflow_files = _v2_cleanup_overflow_files


class ToolSkipped(_RuntimeToolSkipped):
    """User-initiated skip of the current tool execution.

    Published under :mod:`openakita.agent.tools` and backed by the runtime type.
    """


@runtime_checkable
class ToolExecutorProtocol(Protocol):
    """Minimal v2 surface that agent.* callers depend on.

    The runtime base exposes ~40 public and private methods; the
    Protocol below names the handful that v2 callers inside
    ``agent.*`` actually depend on so concrete v2 executors can
    satisfy it without inheriting the full runtime implementation.
    """

    handler_registry: Any

    async def execute_tool(self, tool_name: str, tool_input: dict) -> ToolResultWithHint:
        """Execute one tool and return ``(text, hint)``."""

    async def execute_batch(self, tool_calls: list[dict], *, agent: Any = None) -> list[Any]:
        """Execute a batch of tool calls; honours concurrency policy."""

    def get_handler_name(self, tool_name: str) -> str | None:
        """Map a tool name to the handler that owns it."""

    def canonicalize_tool_name(self, tool_name: str) -> str:
        """Resolve aliases / case-folding to the canonical tool name."""

    def check_permission(self, tool_name: str, tool_input: dict) -> Any:
        """Return the permission decision for ``tool_name``."""

    def clear_confirm_cache(self) -> None:
        """Drop any cached confirm prompts."""


class ToolExecutor(_RuntimeToolExecutorBase):
    """Canonical ToolExecutor with focused runtime composition.

    Inherits the complete private runtime implementation and adds:

    * a public :attr:`retry_policy` accessor that returns the v2
      :class:`runtime.retry_policy.RetryPolicy` built from
      :func:`default_tool_retry_policy`. Callers can swap it.
    * a public :meth:`truncate` helper that always routes through
      :func:`runtime.io.smart_truncate`.
    * a public :meth:`save_overflow` static helper.
    * a public :meth:`is_retriable_error` predicate.

    Deep methods (``execute_tool``, ``execute_batch``,
    ``execute_tool_with_policy``, ``check_permission``, ...) are
    inherited from the runtime base.
    """

    def __init__(
        self,
        handler_registry: Any,
        max_parallel: int = 1,
        *,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        super().__init__(handler_registry=handler_registry, max_parallel=max_parallel)
        # Late-bound so subclasses / callers can swap after construction.
        self._retry_policy: RetryPolicy = retry_policy or default_tool_retry_policy()

    # ---- v2 accessors ----

    @property
    def retry_policy(self) -> RetryPolicy:
        """The v2 :class:`RetryPolicy` driving tool retries."""
        return self._retry_policy

    @retry_policy.setter
    def retry_policy(self, value: RetryPolicy) -> None:
        if not isinstance(value, RetryPolicy):
            raise TypeError(f"retry_policy must be a RetryPolicy instance, got {type(value)}")
        self._retry_policy = value

    @staticmethod
    def truncate(content: str, limit: int, *, label: str = "content") -> tuple[str, bool]:
        """Truncate ``content`` to ``limit`` chars via runtime.io."""
        return _v2_smart_truncate(content, limit, label=label)

    @staticmethod
    def save_overflow(tool_name: str, content: str) -> str:
        """Persist overflow content to a sidecar; returns the file path."""
        return _v2_save_overflow(tool_name, content)

    @staticmethod
    def is_retriable_error(exc: BaseException) -> bool:
        """Return True iff ``exc`` should trigger a tool retry.

        Thin re-anchor on
        :func:`runtime.retry_policy.is_retriable_tool_error`.
        """
        return is_retriable_tool_error(exc)

    # ---- v2 composed operations ----

    async def execute_with_retry(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        policy: RetryPolicy | None = None,
    ) -> ToolResultWithHint:
        """Execute one tool through the v2 retry policy.

        The previous :meth:`execute_tool` does NOT retry by itself; the
        ``ReasoningEngine`` decides at a higher level. The v2 contract
        is simpler: each tool call gets one trip through the configured
        :class:`RetryPolicy`. Non-retriable exceptions
        (``ToolSkipped``, ``ToolConfigError``, etc.) bubble up
        immediately as documented in :func:`is_retriable_tool_error`.
        """
        active_policy = policy or self._retry_policy

        async def _op() -> ToolResultWithHint:
            return await self.execute_tool(tool_name, tool_input)

        return await active_policy.run(_op, retry_predicate=is_retriable_tool_error)

    def truncate_result(
        self,
        text: str,
        *,
        tool_name: str = "tool",
        limit: int | None = None,
    ) -> tuple[str, bool]:
        """Truncate a tool result text to the configured cap.

        Reads the cap from :data:`MAX_TOOL_RESULT_CHARS` when ``limit``
        is None. Routes through :func:`runtime.io.smart_truncate`.
        """
        cap = limit if limit is not None else MAX_TOOL_RESULT_CHARS
        return _v2_smart_truncate(text, cap, label=tool_name)

    @staticmethod
    def list_overflow_files(directory: str | None = None) -> list[str]:
        """Enumerate the on-disk overflow files for diagnostics."""
        from pathlib import Path as _Path

        from openakita.runtime.io import get_overflow_dir

        target = _Path(directory) if directory else get_overflow_dir()
        if not target.exists():
            return []
        files = sorted(target.glob("*.txt"), key=lambda f: f.stat().st_mtime)
        return [str(f.resolve()) for f in files]

    def prune_overflow(self, *, max_files: int | None = None) -> int:
        """Manually trigger overflow eviction; returns count evicted."""
        from openakita.runtime.io import get_overflow_dir, get_overflow_max_files

        cap = max_files if max_files is not None else get_overflow_max_files()
        directory = get_overflow_dir()
        if not directory.exists():
            return 0
        files = sorted(directory.glob("*.txt"), key=lambda f: f.stat().st_mtime)
        to_evict = max(0, len(files) - cap)
        _v2_cleanup_overflow_files(directory, cap)
        return to_evict

    def describe_runtime(self) -> dict[str, Any]:
        """Return a snapshot of v2 runtime config for diagnostics.

        Used by the setup-center UI to render a "current policy"
        panel. Shallow JSON-friendly dict; no custom encoders needed.
        """
        from openakita.runtime.io import get_overflow_dir, get_overflow_max_files

        return {
            "max_tool_result_chars": MAX_TOOL_RESULT_CHARS,
            "overflow_dir": str(get_overflow_dir()),
            "overflow_max_files": get_overflow_max_files(),
            "retry_max_attempts": self._retry_policy.max_attempts,
            "retry_initial_interval": self._retry_policy.initial_interval,
            "retry_max_interval": self._retry_policy.max_interval,
            "retry_multiplier": self._retry_policy.multiplier,
            "retry_jitter": bool(self._retry_policy.jitter),
        }

    @classmethod
    def with_default_policy(
        cls,
        handler_registry: Any,
        max_parallel: int = 1,
    ) -> ToolExecutor:
        """Construct a v2 ToolExecutor with the default retry policy.

        Convenience builder for tests and the v2 ``AgentFactory``.
        """
        return cls(
            handler_registry=handler_registry,
            max_parallel=max_parallel,
            retry_policy=default_tool_retry_policy(),
        )

    # ---- v2 lifecycle helpers ----

    async def aclose(self) -> None:
        """V2 lifecycle hook for clean shutdown.

        The previous ToolExecutor has no explicit close path; resources
        are reclaimed by GC. The v2 contract is more explicit: callers
        should ``await tool_exec.aclose()`` when the owning agent is
        torn down so the confirm-cache is dropped deterministically
        and any background dispatch hooks finish their work.

        Inherits no super().aclose() because the previous class doesn't
        define one; this is a forward-looking hook for the P-RC-7
        rewrite that will own real resources (timers, semaphores).
        """
        try:
            self.clear_confirm_cache()
        except Exception:  # noqa: BLE001
            # Cache eviction is best-effort; never raise during teardown.
            pass

    def reset_runtime_state(self) -> None:
        """Drop transient runtime state, leaving config intact.

        Used by integration tests that share a ToolExecutor across
        cases. Clears the confirm-cache and resets the current-mode
        marker back to ``"agent"``; the retry policy and handler
        registry are deliberately preserved.
        """
        self.clear_confirm_cache()
        self._current_mode = "agent"

    def install_extra_permission_rules(self, rules: list[Any] | None) -> None:
        """Inject profile-specific permission rules (post-construction).

        The v1 ``AgentFactory`` set ``_extra_permission_rules``
        directly. The v2 contract is to call this setter so the
        ``ReasoningEngine`` can introspect the dependency without
        touching private attributes.
        """
        self._extra_permission_rules = rules

    @property
    def max_parallel(self) -> int:
        """Concurrency cap configured at construction time."""
        return self._max_parallel
