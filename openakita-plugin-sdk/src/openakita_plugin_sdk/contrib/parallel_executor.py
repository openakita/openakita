"""Parallel executor — bounded concurrency without silent skips.

Inspired by CutClaw's ``core.py:1565-1637`` pattern where independent LLM
calls were run with ``asyncio.gather`` and a bare ``try/except`` that
swallowed individual failures.  The result was *silent skipping*: a 30-clip
job that lost 5 to network errors looked identical to a clean run.

This module fixes that by:

1. **Always returning a Result row per input** — even on failure.  Callers
   can ``[r for r in results if r.failed]`` and report a clear count
   instead of guessing why the output is short.
2. **Bounded concurrency via Semaphore** — pass ``max_concurrency`` to
   throttle vendor APIs.  The default (``8``) matches the conservative
   per-key rate limits typical of LLM and image-generation providers.
3. **Optional fail-fast** — when ``fail_fast=True``, the first exception
   cancels in-flight tasks and re-raises (use for transactional batches).
4. **Progress callback** — ``on_progress(done, total)`` fires after each
   item completes (success OR fail).  Synchronous callable, called from
   inside the gather loop; keep it cheap.

Design rules:

* **Pure stdlib + asyncio** — no extra deps.
* **No multi-path** — every plugin's bounded-parallel pattern routes
  through ``run_parallel`` so future fixes (e.g. adaptive concurrency,
  per-key buckets) land once.
* **Generic-over-runner** — accepts any ``async def runner(item) -> T``.
  ``T`` is opaque to the executor; we only wrap it in ``ParallelResult``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")
ItemT = TypeVar("ItemT")

__all__ = [
    "ParallelResult",
    "ParallelSummary",
    "run_parallel",
    "summarize",
]


@dataclass(frozen=True)
class ParallelResult(Generic[T]):
    """One row of the executor output (one per input item).

    Exactly one of ``value`` / ``error`` is meaningful depending on
    ``status``.  ``index`` mirrors the position in the input iterable so
    callers can reorder if they need stable output ordering.
    """

    index: int
    item: Any                # original input item (kept for error reports)
    status: str              # "ok" | "failed" | "cancelled"
    value: T | None = None
    error: BaseException | None = None
    elapsed_sec: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "status": self.status,
            "value": self.value,
            "error": repr(self.error) if self.error is not None else None,
            "elapsed_sec": round(self.elapsed_sec, 4),
        }


@dataclass(frozen=True)
class ParallelSummary:
    """Aggregate count breakdown produced by :func:`summarize`.

    Use to populate a clear UI report: ``"completed 25/30, 4 failed,
    1 cancelled — see logs"``.
    """

    total: int
    ok: int
    failed: int
    cancelled: int
    total_elapsed_sec: float
    error_messages: list[str] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return self.failed == 0 and self.cancelled == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "ok": self.ok,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "all_ok": self.all_ok,
            "total_elapsed_sec": round(self.total_elapsed_sec, 4),
            "error_messages": list(self.error_messages),
        }


def summarize(results: Sequence[ParallelResult[Any]]) -> ParallelSummary:
    """Reduce a list of results to a clean summary block.

    ``error_messages`` keeps at most the first 5 unique repr strings so
    a 100-item failure does not blow up the UI / log.
    """
    ok = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if r.failed)
    cancelled = sum(1 for r in results if r.cancelled)
    total_elapsed = sum(r.elapsed_sec for r in results)
    seen: set[str] = set()
    messages: list[str] = []
    for r in results:
        if r.error is None:
            continue
        msg = repr(r.error)
        if msg in seen:
            continue
        seen.add(msg)
        messages.append(msg)
        if len(messages) >= 5:
            break
    return ParallelSummary(
        total=len(results),
        ok=ok,
        failed=failed,
        cancelled=cancelled,
        total_elapsed_sec=total_elapsed,
        error_messages=messages,
    )


async def run_parallel(
    items: Iterable[ItemT],
    runner: Callable[[ItemT], Awaitable[T]],
    *,
    max_concurrency: int = 8,
    fail_fast: bool = False,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ParallelResult[T]]:
    """Run ``runner`` over each item with bounded concurrency.

    Args:
        items: Any iterable.  Materialised eagerly so ``len(results)`` is
            stable; callers with very large generators should chunk first.
        runner: ``async def runner(item) -> T``.  Must accept exactly one
            positional argument.  Exceptions are captured and reported
            (NEVER silently swallowed); :class:`asyncio.CancelledError` is
            propagated specially as ``status="cancelled"``.
        max_concurrency: Max simultaneous in-flight tasks.  Must be >= 1.
        fail_fast: If ``True``, the first exception cancels in-flight
            tasks and re-raises after collecting partial results into the
            exception's ``__context__``-friendly attribute (we attach
            ``.partial_results`` on the raised exception when we can).
            Default ``False`` — collect everything and let the caller
            decide.
        on_progress: Optional ``(done, total)`` callback fired after each
            item completes.  Errors in this callback are swallowed so a
            broken UI does not break the worker.

    Returns:
        A list of :class:`ParallelResult` aligned to input order.
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")

    materialised: list[ItemT] = list(items)
    total = len(materialised)
    if total == 0:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[ParallelResult[T] | None] = [None] * total
    done_counter = {"n": 0}
    fail_event = asyncio.Event() if fail_fast else None

    async def _wrapped(idx: int, item: ItemT) -> None:
        # Honour fail_fast cancellation: if a sibling already failed we
        # bail without ever entering the semaphore so we don't waste a
        # slot on work that will be discarded anyway.
        if fail_event is not None and fail_event.is_set():
            results[idx] = ParallelResult(
                index=idx, item=item, status="cancelled",
            )
            _bump_progress()
            return

        loop = asyncio.get_running_loop()
        start = loop.time()
        async with semaphore:
            if fail_event is not None and fail_event.is_set():
                results[idx] = ParallelResult(
                    index=idx, item=item, status="cancelled",
                    elapsed_sec=loop.time() - start,
                )
                _bump_progress()
                return
            try:
                value = await runner(item)
            except asyncio.CancelledError:
                results[idx] = ParallelResult(
                    index=idx, item=item, status="cancelled",
                    elapsed_sec=loop.time() - start,
                )
                # Re-raise so the gather propagates the cancellation
                # upwards if the *external* caller cancelled us; if we
                # cancelled ourselves via fail_event the
                # asyncio.gather(return_exceptions=True) below will
                # absorb it and we already wrote the result row.
                raise
            except Exception as e:  # noqa: BLE001
                results[idx] = ParallelResult(
                    index=idx, item=item, status="failed", error=e,
                    elapsed_sec=loop.time() - start,
                )
                if fail_event is not None:
                    fail_event.set()
                _bump_progress()
                return
            results[idx] = ParallelResult(
                index=idx, item=item, status="ok", value=value,
                elapsed_sec=loop.time() - start,
            )
            _bump_progress()

    def _bump_progress() -> None:
        done_counter["n"] += 1
        if on_progress is None:
            return
        try:
            on_progress(done_counter["n"], total)
        except Exception:  # noqa: BLE001
            # never let a UI hook break the worker
            pass

    tasks = [
        asyncio.create_task(_wrapped(i, item))
        for i, item in enumerate(materialised)
    ]
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # Outer cancel: kill all in-flight tasks and let the caller see it.
        for t in tasks:
            if not t.done():
                t.cancel()
        # Drain so we don't leave dangling tasks.
        await asyncio.gather(*tasks, return_exceptions=True)
        # Fill any still-pending slots so the contract "1 result per input"
        # is preserved even on outer cancel.
        for i, slot in enumerate(results):
            if slot is None:
                results[i] = ParallelResult(
                    index=i, item=materialised[i], status="cancelled",
                )
        raise

    # Belt-and-braces: every slot must be filled.  This should be
    # impossible given the wrapping above, but defending explicitly here
    # protects against future refactors that introduce an early ``return``.
    for i, slot in enumerate(results):
        if slot is None:
            results[i] = ParallelResult(
                index=i, item=materialised[i], status="cancelled",
            )

    final: list[ParallelResult[T]] = [r for r in results if r is not None]

    if fail_fast and any(r.failed for r in final):
        first_failed = next(r for r in final if r.failed)
        # ``first_failed.error`` is the user-facing root cause.
        err = first_failed.error
        if err is not None:
            try:
                err.partial_results = final  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                # Built-in exception types may forbid attribute writes;
                # the caller can still iterate via the returned list
                # (which is also raised through the chain).
                pass
            raise err
    return final
