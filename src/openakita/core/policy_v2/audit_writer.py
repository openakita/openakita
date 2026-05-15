"""C22 P3-2: async batch audit writer.

Plan §13.5.2 A
==============

Plan §13.5.2 A spec'd an asyncio.Queue-based audit writer that decouples
the (latency-sensitive) policy decision path from the (slower) chained
file write:

    Producer (sync, hot path)
       │  enqueue(record)            ← non-blocking, µs-level
       ▼
    asyncio.Queue (bounded)
       │
       ▼
    Background async worker
       │  await queue.get(N records, or up to T ms)
       │  ChainedJsonlWriter.append_batch(records)   ← single filelock acq
       ▼
    On-disk audit JSONL (chain integrity preserved)

Without this, every ``AuditLogger.log()`` call in ``tool_executor`` paid
the filelock acquisition + tail-read cost on the engine hot path
(~1-2 ms per audit row on healthy disk, worse under contention).

Design constraints
==================

1. **Chain integrity is non-negotiable**. We delegate the actual append
   to :meth:`ChainedJsonlWriter.append_batch` which holds the same
   process+file lock semantics. Reordering inside a batch is forbidden
   — the worker drains the queue in FIFO order and passes records to
   ``append_batch`` in that exact order.

2. **At-least-once on graceful shutdown**. ``stop()`` drains the queue
   and awaits the worker. Records still in the queue when the
   interpreter crashes WITHOUT calling ``stop()`` are lost — that's a
   conscious tradeoff: blocking the hot path on sync flushes would
   undo the win. Operators who need at-most-once persistence for every
   row should disable the async writer (``audit.async_batch=false``)
   and pay the per-call cost.

3. **Backpressure → sync fallback**. When the bounded queue is full
   (producer outpacing worker, e.g. disk hiccup), ``enqueue()`` falls
   back to a direct ``ChainedJsonlWriter.append()`` call rather than
   dropping the record. This trades latency for correctness — losing
   audit rows would void the legal/compliance use of the chain.

4. **Thread safety across sync producers**. Producers run on whatever
   thread happens to call ``AuditLogger.log()`` (FastAPI worker
   threads, gateway streaming task, sync CLI invocations). The writer
   uses ``loop.call_soon_threadsafe`` to hand records to the queue
   from any thread.

5. **Lifecycle hooks**. ``start(loop)`` is idempotent; ``stop()`` is
   safe to call multiple times. ``is_running()`` lets callers fall
   back to sync writes when no worker is up (CLI mode, test fixtures).

Out of scope
============

- **Cross-process queue**. Each process has its own queue; the
  ``ChainedJsonlWriter`` filelock still serializes the actual file
  writes, so two processes both running async writers won't corrupt
  the chain — they'll just contend on the filelock as before, just
  with their workers (not their hot paths) waiting.

- **Persistence of in-flight queue across restart**. Out of scope per
  plan §13.5.2 A's tradeoff statement. If you need that, switch to
  ``audit.async_batch=false``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from .audit_chain import ChainedJsonlWriter, get_writer

logger = logging.getLogger(__name__)

# Tunables. Conservative defaults: drain when queue has ≥32 records OR
# 50ms have elapsed since the oldest queued record. On a bursty workload
# this gives meaningful batching; on a quiet workload the latency upper
# bound is ~50ms (acceptable for audit purposes).
DEFAULT_MAX_BATCH_SIZE: int = 64
DEFAULT_MAX_BATCH_DELAY_MS: float = 50.0
DEFAULT_QUEUE_MAXSIZE: int = 4096


class AsyncBatchAuditWriter:
    """Batch + async wrapper around :class:`ChainedJsonlWriter`.

    Typical lifecycle::

        writer = AsyncBatchAuditWriter(path="data/audit/policy_decisions.jsonl")
        await writer.start()              # spawns worker on running loop
        writer.enqueue({"ts": ..., ...})  # sync or async, no await needed
        ...
        await writer.stop()               # graceful drain

    Public surface kept minimal — ``enqueue`` from any thread, plus
    ``start`` / ``stop`` / ``is_running`` / ``flush`` for lifecycle.
    """

    def __init__(
        self,
        path: str,
        *,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        max_batch_delay_ms: float = DEFAULT_MAX_BATCH_DELAY_MS,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
    ) -> None:
        # Normalise the path so callers comparing string forms — notably
        # ``AuditLogger.log`` which does ``str(Path(self._path))`` — see
        # the same value regardless of OS separator. Without this the
        # singleton path lookup fails on Windows because ``Path(...)``
        # stringifies with ``\`` while the caller passed ``/``.
        self._path = str(Path(path))
        self._writer: ChainedJsonlWriter = get_writer(self._path)
        self._max_batch_size = max(1, max_batch_size)
        self._max_batch_delay_s = max_batch_delay_ms / 1000.0
        self._queue_maxsize = max(1, queue_maxsize)

        # Initialised lazily in start() because the queue must be bound
        # to the running loop.
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._state_lock = threading.Lock()
        self._stopped = False

        # Stats — read by tests + the optional ops endpoint.
        self._stat_enqueued = 0
        self._stat_written = 0
        self._stat_sync_fallback = 0
        self._stat_batches = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the background worker on the calling coroutine's loop.

        Idempotent: calling start() twice while the worker is already
        alive is a no-op. After ``stop()`` it's safe to call ``start()``
        again to restart the worker (e.g. test fixtures reusing the
        same writer instance).
        """
        with self._state_lock:
            if self._worker_task is not None and not self._worker_task.done():
                return
            self._loop = asyncio.get_running_loop()
            self._queue = asyncio.Queue(maxsize=self._queue_maxsize)
            self._stopped = False
            self._worker_task = self._loop.create_task(
                self._worker_main(), name=f"audit-writer:{self._path}"
            )
            logger.info(
                "[audit_writer] started worker for %s (batch=%d, delay=%.0fms, maxsize=%d)",
                self._path,
                self._max_batch_size,
                self._max_batch_delay_s * 1000,
                self._queue_maxsize,
            )

    async def stop(self, *, timeout: float = 10.0) -> None:
        """Drain queue + await worker termination.

        Safe to call multiple times. If called while the worker is
        still draining a large queue, the timeout bounds how long we
        wait — beyond it, remaining records may be lost (logged).
        Operators should size timeout for their queue depth and disk
        throughput; default 10s handles up to ~10k pending rows on
        healthy spindle disk.
        """
        with self._state_lock:
            if self._worker_task is None or self._queue is None:
                return
            if self._stopped:
                return
            self._stopped = True
            queue = self._queue
            task = self._worker_task

        # Split the budget: at most half goes to *delivering* the
        # sentinel; the rest waits for the worker to drain + exit.
        # This bounds the worst-case stop() runtime to ``timeout`` even
        # if the queue is full AND the worker is stuck on filelock —
        # otherwise ``await queue.put(None)`` could hang indefinitely
        # while ``wait_for(task, ...)`` never gets a chance to run.
        sentinel_budget = max(min(timeout / 2.0, 5.0), 0.1)
        worker_budget = max(timeout - sentinel_budget, 0.5)

        sentinel_delivered = False
        try:
            queue.put_nowait(None)  # type: ignore[arg-type]
            sentinel_delivered = True
        except asyncio.QueueFull:
            try:
                await asyncio.wait_for(
                    queue.put(None), timeout=sentinel_budget  # type: ignore[arg-type]
                )
                sentinel_delivered = True
            except TimeoutError:
                logger.error(
                    "[audit_writer] could not deliver stop sentinel within "
                    "%.1fs for %s; cancelling worker (queued records will "
                    "be lost)",
                    sentinel_budget,
                    self._path,
                )

        if sentinel_delivered:
            try:
                await asyncio.wait_for(task, timeout=worker_budget)
            except TimeoutError:
                logger.error(
                    "[audit_writer] worker stop timed out after %.1fs for %s; "
                    "%d records may be lost",
                    worker_budget,
                    self._path,
                    queue.qsize(),
                )
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, TimeoutError, Exception):
                    pass
        else:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, TimeoutError, Exception):
                pass

        with self._state_lock:
            self._worker_task = None
            self._queue = None
            self._loop = None
            logger.info(
                "[audit_writer] stopped %s (enqueued=%d, written=%d, "
                "batches=%d, sync_fallback=%d)",
                self._path,
                self._stat_enqueued,
                self._stat_written,
                self._stat_batches,
                self._stat_sync_fallback,
            )

    def is_running(self) -> bool:
        with self._state_lock:
            return (
                self._worker_task is not None
                and not self._worker_task.done()
                and not self._stopped
            )

    # ------------------------------------------------------------------
    # Enqueue (sync + async safe)
    # ------------------------------------------------------------------

    def enqueue(self, record: dict[str, Any]) -> None:
        """Enqueue a record. Safe from any thread.

        Three paths:

        1. **Worker running + queue has capacity**: schedules a
           threadsafe ``put_nowait`` and returns immediately.

        2. **Worker running + queue full**: backpressure → falls back
           to a synchronous ``ChainedJsonlWriter.append`` so the row
           still lands on disk. Logged at WARNING.

        3. **Worker NOT running**: synchronous ``append`` — same as
           pre-C22 behaviour. This is the test/CLI/early-init path.

        Returns immediately in all three cases. Sync fallback is bounded
        by the underlying writer's filelock timeout (5s).
        """
        if not isinstance(record, dict):
            raise TypeError(f"record must be dict, got {type(record).__name__}")

        with self._state_lock:
            running = (
                self._worker_task is not None
                and not self._worker_task.done()
                and not self._stopped
                and self._queue is not None
                and self._loop is not None
            )
            queue = self._queue
            loop = self._loop

        if not running:
            self._do_sync_append(record, reason="worker_not_running")
            return

        assert queue is not None and loop is not None

        # Detect whether the caller is currently executing on the
        # worker's loop thread. ``asyncio.get_running_loop()`` raises
        # RuntimeError when called from a thread that isn't running an
        # event loop, which is exactly how we tell the threads apart.
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is loop:
            # Same thread as the worker → asyncio.Queue.put_nowait is
            # safe to call directly. This is the hot path for code that
            # already runs as a coroutine on the engine's loop.
            try:
                queue.put_nowait(record)
                with self._state_lock:
                    self._stat_enqueued += 1
                return
            except asyncio.QueueFull:
                self._do_sync_append(record, reason="queue_full")
                return

        # Foreign thread (engine loop calling AuditLogger.log, FastAPI
        # worker, sync CLI, gateway thread): asyncio.Queue is NOT
        # thread-safe so we must marshal via ``call_soon_threadsafe``.
        # We deliberately do NOT block the producer waiting for the
        # loop to confirm — that would erase the latency win.
        #
        # Two backpressure cases:
        #
        # 1. ``qsize() >= maxsize`` *now* → producer-thread sync write
        #    (caller pays the filelock cost, loop unaffected). This is
        #    the steady-state backpressure path.
        # 2. qsize() saw room but the queue filled in the ~µs window
        #    before our ``call_soon_threadsafe`` callback runs on the
        #    loop. The loop-side fallback ``_put_or_fallback`` does a
        #    sync write — yes, that briefly blocks the loop, but this
        #    is rare (qsize check + scheduling is µs-level) and the
        #    alternative is *dropping* an audit record, which is
        #    worse: audit data integrity is a contractual obligation
        #    here. Operators can monitor ``stats['sync_fallback']``
        #    and raise ``OPENAKITA_AUDIT_QUEUE_MAX`` if it climbs.
        try:
            if queue.qsize() >= queue.maxsize:
                self._do_sync_append(record, reason="queue_full")
                return
            loop.call_soon_threadsafe(self._put_or_fallback, record)
            with self._state_lock:
                self._stat_enqueued += 1
            return
        except RuntimeError:
            self._do_sync_append(record, reason="loop_unavailable")
            return

    def _put_or_fallback(self, record: dict[str, Any]) -> None:
        """Runs on the loop thread (via call_soon_threadsafe).

        Tries the queue first; if it filled in the ~µs gap between
        qsize check and this callback, sync-writes inline. The sync
        write WILL briefly block the loop on filelock acquisition,
        but only in the tiny race window — degraded mode, not steady
        state. We log at WARNING so operators can detect chronic
        backpressure and bump ``OPENAKITA_AUDIT_QUEUE_MAX``.
        """
        if self._queue is None:
            return
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._do_sync_append(record, reason="queue_full_on_loop_race")

    def _do_sync_append(self, record: dict[str, Any], *, reason: str) -> None:
        """Bypass the queue and write the record synchronously.

        Used in three situations: worker not yet started, queue full,
        loop closed. We log once per fallback so ops can see if the
        async path is degraded.
        """
        if reason == "queue_full":
            logger.warning(
                "[audit_writer] queue full for %s; sync-writing record "
                "(this row is preserved but adds latency to the caller)",
                self._path,
            )
            with self._state_lock:
                self._stat_sync_fallback += 1
        try:
            self._writer.append(record)
        except Exception as exc:
            # Same fallback the legacy AuditLogger did: log a warning
            # and swallow — we never want audit to crash the engine.
            logger.error(
                "[audit_writer] sync-append failed for %s (reason=%s): %s",
                self._path,
                reason,
                exc,
            )

    # ------------------------------------------------------------------
    # Manual flush (mainly for tests / shutdown)
    # ------------------------------------------------------------------

    async def flush(self) -> None:
        """Wait until the queue is empty AND the worker has finished
        the current batch.

        Useful in tests: ``writer.enqueue(...)`` then ``await
        writer.flush()`` then assert the file contains the records.
        Not for high-frequency calls — relies on ``queue.join()``.
        """
        with self._state_lock:
            queue = self._queue
            running = (
                self._worker_task is not None
                and not self._worker_task.done()
                and not self._stopped
            )
        if not running or queue is None:
            return
        await queue.join()

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker_main(self) -> None:
        assert self._queue is not None
        queue = self._queue
        max_batch = self._max_batch_size
        max_delay = self._max_batch_delay_s
        logger.debug("[audit_writer] worker starting for %s", self._path)
        while True:
            try:
                first = await queue.get()
            except asyncio.CancelledError:
                logger.info("[audit_writer] worker cancelled for %s", self._path)
                return
            if first is None:
                # Sentinel — graceful stop. Drain remaining (which
                # were put before us in the order? No: ``stop`` uses
                # put_nowait/put for sentinel, so anything ahead of us
                # is already FIFO behind. The sentinel is last. So
                # after we mark task_done we exit. But task_done must
                # be paired with each get(); ``stop`` only puts the
                # sentinel itself, so we balance.)
                queue.task_done()
                logger.debug(
                    "[audit_writer] sentinel received; worker exiting for %s",
                    self._path,
                )
                return

            batch: list[dict[str, Any]] = [first]
            deadline = self._loop.time() + max_delay if self._loop else None  # type: ignore[union-attr]
            # Greedily pull more without blocking until we hit max_batch
            # or the deadline.
            while len(batch) < max_batch:
                if deadline is None:
                    break
                remaining = deadline - self._loop.time()  # type: ignore[union-attr]
                if remaining <= 0:
                    break
                try:
                    nxt = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    break
                if nxt is None:
                    # Sentinel mid-batch: flush what we have first, then
                    # exit. We put the sentinel back so the outer loop
                    # picks it up; but easier: just exit after this
                    # batch.
                    queue.task_done()
                    try:
                        self._writer.append_batch(batch)
                        self._stat_written += len(batch)
                        self._stat_batches += 1
                    finally:
                        # task_done for every record in batch
                        for _ in batch:
                            queue.task_done()
                    logger.debug(
                        "[audit_writer] sentinel mid-batch; flushed %d and exiting",
                        len(batch),
                    )
                    return
                batch.append(nxt)

            try:
                self._writer.append_batch(batch)
                self._stat_written += len(batch)
                self._stat_batches += 1
            except Exception as exc:
                logger.error(
                    "[audit_writer] batch-append failed for %s (%d records): %s",
                    self._path,
                    len(batch),
                    exc,
                )
                # On batch failure we still task_done() each entry so
                # ``flush()``/``stop()`` aren't deadlocked. Records are
                # lost in this branch — the alternative (re-enqueue)
                # risks an infinite retry loop if the failure is
                # deterministic (e.g. permission denied).
            finally:
                for _ in batch:
                    queue.task_done()

    # ------------------------------------------------------------------
    # Introspection (tests + future ops endpoint)
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, int]:
        """Snapshot of writer counters. Cheap, lock-free read."""
        return {
            "enqueued": self._stat_enqueued,
            "written": self._stat_written,
            "batches": self._stat_batches,
            "sync_fallback": self._stat_sync_fallback,
        }


