"""H4 regression: dispatch event-bus bridges to OrgEventStore + StreamBus.

The audit at ``_orgs_business_capability_audit_v1.md`` §3.2 P0
showed three event surfaces drifted apart: dispatch wrote to an
in-process ``_InMemoryEventBus`` with zero subscribers; the SSE
route read from a per-org ``StreamBus`` that nobody published to;
the events.jsonl persistence layer (``OrgEventStore``) was wired
into the REST surface but never appended to. Net effect: 24 mint
orgs all had 0-line events.jsonl files and SSE consumers only saw
``: ping``.

The fix installs two wildcard taps on the runtime's
``_InMemoryEventBus`` so every dispatch event (and every executor
event sharing the same bus) is both persisted to the org's
``OrgEventStore`` and forwarded to its long-lived ``StreamBus``.
These tests pin:

* The persist tap appends to the per-org event store.
* The stream tap forwards to ``StreamBus`` on the ``lifecycle``
  channel (one of the four channels the SSE route subscribes to).
* A failing sink does NOT poison the dispatch loop (best-effort
  bridges).
* The ``add_tap`` surface exists on the default in-memory bus.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openakita.orgs.runtime import OrgRuntime, _InMemoryEventBus
from openakita.runtime import stream_registry


class _Org:
    def __init__(self, org_id: str) -> None:
        self.id = org_id
        self.state = "active"


class _Lookup:
    def get_org(self, org_id: str) -> Any:
        return _Org(org_id)


def _make_runtime() -> OrgRuntime:
    return OrgRuntime(
        lookup=_Lookup(),
        persistence=object(),
        lifecycle_emitter=object(),
    )


@pytest.fixture(autouse=True)
def _reset_stream_registry() -> None:
    """Drop any lingering per-org StreamBus instances between tests."""

    stream_registry.reset_org_stream_buses()


def test_event_bus_default_backend_has_add_tap_surface() -> None:
    """case id: h4.event_bus.add_tap_surface"""

    bus = _InMemoryEventBus()
    assert hasattr(bus, "add_tap")
    assert callable(bus.add_tap)
    assert hasattr(bus, "remove_tap")


def test_event_store_command_id_filter() -> None:
    """case id: events.query.command_id_filter (low-pri follow-up)

    ``/activity`` + ``/events`` accept ``command_id`` so the backend (not just
    the UI) can scope a feed to a single command's events.
    """
    from openakita.orgs._runtime_event_store import OrgEventStore

    store = OrgEventStore("o-cmd")
    store.append({"type": "a", "command_id": "cmd-1"})
    store.append({"type": "b", "command_id": "cmd-2"})
    store.append({"type": "c", "command_id": "cmd-1"})
    only1 = store.query(command_id="cmd-1", limit=50)
    assert [e["type"] for e in only1] == ["a", "c"]
    assert store.query(command_id="cmd-2", limit=50)[0]["type"] == "b"
    assert len(store.query(limit=50)) == 3  # no filter -> all


def test_persist_tap_appends_dispatch_event_to_org_event_store() -> None:
    """case id: h4.persist_tap.events_land_on_store

    Pre-fix the audit's evidence: 24 org dirs, 0 events.jsonl rows.
    A single ``send_command`` must now surface ``user_command_submitted``
    on the org's ``OrgEventStore``.
    """

    rt = _make_runtime()
    asyncio.run(rt.send_command("o-h4", "n1", "ping"))
    store = rt.get_event_store("o-h4")
    assert store is not None
    events = store.query(limit=20)
    types = [e.get("type") or e.get("event_type") for e in events]
    assert "user_command_submitted" in types


def test_stream_tap_forwards_dispatch_event_to_stream_bus() -> None:
    """case id: h4.stream_tap.events_reach_stream_bus

    The SSE route at ``api/routes/orgs_v2_stream.py`` subscribes to
    the ``lifecycle`` / ``messages`` / ``tasks`` / ``progress_ledger``
    channels on the per-org StreamBus. The bridge emits on the
    ``lifecycle`` channel so the live front-end timeline sees real
    events instead of only ``: ping``.
    """

    rt = _make_runtime()
    bus = stream_registry.get_or_create_org_stream_bus("o-h4-sse")
    sub = bus.make_subscription(("lifecycle",), drain_on_close=False)

    async def main() -> Any:
        await bus.register_subscription(sub)
        await rt.send_command("o-h4-sse", "n1", "ping")
        return await asyncio.wait_for(sub.queue.get(), timeout=1.0)

    event = asyncio.run(main())
    assert event.type == "user_command_submitted"
    assert event.org_id == "o-h4-sse"


def test_tap_exception_does_not_poison_dispatch() -> None:
    """case id: h4.tap.exception_isolated

    A storage / network failure on either sink must not crash the
    dispatch background task. The audit explicitly called this out
    as a hard requirement before merging.
    """

    bus = _InMemoryEventBus()

    def bad_tap(event_name: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("sink offline")

    bus.add_tap(bad_tap)
    asyncio.run(bus.emit("user_command_submitted", {"org_id": "o", "command_id": "c"}))


def test_remove_tap_is_idempotent() -> None:
    """case id: h4.tap.remove_idempotent"""

    bus = _InMemoryEventBus()

    def t(event_name: str, payload: dict[str, Any]) -> None:
        return None

    bus.add_tap(t)
    bus.remove_tap(t)
    bus.remove_tap(t)  # no-op the second time


def test_tap_skips_payload_without_org_id() -> None:
    """case id: h4.tap.requires_org_id

    Both bridges key off ``payload['org_id']``. Synthetic events
    that omit it (e.g. internal telemetry) must be skipped silently
    instead of registering a store under an empty / missing id.
    """

    rt = _make_runtime()

    async def main() -> None:
        await rt._event_bus.emit("anonymous_event", {"k": "v"})

    asyncio.run(main())
    # No org_id => persist tap is a no-op; the runtime's internal
    # event-store cache stays empty for this synthetic event.
    assert rt._event_stores == {}
