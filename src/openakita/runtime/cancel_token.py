"""Cooperative cancellation primitive for the runtime.

Raising :class:`asyncio.CancelledError` through ``asyncio.wait_for`` is
*coercive*: a plugin in the middle of a long DashScope upload has no chance to
save mid-task state, acknowledge the
cancel, or write a final checkpoint. Every cancel therefore looked to
the parent producer node like a hard failure, which then triggered the
duplicate-delegate cascade described by ADR-0001 and ADR-0004.

``CancellationToken`` is a *cooperative* primitive modelled on AutoGen's
``CancellationToken`` (autogen-core). A consumer
explicitly checks :meth:`is_cancelled` at safe points; producers attach
callbacks via :meth:`add_callback` to be notified the moment cancel
happens (so they can flip a flag, close a network handle, write a final
event, etc.). Both check and callback semantics are required because
some integrations only support one or the other.

This module is intentionally small (target <=150 lines per ADR-0002 and
fits well under that budget). It has no internal imports outside of the
standard library.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

__all__ = [
    "CancellationToken",
    "CancelledByToken",
]


class CancelledByToken(Exception):
    """Raised when a token-aware operation observes cancellation.

    Distinct from :class:`asyncio.CancelledError` so callers can decide
    whether a cancel was *cooperative* (this exception) or *coercive*
    (the asyncio one). Cooperative cancels write a final checkpoint
    before propagating; coercive cancels do not.
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason or "operation cancelled by token")
        self.reason = reason


class CancellationToken:
    """A thread-safe, cooperative cancellation flag with callbacks.

    Usage:
        token = CancellationToken()
        token.add_callback(lambda: log.info("user requested stop"))

        async def long_work() -> None:
            for chunk in stream:
                token.raise_if_cancelled()  # cooperative check
                await process(chunk)

        # elsewhere
        token.cancel("user pressed /stop")

    Multiple callbacks are supported; they fire in registration order.
    Callbacks added after :meth:`cancel` is called fire immediately
    (still in registration order, with the late ones running after the
    eager ones). Exceptions raised inside a callback are swallowed and
    appended to :attr:`callback_errors`; this is deliberate so a buggy
    callback cannot block a cancel from propagating.
    """

    __slots__ = ("_cancelled", "_reason", "_lock", "_callbacks", "callback_errors")

    def __init__(self) -> None:
        self._cancelled: bool = False
        self._reason: str = ""
        self._lock = threading.RLock()
        self._callbacks: list[Callable[[], None]] = []
        self.callback_errors: list[BaseException] = []

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def raise_if_cancelled(self) -> None:
        """Raise :class:`CancelledByToken` if the token has been cancelled.

        Use at every safe checkpoint inside a long operation. Cheap
        (single lock acquisition + branch).
        """
        if self.is_cancelled():
            raise CancelledByToken(self.reason)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def cancel(self, reason: str = "") -> bool:
        """Mark the token cancelled and fire registered callbacks.

        Idempotent: a second call is a no-op. Returns ``True`` on the
        first transition, ``False`` thereafter.
        """
        with self._lock:
            if self._cancelled:
                return False
            self._cancelled = True
            self._reason = reason or self._reason
            callbacks = list(self._callbacks)
        # Run callbacks outside the lock; we never want a callback to
        # deadlock against ``add_callback`` from another thread.
        for cb in callbacks:
            self._run_callback(cb)
        return True

    def add_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback. Fires immediately if already cancelled."""
        with self._lock:
            if not self._cancelled:
                self._callbacks.append(callback)
                return
        # already cancelled — fire eagerly outside the lock
        self._run_callback(callback)

    def link_future(self, fut: asyncio.Future[Any]) -> asyncio.Future[Any]:
        """Bind an asyncio Future to this token.

        When the token cancels, the future is cancelled. Useful for
        wrapping ``asyncio.wait_for`` semantics without losing the
        cooperative-cancel marker. Returns the future for chaining.
        """

        def _cancel_future() -> None:
            if not fut.done():
                fut.cancel()

        self.add_callback(_cancel_future)
        return fut

    def link_task(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        """Cancel the given asyncio task when this token cancels."""

        def _cancel_task() -> None:
            if not task.done():
                task.cancel()

        self.add_callback(_cancel_task)
        return task

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_callback(self, cb: Callable[[], None]) -> None:
        try:
            cb()
        except BaseException as exc:  # noqa: BLE001 — see docstring
            self.callback_errors.append(exc)

    # ------------------------------------------------------------------
    # Convenience helpers used by higher layers
    # ------------------------------------------------------------------

    async def wait_cancelled(self, poll_interval: float = 0.05) -> None:
        """Block until the token cancels.

        Useful in tests and in supervisor "monitor" tasks. The default
        50 ms poll keeps overhead low while still cancelling promptly.
        """
        while not self.is_cancelled():
            await asyncio.sleep(poll_interval)

    @staticmethod
    async def race(
        operation: Coroutine[Any, Any, Any],
        token: CancellationToken,
        *,
        on_cancel: Callable[[], None] | None = None,
    ) -> Any:
        """Run ``operation`` until it completes or ``token`` cancels.

        On cancel:
          * raises :class:`CancelledByToken`,
          * cancels the underlying task,
          * invokes ``on_cancel`` if provided (after the task is
            cancelled), so the caller can flush a final checkpoint.
        """
        task = asyncio.ensure_future(operation)
        token.link_task(task)
        try:
            return await task
        except asyncio.CancelledError:
            if token.is_cancelled():
                if on_cancel is not None:
                    on_cancel()
                raise CancelledByToken(token.reason) from None
            raise