# ---------------------------------------------------------------------------
# Process-wide singleton + integration with AuditLogger
# ---------------------------------------------------------------------------

_GLOBAL_WRITER: AsyncBatchAuditWriter | None = None
_GLOBAL_WRITER_LOCK = threading.Lock()


def get_async_audit_writer(path: str | None = None) -> AsyncBatchAuditWriter | None:
    """Return the process-wide singleton, or ``None`` if not initialized.

    Returning ``None`` is intentional: callers (the legacy
    :class:`AuditLogger`) can fall back to synchronous writes when no
    async writer has been started. This makes the async path opt-in
    via :func:`start_global_audit_writer` rather than implicit.

    Path comparison is normalised through ``pathlib.Path`` so callers
    passing ``"a/b.jsonl"`` and ``"a\\b.jsonl"`` (Windows) hit the same
    singleton. Without normalisation the Windows AuditLogger always
    fell through to sync because its stringified ``Path`` used ``\\``
    while ``start_global_audit_writer`` was called with ``/``.
    """
    with _GLOBAL_WRITER_LOCK:
        if _GLOBAL_WRITER is None:
            return None
        if path is not None:
            try:
                requested_norm = str(Path(path))
            except Exception:
                requested_norm = path
            if _GLOBAL_WRITER._path != requested_norm:
                logger.warning(
                    "[audit_writer] singleton path=%s does not match requested %s; "
                    "returning None so caller sync-writes",
                    _GLOBAL_WRITER._path,
                    requested_norm,
                )
                return None
        return _GLOBAL_WRITER


