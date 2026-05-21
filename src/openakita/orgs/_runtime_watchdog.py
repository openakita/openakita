"""``_runtime_watchdog.py`` -- v2 OrgRuntime watchdog sibling (P9.6c).

Owns the two background-loop responsibilities lifted out of
v1 ``OrgRuntime``:

* :class:`CommandWatchdog` -- stuck-detection for a single
  user command (parity with v1 ``_command_watchdog`` at
  ``orgs/runtime.py:4554-4728``, 175 LOC). Polls a tracker
  snapshot at a configurable interval; flips the tracker to
  ``deadlock-stopped`` when the quiet-deadlock signal trips.
* :class:`IdleProbeLoop` -- per-org idle nudge (parity with
  v1 ``_idle_probe_loop`` at ``orgs/runtime.py:5075-5217``,
  143 LOC). Polls all nodes for an org at the configured
  cadence; when a node has been idle past the threshold the
  loop calls the injected ``nudge_callback`` so the
  dispatch sibling can decide whether to prompt the node.

Both classes are pure asyncio + dependency-injection: the
heavy logic (tracker introspection, node-message dispatch)
is delegated to callbacks supplied by the dispatch /
node-lifecycle siblings (P9.6beta). This commit lands the
loop scaffolding + start / stop semantics so the lifecycle
sibling (P9.6d) can wire ``OrgRuntime`` to start / stop the
loops on ``start_org`` / ``stop_org``.

The v1 wall-clock SLA tests (P9.4e ADR-0013) stay green:
this module does not touch the cancel pipeline that those
tests pin; the watchdog only adds best-effort recovery on
top of an already-cancelled tracker.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import time
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)


class _TrackerSnapshotProtocol(Protocol):
    """Minimal tracker view this watchdog needs.

    The full tracker class lives in v1 ``orgs/command_tracker.py``
    and will move to the v2 ``_runtime_dispatch.py`` (P9.6beta).
    Defining a tiny Protocol here keeps the watchdog testable
    without importing the full tracker.
    """

    org_id: str
    command_id: str
    last_activity_at: float
    state: str


_AsyncCallable = Callable[..., Awaitable[Any]]


class CommandWatchdog:
    """Background loop watching one tracker for quiet deadlock.

    Constructor args:

    * ``tracker`` -- a :class:`_TrackerSnapshotProtocol`-shaped
      object owned by the dispatch sibling.
    * ``quiet_threshold_secs`` -- how long ``tracker.state ==
      "running"`` may go without ``last_activity_at`` ticking
      before the watchdog flips it to ``deadlock-stopped``
      (default 300 s, matches v1 ``OrgRuntime`` defaults).
    * ``poll_interval_secs`` -- how often to re-check
      (default 30 s).
    * ``on_deadlock`` -- async callback the watchdog awaits
      when quiet-deadlock is detected; the dispatch sibling
      uses this hook to tear down the tracker state.
    """

    def __init__(
        self,
        tracker: _TrackerSnapshotProtocol,
        *,
        quiet_threshold_secs: float = 300.0,
        poll_interval_secs: float = 30.0,
        on_deadlock: _AsyncCallable | None = None,
    ) -> None:
        self._tracker = tracker
        self._quiet_threshold = quiet_threshold_secs
        self._poll_interval = poll_interval_secs
        self._on_deadlock = on_deadlock
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Async loop body; safe to await from a task."""

        while not self._stop.is_set():
            try:
                if self._tracker.state != "running":
                    return
                quiet_for = time() - self._tracker.last_activity_at
                if quiet_for >= self._quiet_threshold:
                    _LOGGER.warning(
                        "quiet-deadlock detected: org=%s command=%s quiet=%.1fs",
                        self._tracker.org_id,
                        self._tracker.command_id,
                        quiet_for,
                    )
                    if self._on_deadlock is not None:
                        await self._on_deadlock(self._tracker, quiet_for)
                    return
            except Exception:  # noqa: BLE001 (v1 parity: never crash the loop)
                _LOGGER.exception(
                    "command_watchdog loop iteration raised (org=%s command=%s)",
                    self._tracker.org_id,
                    self._tracker.command_id,
                )
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue

    def start(self) -> asyncio.Task[None]:
        """Spawn the loop as a background task; idempotent."""

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self.run(), name=f"watchdog-{self._tracker.command_id}"
            )
        return self._task

    async def stop(self) -> None:
        """Signal the loop + await graceful shutdown."""

        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()


class IdleProbeLoop:
    """Background per-org idle nudge loop.

    Constructor args:

    * ``org_id`` -- the org this loop watches.
    * ``list_nodes`` -- sync callback returning the list of
      node ids currently registered for the org.
    * ``node_last_active`` -- sync callback ``(org_id, node_id)
      -> float`` returning the last-active timestamp.
    * ``nudge_callback`` -- async callback the loop awaits
      when a node has been idle past ``idle_threshold_secs``;
      the dispatch sibling decides whether to actually prompt
      the node.
    * ``poll_interval_secs`` -- default 60 s.
    * ``idle_threshold_secs`` -- default 600 s.
    """

    def __init__(
        self,
        org_id: str,
        *,
        list_nodes: Callable[[], list[str]],
        node_last_active: Callable[[str, str], float],
        nudge_callback: Callable[[str, str], Awaitable[None]],
        poll_interval_secs: float = 60.0,
        idle_threshold_secs: float = 600.0,
    ) -> None:
        self._org_id = org_id
        self._list_nodes = list_nodes
        self._node_last_active = node_last_active
        self._nudge = nudge_callback
        self._poll_interval = poll_interval_secs
        self._idle_threshold = idle_threshold_secs
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Async loop body."""

        while not self._stop.is_set():
            try:
                now = time()
                for node_id in list(self._list_nodes()):
                    last = self._node_last_active(self._org_id, node_id)
                    if now - last >= self._idle_threshold:
                        await self._nudge(self._org_id, node_id)
            except Exception:  # noqa: BLE001 (v1 parity)
                _LOGGER.exception("idle_probe loop iteration raised (org=%s)", self._org_id)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue

    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name=f"idle-probe-{self._org_id}")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()


__all__ = [
    "CommandWatchdog",
    "IdleProbeLoop",
]
