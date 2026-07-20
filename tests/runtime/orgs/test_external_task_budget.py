from __future__ import annotations

import asyncio

import pytest

from openakita.orgs._runtime_external_tasks import (
    ExternalTaskTimeout,
    ExternalTaskTracker,
    wait_with_external_task_budget,
)


@pytest.mark.asyncio
async def test_external_wait_does_not_consume_leaf_node_budget() -> None:
    tracker = ExternalTaskTracker(active_calls=1, max_wait_s=1.0)

    async def work() -> str:
        await asyncio.sleep(0.08)
        tracker.active_calls = 0
        await asyncio.sleep(0.01)
        return "done"

    result = await wait_with_external_task_budget(
        work(),
        node_timeout_s=0.05,
        tracker=tracker,
    )

    assert result == "done"
    assert tracker.external_wait_s > 0


@pytest.mark.asyncio
async def test_external_wait_has_its_own_hard_limit() -> None:
    tracker = ExternalTaskTracker(active_calls=1, max_wait_s=0.03)

    with pytest.raises(TimeoutError, match="external task wait budget"):
        await wait_with_external_task_budget(
            asyncio.sleep(1),
            node_timeout_s=1,
            tracker=tracker,
        )


@pytest.mark.asyncio
async def test_timeout_raised_by_external_handler_is_classified_as_external() -> None:
    tracker = ExternalTaskTracker(active_calls=1, max_wait_s=1.0)

    async def work() -> None:
        raise TimeoutError("provider polling timed out")

    with pytest.raises(ExternalTaskTimeout, match="provider polling timed out"):
        await wait_with_external_task_budget(
            work(),
            node_timeout_s=0.01,
            tracker=tracker,
        )