async def start_global_audit_writer(
    path: str,
    *,
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
    max_batch_delay_ms: float = DEFAULT_MAX_BATCH_DELAY_MS,
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
) -> AsyncBatchAuditWriter:
    """Start (or replace) the process-wide async audit writer."""
    global _GLOBAL_WRITER
    try:
        path_norm = str(Path(path))
    except Exception:
        path_norm = path
    with _GLOBAL_WRITER_LOCK:
        existing = _GLOBAL_WRITER
    if existing is not None:
        if existing._path == path_norm and existing.is_running():
            return existing
        await existing.stop()

    new_writer = AsyncBatchAuditWriter(
        path=path_norm,
        max_batch_size=max_batch_size,
        max_batch_delay_ms=max_batch_delay_ms,
        queue_maxsize=queue_maxsize,
    )
    await new_writer.start()
    with _GLOBAL_WRITER_LOCK:
        _GLOBAL_WRITER = new_writer
    return new_writer


async def stop_global_audit_writer() -> None:
    """Stop the process-wide writer (drain + await)."""
    global _GLOBAL_WRITER
    with _GLOBAL_WRITER_LOCK:
        existing = _GLOBAL_WRITER
        _GLOBAL_WRITER = None
    if existing is not None:
        await existing.stop()


def reset_for_testing() -> None:
    """Clear the global singleton WITHOUT awaiting (tests + fixtures).

    Use only when you've already stopped the writer or are confident
    no in-flight records are pending. Production code should call
    :func:`stop_global_audit_writer` instead.
    """
    global _GLOBAL_WRITER
    with _GLOBAL_WRITER_LOCK:
        _GLOBAL_WRITER = None


__all__ = [
    "AsyncBatchAuditWriter",
    "DEFAULT_MAX_BATCH_DELAY_MS",
    "DEFAULT_MAX_BATCH_SIZE",
    "DEFAULT_QUEUE_MAXSIZE",
    "get_async_audit_writer",
    "reset_for_testing",
    "start_global_audit_writer",
    "stop_global_audit_writer",
]
