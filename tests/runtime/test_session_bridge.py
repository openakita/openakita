"""Tests for :mod:`openakita.runtime.session_bridge`.

P-RC-1 commit 1. The bridge is a single dependency-injection seam:
the v2 runtime asks ``get_org_id_for_session(session_key)``, the
gateway (registered via ``register_session_org_lookup``) answers
with the org bound to that session. These tests cover the four
scenarios the continuation plan calls out:

* bound       -> registered lookup returns ``"org_abc"``
* unbound     -> registered lookup returns ``None``
* unknown     -> registered lookup raises (treated as ``None``)
* cancelled   -> no lookup registered at all (process-wide default)

Plus a few defensive cases (empty session key, non-string return).
"""

from __future__ import annotations

import pytest

from openakita.runtime.session_bridge import (
    SessionOrgLookup,
    get_org_id_for_session,
    register_session_org_lookup,
    reset_session_org_lookup,
)


@pytest.fixture(autouse=True)
def _clean_lookup() -> None:
    """Make sure each test starts and ends with no registered lookup."""
    reset_session_org_lookup()
    yield
    reset_session_org_lookup()


def test_returns_org_id_when_lookup_resolves_session() -> None:
    """Bound case: lookup returns a real org id."""
    def lookup(session_key: str) -> str | None:
        return "org_abc" if session_key == "telegram:chat:user" else None

    register_session_org_lookup(lookup)
    assert get_org_id_for_session("telegram:chat:user") == "org_abc"


def test_returns_none_when_session_is_unbound() -> None:
    """Unbound case: session exists but no org binding."""
    def lookup(session_key: str) -> str | None:
        return None

    register_session_org_lookup(lookup)
    assert get_org_id_for_session("feishu:chat:user") is None


def test_returns_none_when_lookup_raises() -> None:
    """Unknown-session case: lookup raises (e.g. store crashed) -> None."""
    def lookup(session_key: str) -> str | None:
        raise RuntimeError("session store unavailable")

    register_session_org_lookup(lookup)
    assert get_org_id_for_session("wecom:chat:user") is None


def test_returns_none_when_no_lookup_registered() -> None:
    """Cancelled / pre-registration case: never call into nothing."""
    # reset_session_org_lookup already cleared the slot via the fixture.
    assert get_org_id_for_session("dingtalk:chat:user") is None


def test_returns_none_for_empty_session_key() -> None:
    """Defensive: empty key cannot map to an org."""
    def lookup(session_key: str) -> str | None:
        return "org_should_not_be_returned"

    register_session_org_lookup(lookup)
    assert get_org_id_for_session("") is None


def test_returns_none_when_lookup_returns_non_string() -> None:
    """Defensive: lookup must hand back a string; anything else -> None."""
    def lookup(session_key: str) -> object:  # type: ignore[return-value]
        return 12345  # bogus type

    register_session_org_lookup(lookup)  # type: ignore[arg-type]
    assert get_org_id_for_session("qq:chat:user") is None


def test_register_overwrites_previous_lookup() -> None:
    """Last-writer-wins -- the gateway can be re-constructed in tests."""
    register_session_org_lookup(lambda key: "first")
    register_session_org_lookup(lambda key: "second")
    assert get_org_id_for_session("sess") == "second"


def test_protocol_isinstance_check() -> None:
    """The runtime-checkable Protocol accepts a plain callable."""
    def lookup(session_key: str) -> str | None:
        return None

    assert isinstance(lookup, SessionOrgLookup)

# ---------------------------------------------------------------------------
# P-RC-2: cold-session rehydration in MessageGateway._lookup_org_id_for_session
# (closes G-RC-1 residual risk #3)
# ---------------------------------------------------------------------------


