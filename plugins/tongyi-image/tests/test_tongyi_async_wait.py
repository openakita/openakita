"""Unit tests for the in-tool wait loop added to ``Plugin._create_task_internal``.

The fix being covered: workbench LLMs were treating the immediate
``status='running' / image_urls=[]`` response from
``tongyi_image_create`` as success and submitting an empty deliverable.
We now poll DashScope inside the tool until the task settles (or a
180 s safety timeout fires) so the returned payload already carries
asset_ids / local_paths in the common case.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

# Imported via conftest path manipulation.
from plugin import Plugin  # noqa: E402
from tongyi_dashscope_client import DashScopeError  # noqa: E402


def _make_plugin() -> Plugin:
    """Build a Plugin with the bare minimum stubs needed by
    :pyfunc:`Plugin._wait_for_async_task`. The wait loop only ever
    touches ``self._client.get_task``, ``self._tm.update_task``,
    ``self._download_and_publish_images`` and ``self._broadcast_update``
    — everything else stays untouched.
    """
    pl = Plugin()
    pl._client = MagicMock()
    pl._client.get_task = AsyncMock()
    pl._tm = MagicMock()
    pl._tm.update_task = AsyncMock()
    pl._download_and_publish_images = AsyncMock()
    pl._broadcast_update = MagicMock()
    # Speed the test up — production defaults are 3 / 5 / 180 seconds.
    pl._ASYNC_WAIT_INITIAL_DELAY = 0.0
    pl._ASYNC_WAIT_INTERVAL = 0.0
    pl._ASYNC_WAIT_TIMEOUT = 1.0
    return pl


@pytest.mark.asyncio
async def test_wait_returns_immediately_when_task_succeeds() -> None:
    pl = _make_plugin()
    pl._client.get_task.return_value = {
        "output": {
            "task_status": "SUCCEEDED",
            "results": [{"url": "https://x.test/foo.png"}],
        },
        "usage": {"image_count": 1},
    }

    await pl._wait_for_async_task(
        task_id="local-1", api_task_id="ds-1", prompt="cyberpunk skyline",
    )

    pl._tm.update_task.assert_awaited_once()
    call = pl._tm.update_task.await_args
    assert call.args == ("local-1",)
    assert call.kwargs["status"] == "succeeded"
    assert call.kwargs["image_urls"] == ["https://x.test/foo.png"]
    pl._download_and_publish_images.assert_awaited_once()
    pl._broadcast_update.assert_called_once_with("local-1", "succeeded")


@pytest.mark.asyncio
async def test_wait_records_failure_when_task_fails() -> None:
    pl = _make_plugin()
    pl._client.get_task.return_value = {
        "output": {
            "task_status": "FAILED",
            "message": "moderation rejected the prompt",
        },
    }

    await pl._wait_for_async_task(
        task_id="local-2", api_task_id="ds-2", prompt="bad prompt",
    )

    pl._tm.update_task.assert_awaited_once()
    call = pl._tm.update_task.await_args
    assert call.kwargs["status"] == "failed"
    assert "moderation" in call.kwargs["error_message"]
    # No download attempt for failed tasks.
    pl._download_and_publish_images.assert_not_awaited()
    pl._broadcast_update.assert_called_once_with("local-2", "failed")


@pytest.mark.asyncio
async def test_wait_keeps_polling_through_running_state() -> None:
    pl = _make_plugin()
    pl._ASYNC_WAIT_TIMEOUT = 5.0
    # First two polls return RUNNING, third returns SUCCEEDED.
    pl._client.get_task.side_effect = [
        {"output": {"task_status": "RUNNING"}},
        {"output": {"task_status": "RUNNING"}},
        {
            "output": {
                "task_status": "SUCCEEDED",
                "results": [{"url": "https://x.test/done.png"}],
            },
        },
    ]

    await pl._wait_for_async_task(
        task_id="local-3", api_task_id="ds-3", prompt="prompt",
    )

    assert pl._client.get_task.await_count == 3
    pl._tm.update_task.assert_awaited_once()
    assert pl._tm.update_task.await_args.kwargs["status"] == "succeeded"


@pytest.mark.asyncio
async def test_wait_swallows_transient_dashscope_errors() -> None:
    pl = _make_plugin()
    pl._ASYNC_WAIT_TIMEOUT = 5.0
    pl._client.get_task.side_effect = [
        DashScopeError("Throttling", "rate limit"),
        ConnectionError("network glitch"),
        {
            "output": {
                "task_status": "SUCCEEDED",
                "results": [{"url": "https://x.test/r.png"}],
            },
        },
    ]

    await pl._wait_for_async_task(
        task_id="local-4", api_task_id="ds-4", prompt="prompt",
    )

    # All three calls happened; the wait did NOT abort on transient errors.
    assert pl._client.get_task.await_count == 3
    pl._tm.update_task.assert_awaited_once()
    assert pl._tm.update_task.await_args.kwargs["status"] == "succeeded"


@pytest.mark.asyncio
async def test_wait_times_out_silently_when_task_keeps_running() -> None:
    pl = _make_plugin()
    pl._ASYNC_WAIT_TIMEOUT = 0.05  # tiny window; will hit the timeout fast
    pl._ASYNC_WAIT_INTERVAL = 0.01
    pl._client.get_task.return_value = {"output": {"task_status": "RUNNING"}}

    # Should not raise; should not flip the task status. The background
    # _poll_loop will eventually pick it up.
    await pl._wait_for_async_task(
        task_id="local-5", api_task_id="ds-5", prompt="prompt",
    )

    pl._tm.update_task.assert_not_awaited()
    pl._broadcast_update.assert_not_called()


@pytest.mark.asyncio
async def test_wait_no_op_when_client_missing() -> None:
    pl = _make_plugin()
    pl._client = None

    # Without a configured DashScope client there is nothing to poll. The
    # method must return immediately and not blow up — this guards the
    # "user removed API key while a task was queued" edge case.
    await pl._wait_for_async_task(
        task_id="local-6", api_task_id="ds-6", prompt="prompt",
    )

    pl._tm.update_task.assert_not_awaited()
