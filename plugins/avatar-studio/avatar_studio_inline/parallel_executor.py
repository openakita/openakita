"""Parallel executor — bounded concurrency without silent skips.

Vendored from ``openakita_plugin_sdk.contrib.parallel_executor`` (SDK 0.6.0)
into avatar-studio in 1.0.0 (forked from ``plugins/seedance-video/seedance_inline``);
see ``avatar_studio_inline/__init__.py``. Used by future video_reface
refinements to fan out multi-frame face checks with bounded concurrency.
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
    """One row of the executor output (one per input item)."""

    index: int
    item: Any
    status: str
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
    """Aggregate count breakdown produced by :func:`summarize`."""

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
    """Reduce a list of results to a clean summary block."""
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
    """Run ``runner`` over each item with bounded concurrency."""
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
            pass

    tasks = [
        asyncio.create_task(_wrapped(i, item))
        for i, item in enumerate(materialised)
    ]
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for i, slot in enumerate(results):
            if slot is None:
                results[i] = ParallelResult(
                    index=i, item=materialised[i], status="cancelled",
                )
        raise

    for i, slot in enumerate(results):
        if slot is None:
            results[i] = ParallelResult(
                index=i, item=materialised[i], status="cancelled",
            )

    final: list[ParallelResult[T]] = [r for r in results if r is not None]

    if fail_fast and any(r.failed for r in final):
        first_failed = next(r for r in final if r.failed)
        err = first_failed.error
        if err is not None:
            try:
                err.partial_results = final  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass
            raise err
    return final
