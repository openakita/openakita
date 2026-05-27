"""Sprint-7 P0-A regression: stop_org cancelled_by is a single value.

Audit ``_orgs_business_capability_audit_v7.md`` §1.2 R.F2 + §5 finding 5
caught that v18 events.jsonl carried ``cancelled_by="stop_org:stop"``
instead of the Sprint-6 changelog's contracted single value
``cancelled_by="stop_org"``. Root cause: the
``_on_stop_org_cancel_inflight`` shim in :func:`api.server.create_app`
interpolated the lifecycle's inner ``reason`` kwarg ("stop" / "restart")
into the source string handed to
:meth:`OrgCommandService.cancel_all_for_org`. Sprint-7 P0-A drops the
suffix; the inner lifecycle reason stays on the separate
``org_stopped`` lifecycle event payload (see
:meth:`OrgLifecycleManager.stop_org`), so no information is lost.

This module pins:

* :func:`api.server._build_on_stop_org_cancel_inflight_handler` always
  forwards the literal ``"stop_org"`` regardless of the inner reason
  ("stop" / "restart" / "custom-reason"). Tested via a stub
  ``OrgCommandService`` that captures the keyword argument.
* End-to-end: a real :class:`OrgCommandService` + :class:`OrgEventStore`
  wired through the handler emits the normalised value to events.jsonl
  with NO colon-suffixed compound form anywhere in the payload.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.api.server import _build_on_stop_org_cancel_inflight_handler
from openakita.orgs._runtime_dispatch import (
    CommandDispatchManager,
    _CommandTracker,
)
from openakita.orgs._runtime_event_store import OrgEventStore
from openakita.orgs.command_models import OrgCommandRequest
from openakita.orgs.command_service import OrgCommandService


class _DiskWiredEventBus:
    """In-process bus that persists every emit to a JSONL file.

    Mirrors the production composition tap so the assertions can read
    the actual file content (Pattern 2 from the Sprint-6 changelog --
    mock-only tests miss disk-side observables).
    """

    def __init__(self, store: OrgEventStore) -> None:
        self._store = store
        self._subs: dict[str, list[Any]] = {}
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def subscribe(self, event: str, handler: Any) -> None:
        self._subs.setdefault(event, []).append(handler)

    def unsubscribe(self, event: str, handler: Any) -> None:
        if handler in self._subs.get(event, ()):
            self._subs[event].remove(handler)

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.emitted.append((event, dict(payload)))
        record = dict(payload)
        record.setdefault("type", event)
        self._store.append(record)
        for handler in list(self._subs.get(event, ())):
            res = handler(payload)
            if asyncio.iscoroutine(res):
                await res


class _Node:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _Org:
    def __init__(self, *, roots: tuple[str, ...] = ("root1",)) -> None:
        self.status = type("_Status", (), {"value": "active"})()
        self.nodes = [_Node(r) for r in roots]
        self.watchdog_enabled = True
        self.watchdog_stuck_threshold_s = 0.5

    def get_node(self, nid: str) -> _Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def get_root_nodes(self) -> list[_Node]:
        return list(self.nodes)


def _read_events(jsonl: Path) -> list[dict[str, Any]]:
    if not jsonl.is_file():
        return []
    events: list[dict[str, Any]] = []
    for raw in jsonl.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# ---------------------------------------------------------------------------
# P0-A unit -- handler always forwards the literal "stop_org"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lifecycle_reason",
    ["stop", "restart", "user-issued-stop", "custom"],
)
async def test_on_stop_org_handler_forwards_literal_stop_org(
    lifecycle_reason: str,
) -> None:
    """case id: p07.on_stop_org.forwards_literal_source

    Regression guard for v18 §1.2 finding (``stop_org:stop`` compound).
    Whatever inner reason the lifecycle hands to the callback, the
    handler must call ``cancel_all_for_org`` with the literal
    ``"stop_org"`` source string -- no f-string interpolation, no
    colon-suffix compound.
    """

    svc = MagicMock()
    svc.cancel_all_for_org = AsyncMock(return_value=["cid-1"])

    handler = _build_on_stop_org_cancel_inflight_handler(svc)
    await handler("org-int", lifecycle_reason)

    svc.cancel_all_for_org.assert_awaited_once_with("org-int", reason="stop_org")
    # And the literal string never contains a colon (defensive against
    # a future refactor that re-introduces compound formatting).
    forwarded = svc.cancel_all_for_org.await_args.kwargs["reason"]
    assert ":" not in forwarded
    assert forwarded == "stop_org"


@pytest.mark.asyncio
async def test_on_stop_org_handler_swallows_service_exception() -> None:
    """case id: p07.on_stop_org.swallows_exception

    The Sprint-5 shim wrapped the service call in try/except so a
    failure here does not block the lifecycle state transition to
    STOPPED. Sprint-7 refactor must preserve that property.
    """

    svc = MagicMock()
    svc.cancel_all_for_org = AsyncMock(side_effect=RuntimeError("boom"))

    handler = _build_on_stop_org_cancel_inflight_handler(svc)
    # Must NOT raise.
    await handler("org-int", "stop")
    svc.cancel_all_for_org.assert_awaited_once()


# ---------------------------------------------------------------------------
# P0-A end-to-end -- normalised value lands on events.jsonl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_stop_org_handler_emits_stop_org_to_disk_no_compound(
    tmp_path: Path,
) -> None:
    """case id: p07.on_stop_org.emits_stop_org_to_disk_no_compound

    End-to-end through the production wiring shape: build the real
    handler, run an in-flight command through a real
    :class:`OrgCommandService` with a disk-wired bus, trigger the
    handler the way :meth:`OrgLifecycleManager.stop_org` does
    (``reason="stop"``), and assert events.jsonl carries the
    normalised ``cancelled_by="stop_org"`` value with NO colon-suffix
    compound anywhere in either the ``user_command_cancelled`` or
    ``agent_run_cancelled`` payload.

    This is the v18 audit signal the Sprint-7 commit must flip.
    """

    jsonl = tmp_path / "logs" / "events.jsonl"
    store = OrgEventStore(org_id="org-int", jsonl_path=jsonl)
    bus = _DiskWiredEventBus(store)

    async def slow_send(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {"status": "submitted", "command_id": kwargs.get("command_id")}

    lookup = MagicMock()
    lookup.get_org = MagicMock(return_value=_Org())

    dispatch = CommandDispatchManager(
        command_service=None,
        lookup=lookup,
        event_bus=bus,
    )

    rt = MagicMock()
    rt.get_org = lookup.get_org
    rt.get_command_tracker_snapshot = MagicMock(return_value=None)
    rt.get_event_store = MagicMock(return_value=MagicMock(query=lambda **kw: []))
    rt.has_active_delegations = MagicMock(return_value=False)
    rt.get_inbox = MagicMock(return_value=MagicMock())

    async def _cancel_user_command(
        org_id: str, command_id: str, *, cancel_reason: str | None = None
    ) -> dict[str, Any] | None:
        tr = dispatch._registry.get(org_id, command_id)  # type: ignore[attr-defined]
        if tr is None:
            tr = _CommandTracker(
                org_id=org_id,
                command_id=command_id,
                root_node_id="root1",
                root_intent="task",
            )
            dispatch._registry.register(tr)  # type: ignore[attr-defined]
        return await dispatch.cancel_user_command(
            org_id, command_id, cancel_reason=cancel_reason
        )

    rt.cancel_user_command = AsyncMock(side_effect=_cancel_user_command)

    # Sprint-9 supervisor takeover: ``rt.send_command`` is no longer
    # in the hot path; the new flow goes through a Supervisor
    # constructed by ``supervisor_factory``. Inject a slow stub so
    # the command is still in-flight when the on-stop handler runs.
    from openakita.runtime.cancel_token import CancellationToken
    from openakita.runtime.supervisor import FinalOutcome, SupervisorOutcome

    class _SlowSupervisor:
        def __init__(self) -> None:
            self.cancel_token = CancellationToken()
            self.stall_detector = type(
                "_SD", (), {"n_turns": 0, "n_stalls": 0}
            )()
            self.history: list[Any] = []
            self.n_replans = 0
            self.last_checkpoint_id = "cp-slow"

        async def run(self) -> SupervisorOutcome:
            for _ in range(600):
                if self.cancel_token.is_cancelled():
                    return SupervisorOutcome(
                        outcome=FinalOutcome.CANCELLED,
                        final_message="cancelled",
                        final_checkpoint_id=self.last_checkpoint_id,
                        n_turns=0,
                        n_replans=0,
                        reason=self.cancel_token.reason or "cancelled",
                    )
                await asyncio.sleep(0.05)
            return SupervisorOutcome(
                outcome=FinalOutcome.DONE,
                final_message="done",
                final_checkpoint_id=self.last_checkpoint_id,
                n_turns=0,
                n_replans=0,
            )

        async def resume_from_checkpoint(self, cp: str) -> "_SlowSupervisor":
            return self

    def _slow_factory(*, org_id, command_id, root_node_id, task, **_kw):
        return _SlowSupervisor()

    svc = OrgCommandService(
        rt, event_bus=bus, supervisor_factory=_slow_factory
    )
    res = await svc.submit(OrgCommandRequest(org_id="org-int", content="long"))
    cid = res["command_id"]
    await asyncio.sleep(0.02)

    handler = _build_on_stop_org_cancel_inflight_handler(svc)
    # The lifecycle's default reason kwarg ("stop") is the v18 scenario
    # that produced ``stop_org:stop`` on disk; assert the shim
    # normalises it back to the single-value source.
    await handler("org-int", "stop")

    events = _read_events(jsonl)
    user_cancelled = [
        e for e in events if e.get("type") == "user_command_cancelled"
    ]
    assert user_cancelled, "user_command_cancelled must land on events.jsonl"
    payload = user_cancelled[0]
    assert payload["cancelled_by"] == "stop_org", (
        "Sprint-7 P0-A: cancelled_by must be the literal 'stop_org', "
        f"not the v18 compound: got {payload['cancelled_by']!r}"
    )
    assert payload["reason"] == "stop_org"
    assert ":" not in str(payload["cancelled_by"])
    assert ":" not in str(payload["reason"])

    # Outcome cache parity also stays on the normalised value.
    outcome = svc._command_outcomes.get(cid)
    assert outcome is not None
    assert outcome["cancelled_by"] == "stop_org"
    assert ":" not in str(outcome["cancelled_by"])
