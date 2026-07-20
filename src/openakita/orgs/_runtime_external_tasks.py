"""Execution-budget accounting for tools backed by external async jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class ExternalTaskTracker:
    active_calls: int = 0
    max_wait_s: float = 0.0
    external_wait_s: float = 0.0


class NodeActivationTimeout(TimeoutError):
    """Local/LLM work exhausted the node activation budget."""


class ExternalTaskTimeout(TimeoutError):
    """An external job exhausted its independently declared wait budget."""


current_external_task_tracker_var: ContextVar[ExternalTaskTracker | None] = ContextVar(
    "org_current_external_task_tracker",
    default=None,
)


def external_task_timeout(definition: dict[str, Any] | None) -> float:
    if not isinstance(definition, dict):
        return 0.0
    execution = definition.get("x-openakita-execution")
    if not isinstance(execution, dict) or execution.get("kind") != "external_task":
        return 0.0
    try:
        return max(0.0, min(float(execution.get("timeout_s") or 0), 3600.0))
    except (TypeError, ValueError):
        return 0.0


async def wait_with_external_task_budget(
    awaitable: Awaitable[Any],
    *,
    node_timeout_s: float,
    tracker: ExternalTaskTracker,
) -> Any:
    """Apply the leaf timeout only while the node is doing local/LLM work.

    External jobs have their own bounded budget. This prevents a 420-second
    node timeout from cancelling a provider poll whose declared timeout is
    longer, without giving the surrounding LLM an unbounded activation.
    """

    task = asyncio.ensure_future(awaitable)
    loop = asyncio.get_running_loop()
    remaining = float(node_timeout_s)
    last = loop.time()
    try:
        while True:
            was_external_wait = tracker.active_calls > 0
            if was_external_wait and tracker.max_wait_s > 0:
                next_budget = tracker.max_wait_s - tracker.external_wait_s
            else:
                next_budget = remaining
            done, _ = await asyncio.wait({task}, timeout=min(1.0, max(next_budget, 0.01)))
            now = loop.time()
            elapsed = now - last
            last = now
            if was_external_wait:
                tracker.external_wait_s += elapsed
            else:
                remaining -= elapsed
            if task in done:
                try:
                    return await task
                except TimeoutError as exc:
                    if tracker.max_wait_s > 0:
                        raise ExternalTaskTimeout(str(exc)) from exc
                    raise
            if was_external_wait:
                if tracker.max_wait_s > 0 and tracker.external_wait_s >= tracker.max_wait_s:
                    raise ExternalTaskTimeout("external task wait budget exceeded")
            else:
                if remaining <= 0:
                    raise NodeActivationTimeout("node activation budget exceeded")
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


__all__ = [
    "ExternalTaskTracker",
    "ExternalTaskTimeout",
    "NodeActivationTimeout",
    "current_external_task_tracker_var",
    "external_task_timeout",
    "wait_with_external_task_budget",
]
