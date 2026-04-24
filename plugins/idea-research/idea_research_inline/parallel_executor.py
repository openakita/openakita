"""Async parallel executor with a hard concurrency cap — Phase 0.

Phase 3 callers (per-frame VLM, per-account compare) lean on this.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def run_with_semaphore(
    items: Iterable[T],
    fn: Callable[[T], Awaitable[R]],
    *,
    concurrency: int = 4,
    return_exceptions: bool = True,
) -> list[R | BaseException]:
    """Run ``fn`` over ``items`` with at most ``concurrency`` in flight."""

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _wrapped(it: T) -> R:
        async with sem:
            return await fn(it)

    return await asyncio.gather(
        *(_wrapped(it) for it in items),
        return_exceptions=return_exceptions,
    )


__all__ = ["run_with_semaphore"]
