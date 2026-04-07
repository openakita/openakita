"""
Dual Event Loop Bridge — API loop / Engine loop isolation.

When active, uvicorn (HTTP + WebSocket) runs in a background thread with its
own event loop ("API loop"), while the main thread keeps the "engine loop"
for Agent, OrgRuntime, LLM calls, Scheduler, Gateway, etc.

All bridge functions are **no-ops** when dual-loop is not active (single loop
mode), so existing behaviour is fully preserved.

Usage in API route handlers::

    from openakita.core.engine_bridge import to_engine
    result = await to_engine(runtime.send_command(org_id, ...))

Usage for streaming (async generators that live in the engine loop)::

    from openakita.core.engine_bridge import engine_stream
    return StreamingResponse(engine_stream(gen), ...)
"""

from __future__ import annotations

import asyncio
import logging
import queue as stdlib_queue
from collections.abc import AsyncIterator, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_engine_loop: asyncio.AbstractEventLoop | None = None
_api_loop: asyncio.AbstractEventLoop | None = None


def set_engine_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _engine_loop
    _engine_loop = loop
    logger.info("[EngineBridge] Engine loop registered (id=%s)", id(loop))


def set_api_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _api_loop
    _api_loop = loop
    logger.info("[EngineBridge] API loop registered (id=%s)", id(loop))


def get_engine_loop() -> asyncio.AbstractEventLoop | None:
    return _engine_loop


def get_api_loop() -> asyncio.AbstractEventLoop | None:
    return _api_loop


def is_dual_loop() -> bool:
    return _engine_loop is not None and _api_loop is not None


def _current_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


# ── Cross-loop coroutine execution ──────────────────────────────────────


async def to_engine(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run *coro* in the engine loop and return the result.

    * If dual-loop is inactive → runs *coro* directly.
    * If already in the engine loop → runs *coro* directly.
    * Otherwise → submits via ``run_coroutine_threadsafe`` and awaits.
    """
    if _engine_loop is None:
        return await coro
    current = _current_loop()
    if current is _engine_loop:
        return await coro
    future = asyncio.run_coroutine_threadsafe(coro, _engine_loop)
    return await asyncio.wrap_future(future)


async def to_api(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run *coro* in the API loop (e.g. WebSocket broadcast from the engine)."""
    if _api_loop is None:
        return await coro
    current = _current_loop()
    if current is _api_loop:
        return await coro
    future = asyncio.run_coroutine_threadsafe(coro, _api_loop)
    return await asyncio.wrap_future(future)


def fire_in_api(coro: Coroutine[Any, Any, Any]) -> None:
    """Schedule *coro* in the API loop without waiting for the result.

    Fire-and-forget variant of :func:`to_api`, used for broadcasting events
    from the engine loop where we don't care about the return value.
    """
    if _api_loop is None:
        loop = _current_loop()
        if loop is not None:
            loop.create_task(coro)
        return
    current = _current_loop()
    if current is _api_loop:
        _api_loop.create_task(coro)
    else:
        asyncio.run_coroutine_threadsafe(coro, _api_loop)


# ── Cross-loop async generator bridge ──────────────────────────────────


_STREAM_DONE = object()
_STREAM_ERROR = "__ENGINE_STREAM_ERROR__"


async def engine_stream(async_gen: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """Bridge an async iterator that lives in the engine loop to the API loop.

    Internally:
    1. Spawns a pump coroutine in the engine loop that iterates *async_gen*
       and pushes items into a thread-safe ``queue.Queue``.
    2. In the calling (API) loop, reads items via ``asyncio.to_thread(q.get)``
       and yields them.

    If dual-loop is not active, yields items directly with zero overhead.
    """
    if _engine_loop is None:
        async for item in async_gen:
            yield item
        return

    current = _current_loop()
    if current is _engine_loop:
        async for item in async_gen:
            yield item
        return

    buf: stdlib_queue.Queue[Any] = stdlib_queue.Queue(maxsize=512)

    async def _pump() -> None:
        try:
            async for item in async_gen:
                buf.put(item)
        except Exception as exc:
            buf.put((_STREAM_ERROR, exc))
        finally:
            buf.put(_STREAM_DONE)

    fut = asyncio.run_coroutine_threadsafe(_pump(), _engine_loop)
    try:
        while True:
            item = await asyncio.to_thread(buf.get)
            if item is _STREAM_DONE:
                break
            if isinstance(item, tuple) and len(item) == 2 and item[0] == _STREAM_ERROR:
                raise item[1]
            yield item
    finally:
        if not fut.done():
            fut.cancel()


# ── Shutdown ────────────────────────────────────────────────────────────


def shutdown() -> None:
    """Clear loop references (called during process shutdown)."""
    global _engine_loop, _api_loop
    _engine_loop = None
    _api_loop = None
