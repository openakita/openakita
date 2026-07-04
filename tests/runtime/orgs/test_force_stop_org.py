"""test17 issue A: stop is a FORCE operation and must never fail on the state
machine.

Regression: an org loaded from disk but not (re)activated in this runtime has
``get_org_state() is None``. The old ``stop_org`` ran the strict transition
table (no ``None``/``CREATED`` -> ``STOPPED`` edge) and raised
``IllegalOrgTransition`` -- surfaced to the user as
"停止失败: cannot create-and-transition new org to 'STOPPED'" -- leaving the
running command uncancellable. Force-stop must always cancel in-flight work and
land the STOPPED terminal; only a DELETED (gone) org is refused.
"""

from __future__ import annotations

import asyncio

from openakita.orgs._runtime_lifecycle import (
    STATE_ACTIVE,
    STATE_CREATED,
    STATE_DELETED,
    STATE_STOPPED,
    OrgLifecycleManager,
)


class _FakeState:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._s: dict[str, str] = dict(initial or {})

    async def transition_org_state(self, org_id: str, target: str, *, reason=None) -> bool:
        self._s[org_id] = target
        return True

    def get_org_state(self, org_id: str):
        return self._s.get(org_id)

    def is_org_active(self, org_id: str) -> bool:
        return self._s.get(org_id) == STATE_ACTIVE


class _FakeBus:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, event: str, payload: dict) -> None:
        self.emitted.append((event, payload))

    async def broadcast_ws(self, event: str, payload: dict) -> None:
        self.emitted.append((f"ws:{event}", payload))


def _mgr(initial=None):
    drained: list[tuple[str, str]] = []

    async def on_stop(org_id: str, reason: str) -> None:
        drained.append((org_id, reason))

    state = _FakeState(initial)
    mgr = OrgLifecycleManager(state, _FakeBus(), on_stop_org=on_stop)
    return mgr, state, drained


def test_force_stop_untracked_org_succeeds_and_cancels_inflight() -> None:
    """current=None (never activated in this process) must still stop."""
    mgr, state, drained = _mgr(initial=None)
    ok = asyncio.run(mgr.stop_org("ghost", reason="stop"))
    assert ok is True
    # in-flight drain ran (command cancel) even though state was empty
    assert drained == [("ghost", "stop")]
    # and the org now sits at the STOPPED terminal
    assert state.get_org_state("ghost") == STATE_STOPPED
    assert mgr.is_org_recently_stopped("ghost") is True


def test_force_stop_from_created_state_succeeds() -> None:
    """CREATED -> STOPPED is not in the normal table, but force-stop allows it."""
    mgr, state, _ = _mgr(initial={"o": STATE_CREATED})
    assert asyncio.run(mgr.stop_org("o")) is True
    assert state.get_org_state("o") == STATE_STOPPED


def test_force_stop_is_idempotent_when_already_stopped() -> None:
    mgr, state, _ = _mgr(initial={"o": STATE_STOPPED})
    assert asyncio.run(mgr.stop_org("o")) is True
    assert state.get_org_state("o") == STATE_STOPPED


def test_force_stop_active_org_transitions_and_emits() -> None:
    mgr, state, _ = _mgr(initial={"o": STATE_ACTIVE})
    assert asyncio.run(mgr.stop_org("o")) is True
    assert state.get_org_state("o") == STATE_STOPPED
    assert any(ev == "org_stopped" for ev, _ in mgr._event_bus.emitted)  # type: ignore[attr-defined]


def test_force_stop_refuses_deleted_org() -> None:
    mgr, state, _ = _mgr(initial={"o": STATE_DELETED})
    assert asyncio.run(mgr.stop_org("o")) is False
    assert state.get_org_state("o") == STATE_DELETED
