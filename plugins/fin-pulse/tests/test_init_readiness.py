"""Regression tests for the ``_await_init`` / ``_require_pipeline_ready``
pair we introduced after the ``ingest failed: http_500`` report.

The real bug: the FastAPI router is registered the moment
:class:`Plugin.on_load` finishes, but the SQLite bootstrap runs as a
background task. A user clicking 拉取 fast enough hit
``assert self._db is not None`` inside :meth:`FinpulseTaskManager`, which
FastAPI surfaced as a body-less ``500`` and the iframe rendered as the
useless ``ingest failed: http_500`` toast.

The fix is to gate every DB-touching route through a small helper that
either waits briefly for init, or raises a structured ``503`` whose
``detail`` carries the actual cause (still-initialising vs. real error).
These tests pin that behaviour so the regression cannot quietly come
back.
"""

from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest

from tests.test_schedule import _load_plugin_module

plugin_mod = _load_plugin_module()
HTTPException = plugin_mod.HTTPException
_await_init = plugin_mod.Plugin._await_init
_require_pipeline_ready = plugin_mod.Plugin._require_pipeline_ready


class _FakeTM:
    def __init__(self, ready: bool) -> None:
        self._ready = ready

    def is_ready(self) -> bool:
        return self._ready


def _make_self(
    *,
    tm: Any = None,
    pipeline: Any = object(),
    init_task: asyncio.Task | None = None,
) -> types.SimpleNamespace:
    """Build the smallest possible fake ``self`` that ``_await_init``
    needs. We deliberately avoid instantiating the real ``Plugin``
    class — its ``__init__`` pulls in the whole host plugin API.

    ``_require_pipeline_ready`` calls ``self._await_init(...)`` so we
    bind the unbound function as a stub method on the namespace.
    """
    fake = types.SimpleNamespace(
        _tm=tm,
        _pipeline=pipeline,
        _init_task=init_task,
    )
    fake._await_init = lambda *, timeout: _await_init(fake, timeout=timeout)
    return fake


# ── _await_init ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_await_init_returns_ready_when_db_already_open() -> None:
    """The fast path: DB is already open, no waiting needed."""
    fake = _make_self(tm=_FakeTM(ready=True))
    ready, err = await _await_init(fake, timeout=0.01)
    assert ready is True
    assert err is None


@pytest.mark.asyncio
async def test_await_init_reports_in_progress_on_timeout() -> None:
    """When init is still running we must return ``init_in_progress``
    so the caller can convert it to a retryable 503 — *not* a 500."""

    async def _slow_init() -> None:
        await asyncio.sleep(5)

    init_task = asyncio.create_task(_slow_init())
    try:
        fake = _make_self(tm=_FakeTM(ready=False), init_task=init_task)
        ready, err = await _await_init(fake, timeout=0.05)
        assert ready is False
        assert err == "init_in_progress"
    finally:
        init_task.cancel()
        try:
            await init_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_await_init_surfaces_real_error_when_bootstrap_failed() -> None:
    """If init already crashed, we want the actual exception text on
    the wire — that's what makes the difference between a useful 503
    and the bare ``http_500`` users were seeing."""

    async def _failing_init() -> None:
        raise RuntimeError("disk is locked")

    init_task = asyncio.create_task(_failing_init())
    with pytest.raises(RuntimeError):
        await init_task  # drive the task to its failed terminal state

    fake = _make_self(tm=_FakeTM(ready=False), init_task=init_task)
    ready, err = await _await_init(fake, timeout=0.05)
    assert ready is False
    assert err is not None
    assert "disk is locked" in err


@pytest.mark.asyncio
async def test_await_init_handles_missing_task_manager() -> None:
    """If ``_tm`` was never created (skeleton/missing dep), we still
    must not blow up — we want a structured error string."""
    fake = _make_self(tm=None)
    ready, err = await _await_init(fake, timeout=0.01)
    assert ready is False
    assert err == "task_manager_unavailable"


# ── _require_pipeline_ready ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_pipeline_ready_raises_503_when_initialising() -> None:
    """The original /ingest 500 path is now a 503 with a Chinese
    detail explaining the init delay (visible in the toast tooltip)."""

    async def _slow_init() -> None:
        await asyncio.sleep(5)

    init_task = asyncio.create_task(_slow_init())
    try:
        fake = _make_self(tm=_FakeTM(ready=False), init_task=init_task)
        with pytest.raises(HTTPException) as ei:
            await _require_pipeline_ready(fake, timeout=0.05)
        assert ei.value.status_code == 503
        # Detail should *not* be the meaningless "pipeline_unavailable"
        # — the user actively wants to know it's still booting.
        assert "正在初始化" in str(ei.value.detail)
    finally:
        init_task.cancel()
        try:
            await init_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_require_pipeline_ready_passes_when_ready() -> None:
    """No-op when init has finished — does not raise, returns ``None``."""
    fake = _make_self(tm=_FakeTM(ready=True))
    result = await _require_pipeline_ready(fake, timeout=0.01)
    assert result is None


@pytest.mark.asyncio
async def test_require_pipeline_ready_returns_503_when_dependencies_missing() -> None:
    """Missing tm/pipeline (e.g. inert skeleton mode) still surfaces a
    structured 503 with the legacy detail token."""
    fake = _make_self(tm=None, pipeline=None)
    with pytest.raises(HTTPException) as ei:
        await _require_pipeline_ready(fake, timeout=0.01)
    assert ei.value.status_code == 503
    assert "pipeline_unavailable" in str(ei.value.detail)
