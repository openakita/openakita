"""Tests for the MultiPost compat engine.

The engine is pure asyncio, so we can unit-test its state machine
without a browser. We cover:

* semver comparison (``version_satisfies``)
* payload shaping (``build_mp_payload``) — especially that the
  platform-id mapping and the "no cookies in payload" rule hold
* the status-recording surface (``record_status`` / ``is_available``)
* the dispatch ↔ ack rendezvous (normal, duplicate ack, timeout)
* the pending-dispatches polling helper
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from omni_post_engine_mp import (
    DEFAULT_MIN_VERSION,
    MultiPostCompatEngine,
    build_mp_payload,
    version_satisfies,
)
from omni_post_models import ErrorKind


# ---------------------------------------------------------------------------
# semver helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seen", "minimum", "expected"),
    [
        ("1.3.8", "1.3.8", True),
        ("1.3.9", "1.3.8", True),
        ("1.4.0", "1.3.8", True),
        ("2.0.0", "1.3.8", True),
        ("1.3.7", "1.3.8", False),
        ("1.2.9", "1.3.8", False),
        ("", "1.3.8", False),
        ("garbage", "1.3.8", False),
        ("1.3.8-beta", "1.3.8", True),
        ("1.3", "1.3.0", True),
    ],
)
def test_version_satisfies(seen: str, minimum: str, expected: bool) -> None:
    assert version_satisfies(seen, minimum) is expected


# ---------------------------------------------------------------------------
# Payload shaping
# ---------------------------------------------------------------------------


def test_build_mp_payload_contains_no_cookie_fields() -> None:
    payload = build_mp_payload(
        task={
            "id": "tk-1",
            "platform": "rednote",
            "payload": {"title": "hi", "content": "body", "hashtags": ["#a"]},
            "client_trace_id": "trace-1",
        },
        asset_info={"kind": "image", "storage_path": "/tmp/x.png", "filename": "x.png"},
        settings={"auto_submit": False},
    )
    assert payload["action"] == "MULTIPOST_EXTENSION_REQUEST_PUBLISH"
    assert payload["contract_version"] == DEFAULT_MIN_VERSION
    assert payload["data"]["platform"] == "xiaohongshu"  # remapped
    assert payload["data"]["task_id"] == "tk-1"
    assert payload["data"]["client_trace_id"] == "trace-1"
    assert payload["data"]["asset"]["path"] == "/tmp/x.png"
    assert payload["data"]["auto_submit"] is False
    flat = repr(payload).lower()
    for banned in ("cookie", "bearer", "authorization", "set-cookie"):
        assert banned not in flat


def test_build_mp_payload_passthrough_for_unmapped_platform() -> None:
    """Unknown platform id stays verbatim (don't lose the user's intent)."""

    payload = build_mp_payload(
        task={
            "id": "tk-unknown",
            "platform": "some_future_thing",
            "payload": {"title": "t"},
        },
        asset_info=None,
    )
    assert payload["data"]["platform"] == "some_future_thing"
    assert "asset" not in payload["data"]


# ---------------------------------------------------------------------------
# Status / availability
# ---------------------------------------------------------------------------


def test_record_status_and_is_available() -> None:
    eng = MultiPostCompatEngine(settings={"mp_extension_min_version": "1.3.8"})
    assert eng.is_available() is False

    eng.record_status(installed=True, version="1.4.0", trusted_domain_ok=True)
    assert eng.is_available() is True
    snap = eng.snapshot_status()
    assert snap["installed"] is True
    assert snap["version_ok"] is True

    # Too old ⇒ not available.
    eng.record_status(installed=True, version="1.2.0", trusted_domain_ok=True)
    assert eng.is_available() is False
    # Domain not trusted ⇒ not available.
    eng.record_status(installed=True, version="1.4.0", trusted_domain_ok=False)
    assert eng.is_available() is False


# ---------------------------------------------------------------------------
# Dispatch / ack rendezvous
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine() -> MultiPostCompatEngine:
    broadcasts: list[tuple[str, dict[str, Any]]] = []
    eng = MultiPostCompatEngine(
        settings={"mp_extension_min_version": "1.3.8"},
        ack_timeout_seconds=2.0,
        broadcaster=lambda topic, data: broadcasts.append((topic, data)),
    )
    eng.broadcasts = broadcasts  # type: ignore[attr-defined]
    eng.record_status(installed=True, version="1.4.0", trusted_domain_ok=True)
    return eng


@pytest.mark.asyncio
async def test_dispatch_resolves_on_ack(engine: MultiPostCompatEngine) -> None:
    task = {"id": "tk-1", "platform": "douyin", "payload": {"title": "hi"}}

    async def _ack_soon() -> None:
        await asyncio.sleep(0.05)
        assert await engine.ack(
            task_id="tk-1",
            success=True,
            published_url="https://douyin.com/video/1",
        )

    asyncio.create_task(_ack_soon())
    outcome = await engine.dispatch(task=task, asset_info=None)
    assert outcome.success is True
    assert outcome.published_url == "https://douyin.com/video/1"
    # And the broadcast fired exactly once with the right topic.
    assert any(b[0] == "mp_dispatch" for b in engine.broadcasts)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_duplicate_ack_returns_false(engine: MultiPostCompatEngine) -> None:
    task = {"id": "tk-2", "platform": "douyin", "payload": {"title": "hi"}}

    async def _ack_twice() -> None:
        await asyncio.sleep(0.05)
        assert await engine.ack(task_id="tk-2", success=True, published_url="u")
        # Second ack has nothing to resolve.
        assert await engine.ack(task_id="tk-2", success=True) is False

    asyncio.create_task(_ack_twice())
    outcome = await engine.dispatch(task=task, asset_info=None)
    assert outcome.success is True


@pytest.mark.asyncio
async def test_dispatch_timeout_returns_typed_error(engine: MultiPostCompatEngine) -> None:
    engine._ack_timeout = 0.05  # noqa: SLF001 - test-only shortcut
    outcome = await engine.dispatch(
        task={"id": "tk-t", "platform": "douyin", "payload": {}},
        asset_info=None,
    )
    assert outcome.success is False
    assert outcome.error_kind == ErrorKind.TIMEOUT.value


@pytest.mark.asyncio
async def test_dispatch_refuses_when_extension_unavailable() -> None:
    eng = MultiPostCompatEngine(settings={})
    # Never record_status → extension counts as missing.
    out = await eng.dispatch(
        task={"id": "tk-u", "platform": "douyin", "payload": {}},
        asset_info=None,
    )
    assert out.success is False
    assert out.error_kind == ErrorKind.DEPENDENCY.value


@pytest.mark.asyncio
async def test_pending_dispatches_reflect_live_state(engine: MultiPostCompatEngine) -> None:
    async def _ack_later() -> None:
        await asyncio.sleep(0.1)
        await engine.ack(task_id="tk-p", success=True)

    asyncio.create_task(_ack_later())
    dispatch_task = asyncio.create_task(
        engine.dispatch(
            task={"id": "tk-p", "platform": "douyin", "payload": {}},
            asset_info=None,
        )
    )
    await asyncio.sleep(0.02)
    pending = engine.list_pending_dispatches()
    assert len(pending) == 1
    assert pending[0]["task_id"] == "tk-p"
    assert "payload" in pending[0]
    await dispatch_task
