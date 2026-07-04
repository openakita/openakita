"""v2 OrgNodeScheduler (P-RC-9 P9.3).

Replaces v1 ``openakita.orgs.node_scheduler.OrgNodeScheduler``
(215 LOC, 10 methods, OrgRuntime-coupled) with a
:class:`typing.Protocol`-typed surface decoupled from the
runtime via three injected Protocols:

* :class:`CommandDispatcher` -- the **cross-subsystem boundary**
  per ADR-0011. P9.4 ``OrgCommandService`` implements it
  without circular deps.
* :class:`ScheduleStore` -- per-node schedule list persistence
  (v1 delegates to ``OrgManager``; v2 will inject the v2
  ``OrgManager`` once it lands at P9.5).
* :class:`SchedulerRuntimeProbe` -- liveness gating
  (``is_node_runnable``) + lifecycle event emission. Replaces
  v1''s direct ``runtime.get_org`` + ``runtime.get_event_store``
  reach-ins.

Public API is 1:1 with v1 (``start_for_org`` /
``stop_for_org`` / ``stop_all`` / ``reload_node_schedules`` /
``trigger_once``) so P9.8 caller migration is one import-line
change. The single signature drift: ``start_for_org`` takes
``(org_id, node_ids)`` rather than an ``Organization``
instance so the v1 ``Organization`` model is not part of the
v2 Protocol surface (callers pass ``[n.id for n in org.nodes]``
at the call site).

Commit split (Nit-4 fold-in from G-RC-9.2: pre-split if
projected > 350 LOC):

* P9.3a0 -- ``scheduler_models.py`` ships ``NodeSchedule`` /
  ``ScheduleType``.
* P9.3a -- the four Protocols + ``compute_next_fire_time``
  pure helper + :class:`OrgNodeScheduler` skeleton (methods
  raise ``NotImplementedError``).
* P9.3b (this commit) -- :class:`OrgNodeScheduler` body --
  ``_schedule_loop`` + ``_execute_schedule`` + the seven
  lifecycle methods.
* P9.3c -- 4 parity fixtures (xfail -> pass).
* P9.3d -- 12 contract cases (single in-memory backend).
* G-RC-9.3 -- mini-gate doc + ledger close.

ADR refs: ADR-0011 (Protocol-typed subsystem decomposition),
ADR-0012 (orgs/ deletion strategy -- no shim under v1),
ADR-0013 (wall-clock SLA: ``compute_next_fire_time`` is a pure
function so the parity 1-ms safety net asserts
deterministically; the smart-frequency back-off bounded by
``MAX_FREQUENCY_FACTOR * base_interval`` is the wall-clock
ceiling).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from .scheduler_models import NodeSchedule, ScheduleType, now_iso

__all__ = [
    "CLEAN_THRESHOLD",
    "FREQUENCY_MULTIPLIER",
    "MAX_FREQUENCY_FACTOR",
    "RECHECK_DELAY",
    "CommandDispatcher",
    "NodeSchedulerProtocol",
    "OrgNodeScheduler",
    "ScheduleStore",
    "SchedulerRuntimeProbe",
    "build_schedule_prompt",
    "compute_next_fire_time",
]

logger = logging.getLogger(__name__)

# v1 ``OrgNodeScheduler`` module constants, lifted verbatim so the
# smart-frequency back-off behaves identically. Re-exported in
# ``__all__`` for downstream callers that introspect the thresholds
# (the v1 test suite reads them directly).
CLEAN_THRESHOLD = 5
FREQUENCY_MULTIPLIER = 1.5
MAX_FREQUENCY_FACTOR = 4.0
RECHECK_DELAY = 300


# ---------------------------------------------------------------------------
# Injected Protocols (ADR-0011 cross-subsystem boundary)
# ---------------------------------------------------------------------------


@runtime_checkable
class CommandDispatcher(Protocol):
    """The cross-subsystem boundary per ADR-0011.

    ``OrgNodeScheduler`` dispatches scheduled commands through
    this Protocol so P9.4 ``OrgCommandService`` can implement
    it without circular imports. The signature mirrors v1
    ``OrgRuntime.send_command`` byte-for-byte (positional
    ``org_id`` / ``node_id`` / ``prompt``; returns the v1 result
    dict containing at least ``result``).
    """

    async def dispatch(self, org_id: str, node_id: str, prompt: str) -> dict[str, Any]: ...


@runtime_checkable
class ScheduleStore(Protocol):
    """Per-node schedule list persistence.

    v1 delegates to ``OrgManager.get_node_schedules`` /
    ``save_node_schedules``; v2 injects the same shape. The
    store is the single source of truth for schedule state --
    the scheduler holds ``asyncio.Task`` handles in memory but
    every mutation (``last_run_at``, ``consecutive_clean``,
    etc.) is read-modify-write through this Protocol.

    Cross-process safety: the underlying store must provide its
    own cross-process correctness if shared across processes
    (Nit-3 fold-in from G-RC-9.2 -- JSON backends are
    single-process; SQLite WAL + ``BEGIN IMMEDIATE`` is the
    cross-process option).
    """

    def get_node_schedules(self, org_id: str, node_id: str) -> list[NodeSchedule]: ...

    def save_node_schedules(
        self, org_id: str, node_id: str, schedules: list[NodeSchedule]
    ) -> None: ...


@runtime_checkable
class SchedulerRuntimeProbe(Protocol):
    """Liveness gating + lifecycle event emission.

    The scheduler loop queries :meth:`is_node_runnable` before
    every dispatch to decide whether a tick should run (v1
    checks ``OrgStatus.ACTIVE/RUNNING`` and not
    ``NodeStatus.FROZEN/OFFLINE``) and calls
    :meth:`emit_event` to record ``schedule_triggered`` /
    ``schedule_completed`` to the per-org event store.
    """

    def is_node_runnable(self, org_id: str, node_id: str) -> bool: ...

    def emit_event(
        self,
        org_id: str,
        event_type: str,
        node_id: str,
        payload: dict[str, Any],
    ) -> None: ...


@runtime_checkable
class NodeSchedulerProtocol(Protocol):
    """Public surface of the v2 OrgNodeScheduler (ADR-0011).

    Mirrors v1 ``openakita.orgs.node_scheduler.OrgNodeScheduler``
    1:1 modulo the single ``start_for_org`` signature drift
    documented at the module level.
    """

    async def start_for_org(self, org_id: str, node_ids: Iterable[str]) -> None: ...

    async def stop_for_org(self, org_id: str) -> None: ...

    async def stop_all(self) -> None: ...

    async def reload_node_schedules(self, org_id: str, node_id: str) -> None: ...

    async def trigger_once(self, org_id: str, node_id: str, schedule_id: str) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Pure helpers (parity gates per P-RC-9-PLAN section 5.2)
# ---------------------------------------------------------------------------


def compute_next_fire_time(sched: NodeSchedule, now: datetime) -> datetime:
    """Pure helper: when is this schedule''s next fire?

    Parity-faithful to v1 ``OrgNodeScheduler._schedule_loop``:

    * ``ONCE`` -- returns the parsed ``run_at`` ISO timestamp
      (UTC-coerced if naive). v1 reads ``sched.run_at`` via
      :py:func:`datetime.fromisoformat` and then sleeps until
      that target; if ``run_at`` is missing v1 falls through to
      immediate execution so v2 returns ``now`` for parity.
    * ``INTERVAL`` or ``CRON`` -- returns ``now +
      sched.interval_s`` (default 3600 seconds if
      ``interval_s`` is ``None`` / ``<= 0``). v1 declares
      ``ScheduleType.CRON`` in the enum but the dispatch loop
      has no cron branch -- any non-``ONCE`` schedule falls
      through to interval timing using
      ``current_interval = sched.interval_s or 3600``. v2
      preserves this byte-for-byte so the P-RC-9-PLAN section
      5.2 1-ms parity assertion holds without croniter (which
      v1 never imported despite the docstring claim).
    """
    if sched.schedule_type == ScheduleType.ONCE:
        if not sched.run_at:
            return now
        target = datetime.fromisoformat(sched.run_at)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        return target
    interval = sched.interval_s if sched.interval_s and sched.interval_s > 0 else 3600
    return now + timedelta(seconds=interval)


def build_schedule_prompt(sched: NodeSchedule) -> str:
    """Pure helper: build the v1-faithful scheduled-task prompt.

    Parity-faithful to v1 ``OrgNodeScheduler._execute_schedule``
    byte-for-byte:

    * Header: ``[\u5b9a\u65f6\u4efb\u52a1] {name}\\n\u65f6\u95f4: {now_iso}\\n\u6307\u4ee4: {prompt}\\n\\n\u8bf7\u6267\u884c\u4e0a\u8ff0\u4efb\u52a1\u3002``
    * If ``report_condition == \"on_issue\"``: appends two
      lines describing the conditional report rule.
    * Else if ``report_condition == \"always\"`` and
      ``report_to`` is set: appends an unconditional report
      directive.

    Pure function so the P9.3c parity test can compare prompt
    structure modulo the ``\u65f6\u95f4: {now_iso}`` line
    (which differs between v1 and v2 invocation; the parity
    runner strips that line before comparison).
    """
    header = (
        f"[\u5b9a\u65f6\u4efb\u52a1] {sched.name}\n"
        f"\u65f6\u95f4: {now_iso()}\n"
        f"\u6307\u4ee4: {sched.prompt}\n\n"
        f"\u8bf7\u6267\u884c\u4e0a\u8ff0\u4efb\u52a1\u3002"
    )
    if sched.report_condition == "on_issue":
        report_to = sched.report_to or "\u4e0a\u7ea7"
        header += (
            f"\n\n\u6c47\u62a5\u89c4\u5219\uff1a\u4ec5\u5728"
            f"\u53d1\u73b0\u5f02\u5e38/\u95ee\u9898\u65f6"
            f"\u5411 {report_to} \u6c47\u62a5\u3002"
            f"\u5982\u679c\u4e00\u5207\u6b63\u5e38\uff0c"
            f"\u7b80\u8981\u8bb0\u5f55\u5230\u4f60\u7684"
            f"\u79c1\u6709\u8bb0\u5fc6\u5373\u53ef\u3002"
        )
    elif sched.report_condition == "always" and sched.report_to:
        header += (
            f"\n\n\u6267\u884c\u5b8c\u6bd5\u540e\u8bf7"
            f"\u5411 {sched.report_to} \u6c47\u62a5\u7ed3\u679c\u3002"
        )
    return header


# ---------------------------------------------------------------------------
# OrgNodeScheduler implementation (P9.3b)
# ---------------------------------------------------------------------------


class OrgNodeScheduler:
    """Manages per-node scheduled tasks across active organisations.

    Construction takes three injected Protocols (``dispatcher``,
    ``store``, ``probe``) rather than the v1 ``runtime``
    reach-in so the scheduler is testable without spinning up
    an :class:`openakita.orgs.runtime.OrgRuntime`.

    Concurrency model: pure asyncio. Schedule task handles live
    in ``self._tasks`` keyed by ``f\"{org_id}:{node_id}:{sched_id}\"``;
    public mutators take ``self._task_lock`` (an
    :class:`asyncio.Lock`) for the cancel-then-replace window so
    concurrent ``trigger_once`` / ``reload_node_schedules``
    calls cannot race. v1 had no such lock; the contract suite
    ``test_concurrent_reload_no_loss`` (P9.3d) is the Nit-2
    fold-in stress (4 worker coroutines x 25 reload cycles =
    100 concurrent reloads).
    """

    def __init__(
        self,
        dispatcher: CommandDispatcher,
        store: ScheduleStore,
        probe: SchedulerRuntimeProbe,
    ) -> None:
        self._dispatcher = dispatcher
        self._store = store
        self._probe = probe
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._task_lock = asyncio.Lock()

    async def start_for_org(self, org_id: str, node_ids: Iterable[str]) -> None:
        """Start schedule loops for every enabled schedule on every node."""
        async with self._task_lock:
            for node_id in node_ids:
                for sched in self._store.get_node_schedules(org_id, node_id):
                    if sched.enabled:
                        self._start_schedule_locked(org_id, node_id, sched)

    async def stop_for_org(self, org_id: str) -> None:
        """Cancel every schedule loop owned by the given org."""
        prefix = f"{org_id}:"
        async with self._task_lock:
            keys = [k for k in self._tasks if k.startswith(prefix)]
            tasks = [self._tasks.pop(k) for k in keys]
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def stop_all(self) -> None:
        """Cancel every schedule loop across every org."""
        async with self._task_lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def reload_node_schedules(self, org_id: str, node_id: str) -> None:
        """Cancel + restart loops for a node after schedule CRUD."""
        prefix = f"{org_id}:{node_id}:"
        async with self._task_lock:
            old_keys = [k for k in self._tasks if k.startswith(prefix)]
            old_tasks = [self._tasks.pop(k) for k in old_keys]
            schedules = self._store.get_node_schedules(org_id, node_id)
            for sched in schedules:
                if sched.enabled:
                    self._start_schedule_locked(org_id, node_id, sched)
        for task in old_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def trigger_once(self, org_id: str, node_id: str, schedule_id: str) -> dict[str, Any]:
        """Manually trigger a schedule execution by id."""
        schedules = self._store.get_node_schedules(org_id, node_id)
        sched = next((s for s in schedules if s.id == schedule_id), None)
        if sched is None:
            return {"error": "Schedule not found"}
        return await self._execute_schedule(org_id, node_id, sched)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_schedule_locked(self, org_id: str, node_id: str, sched: NodeSchedule) -> None:
        """Create the asyncio task; caller must hold ``self._task_lock``."""
        key = f"{org_id}:{node_id}:{sched.id}"
        if key in self._tasks:
            return
        task = asyncio.create_task(self._schedule_loop(org_id, node_id, sched))
        self._tasks[key] = task

    async def _schedule_loop(self, org_id: str, node_id: str, sched: NodeSchedule) -> None:
        """Main loop for a single scheduled task (parity-faithful to v1)."""
        base_interval = sched.interval_s if sched.interval_s and sched.interval_s > 0 else 3600
        current_interval: float = float(base_interval)

        while True:
            try:
                if sched.schedule_type == ScheduleType.ONCE:
                    if sched.run_at:
                        target = datetime.fromisoformat(sched.run_at)
                        if target.tzinfo is None:
                            target = target.replace(tzinfo=UTC)
                        wait = (target - datetime.now(UTC)).total_seconds()
                        if wait > 0:
                            await asyncio.sleep(wait)
                    await self._execute_schedule(org_id, node_id, sched)
                    return

                await asyncio.sleep(current_interval)

                if not self._probe.is_node_runnable(org_id, node_id):
                    continue

                result = await self._execute_schedule(org_id, node_id, sched)

                result_str = str(result).lower()
                has_issue = (
                    "\u5f02\u5e38" in str(result)
                    or "\u9519\u8bef" in str(result)
                    or "error" in result_str
                )

                if has_issue:
                    sched.consecutive_clean = 0
                    current_interval = float(base_interval)
                    self._save_schedule(org_id, node_id, sched)
                    await asyncio.sleep(RECHECK_DELAY)
                    await self._execute_schedule(org_id, node_id, sched)
                else:
                    sched.consecutive_clean += 1
                    if sched.consecutive_clean >= CLEAN_THRESHOLD:
                        new_interval = min(
                            current_interval * FREQUENCY_MULTIPLIER,
                            base_interval * MAX_FREQUENCY_FACTOR,
                        )
                        if new_interval != current_interval:
                            logger.info(
                                "[Scheduler] %s/%s: down-shift %ds -> %ds (consecutive clean=%d)",
                                node_id,
                                sched.name,
                                int(current_interval),
                                int(new_interval),
                                sched.consecutive_clean,
                            )
                            current_interval = new_interval
                    self._save_schedule(org_id, node_id, sched)

            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.error("[Scheduler] error in %s/%s: %s", node_id, sched.name, exc)
                await asyncio.sleep(60)

    async def _execute_schedule(
        self, org_id: str, node_id: str, sched: NodeSchedule
    ) -> dict[str, Any]:
        """Execute one scheduled task via the injected dispatcher."""
        self._probe.emit_event(
            org_id,
            "schedule_triggered",
            node_id,
            {"schedule_id": sched.id, "name": sched.name},
        )

        prompt = build_schedule_prompt(sched)
        result = await self._dispatcher.dispatch(org_id, node_id, prompt)

        sched.last_run_at = now_iso()
        result_text = result.get("result", "") if isinstance(result, dict) else ""
        sched.last_result_summary = (
            result_text[:200] if isinstance(result_text, str) and result_text else None
        )
        self._save_schedule(org_id, node_id, sched)

        self._probe.emit_event(
            org_id,
            "schedule_completed",
            node_id,
            {
                "schedule_id": sched.id,
                "result_preview": (
                    result_text[:100] if isinstance(result_text, str) and result_text else ""
                ),
            },
        )
        return result if isinstance(result, dict) else {"result": result}

    def _save_schedule(self, org_id: str, node_id: str, sched: NodeSchedule) -> None:
        """Persist schedule state changes through the injected store."""
        schedules = self._store.get_node_schedules(org_id, node_id)
        for i, s in enumerate(schedules):
            if s.id == sched.id:
                schedules[i] = sched
                break
        self._store.save_node_schedules(org_id, node_id, schedules)
