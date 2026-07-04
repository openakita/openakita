"""Retry policy with retriable error taxonomy.

The legacy runtime treats all errors uniformly: any exception inside a
node's tool call escalates to the supervisor, which may then trigger a
re-delegate. That is too aggressive for transient network blips and
too lenient for terminal contract violations. ADR-0004 calls for a
LangGraph-style retry policy that classifies errors into:

* **retriable** — transient transport faults, server-side rate limits,
  short-lived provider hiccups; retry with exponential backoff and
  jitter;
* **non-retriable** — semantic errors (bad arguments, contract
  violations, permission denials) and unexpected programmer errors;
  re-raise immediately.

This module is leaf-level: imports only ``asyncio``, ``random``,
``logging``, and the standard typing surface, plus
:class:`CancellationToken` for cooperative abort. Every retry attempt
honours the cancel token so a stuck retry loop cannot ignore a user
``/stop``.

The taxonomy is conservative on purpose. We list only error classes we
have observed as transient in the real provider mix (anthropic, openai,
DashScope, MCP). Adding a class to ``RETRIABLE_EXCEPTION_NAMES`` is a
deliberate review decision; we match by class name rather than by
import to avoid pulling third-party packages into a leaf module.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import TypeVar

from .cancel_token import CancellationToken, CancelledByToken

__all__ = [
    "RetryPolicy",
    "RetryGaveUp",
    "RetryAttempt",
    "is_retriable_exception",
    "is_retriable_tool_error",
    "default_tool_retry_policy",
    "TOOL_NON_RETRIABLE_NAMES",
]

logger = logging.getLogger(__name__)

T = TypeVar("T")


#: Conservative, name-based allow-list of error classes we treat as
#: retriable. Match on either bare class name or fully qualified name.
RETRIABLE_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        # built-in / stdlib
        "TimeoutError",
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        # httpx / requests transport faults
        "httpx.TimeoutException",
        "httpx.ConnectError",
        "httpx.ReadError",
        "httpx.RemoteProtocolError",
        "httpx.PoolTimeout",
        "httpx.WriteError",
        "requests.ConnectionError",
        "requests.Timeout",
        # Anthropic / OpenAI canonical transient classes
        "anthropic.APITimeoutError",
        "anthropic.APIConnectionError",
        "anthropic.RateLimitError",
        "anthropic.InternalServerError",
        "openai.APITimeoutError",
        "openai.APIConnectionError",
        "openai.RateLimitError",
        "openai.InternalServerError",
        # MCP transient transport
        "mcp.shared.exceptions.McpError",
    }
)


def _qualname(exc: BaseException) -> str:
    cls = exc.__class__
    return f"{cls.__module__}.{cls.__qualname__}"


def is_retriable_exception(exc: BaseException) -> bool:
    """Return ``True`` when ``exc`` should trigger another attempt.

    Matches both the bare class name and the fully-qualified name so a
    plugin re-exporting an exception under a different module path
    (e.g. ``openai._exceptions.APITimeoutError`` vs
    ``openai.APITimeoutError``) is still recognised. Cancellation
    exceptions are *never* retriable: once cancelled, retry is a bug.
    """
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, CancelledByToken):
        return False
    cls = exc.__class__
    if cls.__name__ in RETRIABLE_EXCEPTION_NAMES:
        return True
    return _qualname(exc) in RETRIABLE_EXCEPTION_NAMES


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Tool-call retry predicate (P-RC-4 P4.9)
# ---------------------------------------------------------------------------

#: Tool handlers raise their own taxonomy. These names are NEVER
#: retriable because they signal a contract violation, not a transient
#: failure. Matching is by class name (with optional fully-qualified
#: name) to avoid pulling tool packages into this leaf module.
TOOL_NON_RETRIABLE_NAMES: frozenset[str] = frozenset(
    {
        "ToolSkipped",
        "ToolConfigError",
        "ToolPermissionDenied",
        "ToolArgumentError",
        "ToolNotFoundError",
        # PermissionError from filesystem tools is always semantic.
        "PermissionError",
        "FileNotFoundError",
        "NotADirectoryError",
        "IsADirectoryError",
    }
)


def is_retriable_tool_error(exc: BaseException) -> bool:
    """Tool-call variant of :func:`is_retriable_exception`.

    Differs from the LLM-side predicate in one direction: tool-domain
    exceptions (``ToolSkipped``, ``ToolConfigError``, permission /
    not-found errors from filesystem tools) are NEVER retriable
    because they signal contract violations, not transient faults.
    Everything else delegates to the general predicate.

    Refs: continuation plan section 5 (P-RC-4, P4.9). The legacy
    ``core.tool_executor`` decided "retry vs. fail" ad-hoc inside
    ``execute_tool_with_policy``; lifting the decision here means
    the v2 ``agent.tools`` rewrite (P4.10) can compose
    ``RetryPolicy(retry_predicate=is_retriable_tool_error)`` instead
    of carrying its own ladder.
    """
    if isinstance(exc, (asyncio.CancelledError, CancelledByToken)):
        return False
    cls = exc.__class__
    if cls.__name__ in TOOL_NON_RETRIABLE_NAMES:
        return False
    if _qualname(exc) in TOOL_NON_RETRIABLE_NAMES:
        return False
    return is_retriable_exception(exc)


def default_tool_retry_policy() -> RetryPolicy:
    """Return the v2 default ``RetryPolicy`` for tool calls.

    Conservative defaults: 3 attempts, 100ms initial interval, 5s
    ceiling, full jitter. Callers wanting a different shape should
    construct their own ``RetryPolicy`` directly.
    """
    return RetryPolicy(
        max_attempts=3,
        initial_interval=0.1,
        max_interval=5.0,
        jitter=True,
    )


# ---------------------------------------------------------------------------
# Policy + helpers
# ---------------------------------------------------------------------------


class RetryGaveUp(Exception):
    """Raised after the final attempt still failed.

    Carries the most recent underlying exception in ``__cause__`` so
    callers can introspect the actual error class (e.g. to map to a
    user-friendly message in the UI).
    """

    def __init__(self, attempts: int, last_exc: BaseException) -> None:
        super().__init__(
            f"retry exhausted after {attempts} attempts: "
            f"{type(last_exc).__name__}: {last_exc}"
        )
        self.attempts = attempts
        self.__cause__ = last_exc


@dataclass(frozen=True)
class RetryAttempt:
    """Per-attempt record exposed to ``on_attempt`` callbacks."""

    attempt: int  # 1-indexed
    delay_before: float  # seconds (0 for first attempt)
    error: BaseException | None  # error that caused this retry, if any


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff retry policy with jitter and a cancel token.

    Defaults are tuned for LLM-API call sites:
    * three attempts total (one initial + two retries);
    * 0.5 s initial backoff, doubling on each retry (cap 8 s);
    * full-jitter (multiplied by ``random()``) so concurrent retries
      do not synchronise.
    """

    max_attempts: int = 3
    initial_interval: float = 0.5
    multiplier: float = 2.0
    max_interval: float = 8.0
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_interval < 0 or self.max_interval < 0:
            raise ValueError("intervals must be non-negative")
        if self.multiplier <= 0:
            raise ValueError("multiplier must be > 0")

    def compute_delay(self, attempt: int) -> float:
        """Return the seconds to sleep before ``attempt`` (1-indexed)."""
        if attempt <= 1:
            return 0.0
        raw = min(
            self.initial_interval * (self.multiplier ** (attempt - 2)),
            self.max_interval,
        )
        if not self.jitter:
            return raw
        # Full jitter: uniform in [0, raw]. Mirrors AWS Architecture
        # Blog "Exponential Backoff and Jitter" recommendation.
        return raw * random.random()

    async def run(
        self,
        op: Callable[[], Awaitable[T]],
        *,
        cancel_token: CancellationToken | None = None,
        on_attempt: Callable[[RetryAttempt], None] | None = None,
        retry_predicate: Callable[[BaseException], bool] = is_retriable_exception,
    ) -> T:
        """Invoke ``op`` with the configured retry policy.

        Args:
            op: zero-arg coroutine factory; called fresh on each attempt
                (so ``op`` can carry mutable state like a request id).
            cancel_token: optional cooperative cancel; checked before
                every attempt and during sleeps.
            on_attempt: optional callback invoked with ``RetryAttempt``
                metadata before each attempt, useful for telemetry.
            retry_predicate: classify an exception as retriable. Defaults
                to the curated taxonomy in this module.

        Raises:
            CancelledByToken: when ``cancel_token`` cancels.
            RetryGaveUp: when ``max_attempts`` are exhausted, wrapping
                the most recent exception as ``__cause__``.
            BaseException: re-raised as-is when the exception is
                non-retriable.
        """
        last_error: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()

            delay = self.compute_delay(attempt)
            if delay > 0:
                await self._sleep(delay, cancel_token)

            if on_attempt is not None:
                on_attempt(RetryAttempt(attempt, delay, last_error))

            try:
                return await op()
            except (asyncio.CancelledError, CancelledByToken):
                raise
            except BaseException as exc:  # noqa: BLE001
                if not retry_predicate(exc):
                    raise
                last_error = exc
                logger.debug(
                    "RetryPolicy: attempt %d failed with %s; will retry "
                    "(remaining=%d)",
                    attempt,
                    type(exc).__name__,
                    self.max_attempts - attempt,
                )
                if attempt == self.max_attempts:
                    raise RetryGaveUp(attempt, exc) from exc
        # Unreachable; the loop either returns, raises, or raises
        # RetryGaveUp on the last attempt.
        raise RuntimeError("RetryPolicy.run reached an unreachable branch")

    async def _sleep(
        self, delay: float, cancel_token: CancellationToken | None
    ) -> None:
        """Sleep for ``delay`` seconds, honouring cooperative cancel."""
        if cancel_token is None:
            await asyncio.sleep(delay)
            return
        end = monotonic() + delay
        while True:
            cancel_token.raise_if_cancelled()
            remaining = end - monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.1))
