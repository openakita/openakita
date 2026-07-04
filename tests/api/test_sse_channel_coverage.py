"""Contract tests for the v2 SSE channel-coverage invariant (I13a).

After v25 RC-3 the SSE handler subscribes to every
:data:`~openakita.runtime.stream.STANDARD_CHANNELS` channel except the
high-volume ``debug`` channel, and no longer fabricates a synthetic
``lifecycle/sse_connected`` first event. These tests pin that
contract so the regression that v22 / v25 surfaced cannot return:

* The frontend used to listen to ``stalls`` / ``replans`` channels
  that the supervisor never publishes to (supervisor emits
  ``stall_warning`` / ``replanning`` on the ``lifecycle`` channel),
  so the type-level invariant must also forbid those names from
  ``STANDARD_CHANNELS``.
* The synthetic first event would always satisfy "first event
  received" assertions even when no real supervisor event ever
  flowed, masking the entire channel-coverage gap. The first SSE
  chunk must therefore be the spec-defined ``retry:`` directive,
  not a forged ``StreamEvent``.

These tests intentionally do NOT spin up FastAPI: they exercise the
module-level constant, the generator with a fake bus, and the
emit-then-receive happy path directly. That keeps the suite fast
(<100 ms) and proves the contract at the lowest meaningful layer.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from openakita.api.routes.orgs_v2_stream import (
    DEFAULT_SSE_CHANNELS,
    _event_stream,
)
from openakita.config import settings
from openakita.runtime.stream import STANDARD_CHANNELS
from openakita.runtime.stream_registry import (
    get_or_create_org_stream_bus,
    reset_org_stream_buses,
)


@pytest.fixture(autouse=True)
def _isolate_stream_registry() -> Iterator[None]:
    reset_org_stream_buses()
    yield
    reset_org_stream_buses()


# ---------------------------------------------------------------------------
# Module-level contract (no event loop needed)
# ---------------------------------------------------------------------------


def test_default_sse_channels_covers_standard_minus_debug() -> None:
    """I13a: SSE subscription must cover every supervisor-emitted channel.

    ``debug`` is the only opt-out (high volume, diagnostic only).
    Every other channel in :data:`STANDARD_CHANNELS` must be in
    :data:`DEFAULT_SSE_CHANNELS`, or supervisor events will be
    silently dropped before reaching the frontend.
    """
    expected = STANDARD_CHANNELS - {"debug"}
    assert set(DEFAULT_SSE_CHANNELS) >= expected, (
        "DEFAULT_SSE_CHANNELS must cover STANDARD_CHANNELS minus debug; "
        f"missing: {sorted(expected - set(DEFAULT_SSE_CHANNELS))}"
    )


def test_default_sse_channels_does_not_subscribe_debug() -> None:
    """``debug`` stays opt-in to keep the default firehose manageable."""
    assert "debug" not in DEFAULT_SSE_CHANNELS


def test_standard_channels_does_not_contain_stalls_or_replans() -> None:
    """Guard against the v22 / v25 frontend / backend channel mismatch.

    The supervisor emits ``stall_warning`` and ``replanning`` events
    on the ``lifecycle`` channel (``supervisor.py:448`` /
    ``supervisor.py:514``). The frontend used to subscribe to
    standalone ``stalls`` / ``replans`` channels that nothing ever
    published to. If a future change adds either name to
    :data:`STANDARD_CHANNELS`, the frontend ``v2Stream.ts``
    channel-list mismatch must be re-evaluated.
    """
    assert "stalls" not in STANDARD_CHANNELS
    assert "replans" not in STANDARD_CHANNELS


def test_default_sse_channels_is_deterministic_tuple() -> None:
    """The tuple is sorted so OpenAPI snapshots / repr() are stable."""
    assert isinstance(DEFAULT_SSE_CHANNELS, tuple)
    assert list(DEFAULT_SSE_CHANNELS) == sorted(DEFAULT_SSE_CHANNELS)


# ---------------------------------------------------------------------------
# Generator-level contract
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Stand-in for :class:`fastapi.Request` used by the SSE generator."""

    def __init__(self) -> None:
        self.disconnect = asyncio.Event()
        # ``_event_stream`` only touches ``request.is_disconnected``;
        # the ``app.state`` plumbing is validated in
        # ``_build_streaming_response`` upstream.
        self.app = SimpleNamespace(state=SimpleNamespace(org_manager=None))

    async def is_disconnected(self) -> bool:
        return self.disconnect.is_set()