class _FakeSession:
    """Tiny stand-in for ``openakita.sessions.session.Session``.

    The lookup only ever calls ``get_metadata(key)`` so we keep the
    surface minimal. Real :class:`Session` is heavy (touches disk
    paths, lock fixtures) and not worth wiring up for one method.
    """

    def __init__(self, *, bound_org_id: str | None) -> None:
        self._meta = {"bound_org_id": bound_org_id} if bound_org_id else {}

    def get_metadata(self, key: str) -> str | None:
        return self._meta.get(key)


class _FakeSessionManager:
    def __init__(
        self,
        *,
        hot: dict[str, _FakeSession] | None = None,
        cold: dict[str, _FakeSession] | None = None,
        recover_raises: bool = False,
    ) -> None:
        self._sessions: dict[str, _FakeSession] = dict(hot or {})
        self._cold = dict(cold or {})
        self._recover_raises = recover_raises
        self.recover_calls: list[str] = []

    def _try_recover_session_from_disk(self, session_key: str) -> _FakeSession | None:
        self.recover_calls.append(session_key)
        if self._recover_raises:
            raise RuntimeError("disk corruption simulated")
        return self._cold.get(session_key)


def _make_lookup(sm: _FakeSessionManager):
    """Construct a bare ``MessageGateway`` and bind the fake manager.

    We deliberately bypass ``MessageGateway.__init__`` because the
    real constructor wires registries, queues, and adapter slots we
    do not need for this lookup-only test. ``__new__`` gives us an
    instance whose only attribute we touch is ``session_manager``.
    """
    from openakita.channels.gateway import MessageGateway

    gw = MessageGateway.__new__(MessageGateway)
    gw.session_manager = sm  # type: ignore[assignment]
    return gw._lookup_org_id_for_session


def test_lookup_returns_org_id_from_hot_session() -> None:
    """Sanity: warm path still works (the existing P-RC-1 case)."""
    sm = _FakeSessionManager(
        hot={"telegram:chat:user": _FakeSession(bound_org_id="org_warm")},
    )
    lookup = _make_lookup(sm)
    assert lookup("telegram:chat:user") == "org_warm"
    # Warm hits must not call into the disk recovery path.
    assert sm.recover_calls == []


def test_lookup_rehydrates_cold_session_from_disk() -> None:
    """Cold path: session not in ``_sessions`` -> recover from disk -> org_id."""
    sm = _FakeSessionManager(
        hot={},
        cold={"feishu:chat:user": _FakeSession(bound_org_id="org_cold")},
    )
    lookup = _make_lookup(sm)
    assert lookup("feishu:chat:user") == "org_cold"
    assert sm.recover_calls == ["feishu:chat:user"]


def test_lookup_returns_none_for_cold_unbound_session() -> None:
    """Cold session exists on disk but is not bound to any org."""
    sm = _FakeSessionManager(
        hot={},
        cold={"wecom:chat:user": _FakeSession(bound_org_id=None)},
    )
    lookup = _make_lookup(sm)
    assert lookup("wecom:chat:user") is None


def test_lookup_returns_none_when_disk_recovery_misses() -> None:
    """Cold path that finds nothing on disk must return None, not crash."""
    sm = _FakeSessionManager(hot={}, cold={})
    lookup = _make_lookup(sm)
    assert lookup("qq:chat:user") is None
    assert sm.recover_calls == ["qq:chat:user"]


def test_lookup_swallows_disk_recovery_exception() -> None:
    """The runtime contract forbids the lookup from raising."""
    sm = _FakeSessionManager(hot={}, cold={}, recover_raises=True)
    lookup = _make_lookup(sm)
    assert lookup("dingtalk:chat:user") is None


def test_lookup_handles_missing_recover_helper() -> None:
    """Older session managers without the disk-recovery helper still work."""

    class _NoRecover:
        _sessions: dict[str, _FakeSession] = {}

    from openakita.channels.gateway import MessageGateway

    gw = MessageGateway.__new__(MessageGateway)
    gw.session_manager = _NoRecover()  # type: ignore[assignment]
    assert gw._lookup_org_id_for_session("any:key") is None
