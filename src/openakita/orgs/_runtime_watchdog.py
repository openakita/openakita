"""``_runtime_watchdog.py`` -- v2 OrgRuntime idle-probe sibling.

Sprint-9 (Supervisor HTTP takeover) deleted the ``CommandWatchdog``
class that lived in this module from P9.6c through Sprint-8.
``CommandWatchdog`` was dead code as of Sprint-5: ``OrgRuntime``
declared ``self._watchdog_tasks = {}`` and never instantiated the
watchdog; the only stuck-task killer that was actually running was
``OrgCommandService._watchdog_loop`` (also deleted in Sprint-9).
With the supervisor's :class:`~openakita.runtime.stall_detector.StallDetector`
+ hard ``max_turns`` cap now driving stall detection on
LLM-evaluated :class:`~openakita.runtime.ledger.ProgressLedger`
signals, the wall-clock watchdog is no longer needed.

What stays here:

* :class:`IdleProbeLoop` -- per-org idle nudge (parity with
  v1 ``_idle_probe_loop`` at ``orgs/runtime.py:5075-5217``,
  143 LOC). Polls all nodes for an org at the configured
  cadence; when a node has been idle past the threshold the
  loop calls the injected ``nudge_callback`` so the
  dispatch sibling can decide whether to prompt the node.
  This is orthogonal to stall detection -- the supervisor
  decides whether *a running command* is stuck; the idle
  probe decides whether *an inactive node* needs a nudge to
  pick up its mailbox.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import time

_LOGGER = logging.getLogger(__name__)


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
    "IdleProbeLoop",
]