async def test_event_stream_first_chunk_is_retry_directive(monkeypatch) -> None:
    """The first SSE chunk is the spec-defined ``retry:`` directive only.

    v25 RC-3 deletes the synthetic ``lifecycle/sse_connected`` first
    event. The retry directive is still yielded first so reverse
    proxies that buffer until they see body bytes release the
    response headers immediately.
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    request = _FakeRequest()
    gen = _event_stream(request, "org_first_chunk")
    try:
        first = await gen.__anext__()
        assert first == "retry: 3000\n\n", (
            "First SSE chunk must be the retry directive only; got "
            f"{first!r} -- the synthetic sse_connected first event "
            "was deleted in v25 RC-3."
        )
        # The synthetic event was the ONLY other emit before the
        # bus loop; nothing else should arrive before a real event
        # is published.
    finally:
        request.disconnect.set()
        await gen.aclose()


async def test_event_stream_does_not_emit_synthetic_event_before_bus(
    monkeypatch,
) -> None:
    """No synthetic ``sse_connected`` event is forged before the bus loop.

    Drives the generator far enough that, in the old code path,
    the synthetic event would have been yielded after the retry
    directive. With v25 RC-3 the generator should instead block on
    the empty subscriber queue until the test releases it via
    ``disconnect.set()`` -- proven here by asserting the next
    yield, if any, is the keepalive comment ``: ping`` (never a
    forged ``StreamEvent``).
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    request = _FakeRequest()
    gen = _event_stream(request, "org_no_synthetic")
    try:
        first = await gen.__anext__()
        assert first == "retry: 3000\n\n"

        # The next yield must not be a fabricated SSE event. We
        # cannot wait the full 15 s queue timeout in a unit test,
        # so we race against the disconnect path: schedule a
        # disconnect after a short delay, then iterate until the
        # generator exits. Anything yielded in between must be a
        # ``: ping`` comment (never an ``event: ...`` line with a
        # forged payload).
        async def _disconnect_soon() -> None:
            await asyncio.sleep(0.05)
            request.disconnect.set()

        disc_task = asyncio.create_task(_disconnect_soon())
        try:
            async for chunk in gen:
                # Any non-ping yield before the bus emits is a
                # contract violation.
                assert chunk.lstrip().startswith(":"), (
                    "Unexpected pre-bus chunk: " f"{chunk!r}"
                )
        finally:
            disc_task.cancel()
            try:
                await disc_task
            except asyncio.CancelledError:
                pass
    finally:
        await gen.aclose()


async def test_event_stream_forwards_every_non_debug_standard_channel(
    monkeypatch,
) -> None:
    """A bus event on any non-``debug`` standard channel reaches the SSE consumer.

    This is the positive complement of the
    ``DEFAULT_SSE_CHANNELS >= STANDARD_CHANNELS - {debug}`` invariant:
    if the subscription set were wrong, the matching channels'
    events would never appear in the SSE body. Emit one event on
    every covered channel and assert each one arrives, in order.
    """
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    bus = get_or_create_org_stream_bus("org_coverage")
    request = _FakeRequest()
    gen = _event_stream(request, "org_coverage")
    try:
        # Drain the retry directive so the subscription is live.
        first = await gen.__anext__()
        assert first == "retry: 3000\n\n"

        covered = sorted(STANDARD_CHANNELS - {"debug"})
        for ch in covered:
            await bus.emit(
                ch,
                "contract_probe",
                {"channel": ch},
                command_id="cmd_probe",
                org_id="org_coverage",
                superstep=0,
            )

        seen: list[str] = []
        for _ in range(len(covered)):
            chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            # Each chunk is a complete SSE event ending with "\n\n".
            lines = chunk.split("\n")
            event_line = next(
                (line for line in lines if line.startswith("event:")), ""
            )
            data_line = next(
                (line for line in lines if line.startswith("data:")), ""
            )
            assert event_line, f"missing event: line in {chunk!r}"
            assert data_line, f"missing data: line in {chunk!r}"
            channel = event_line[len("event:") :].strip()
            payload = json.loads(data_line[len("data:") :].strip())
            assert payload["type"] == "contract_probe"
            seen.append(channel)
        assert sorted(seen) == covered
    finally:
        request.disconnect.set()
        await gen.aclose()


async def test_event_stream_drops_debug_channel_by_default(monkeypatch) -> None:
    """``debug`` events MUST NOT reach the default SSE subscriber."""
    monkeypatch.setattr(settings, "runtime_v2_enabled", True, raising=False)
    bus = get_or_create_org_stream_bus("org_debug_optout")
    request = _FakeRequest()
    gen = _event_stream(request, "org_debug_optout")
    try:
        first = await gen.__anext__()
        assert first == "retry: 3000\n\n"

        # Emit one debug event (should be dropped by the
        # subscription filter) followed by one lifecycle event
        # (should be delivered). Receive one chunk; it must be the
        # lifecycle event, proving the debug event was filtered
        # out at the bus level, not just queued behind it.
        await bus.emit(
            "debug",
            "debug_probe",
            {"should": "be_dropped"},
            command_id="",
            org_id="org_debug_optout",
            superstep=0,
        )
        await bus.emit(
            "lifecycle",
            "lifecycle_probe",
            {"after": "debug"},
            command_id="",
            org_id="org_debug_optout",
            superstep=0,
        )
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert "event: lifecycle" in chunk
        assert "lifecycle_probe" in chunk
        assert "debug_probe" not in chunk
    finally:
        request.disconnect.set()
        await gen.aclose()
