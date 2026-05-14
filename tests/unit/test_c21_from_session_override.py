"""C21 P1-1: ``PolicyContext.from_session`` reads ``confirmation_mode_override``.

Background
==========

C8 added ``Session.confirmation_mode_override`` as a session-scoped override
(``None`` ⇒ use global). The production main path
``build_policy_context`` (policy_v2/adapter.py) was correctly updated to
honour the override. But the convenience helper
``PolicyContext.from_session`` kept reading
``getattr(session, "confirmation_mode", None)`` — a field that does not
exist on the real ``Session`` class.

The bug was latent because:

- Production code calls ``build_policy_context``, not ``from_session``
- Tests passed because they used a fake ``Session`` whose attribute
  happened to be named ``confirmation_mode``

Audit subagent caught this in the C21 architectural review. C21 P1-1
fixes ``from_session`` to read ``confirmation_mode_override`` first,
with a fallback to ``confirmation_mode`` for test compatibility.

Tests
=====

- Real ``Session`` instance with ``confirmation_mode_override="strict"``
  → ``ctx.confirmation_mode == STRICT``
- Real ``Session`` instance with override left as None → defaults to
  TRUST (the schema default)
- Backward-compatible fake Session using ``confirmation_mode`` field
  still works (skeleton tests in ``test_policy_v2_skeleton.py``
  shouldn't regress)
- Override field takes precedence over legacy field
"""

from __future__ import annotations

from pathlib import Path

from openakita.core.policy_v2.context import PolicyContext
from openakita.core.policy_v2.enums import ConfirmationMode, SessionRole


def _make_session(**overrides):
    """Convenience: real ``Session`` with the boilerplate required fields."""
    from openakita.sessions.session import Session

    base = {
        "id": overrides.pop("id", "s-test"),
        "channel": "desktop",
        "chat_id": "chat-1",
        "user_id": "user-1",
    }
    base.update(overrides)
    return Session(**base)


def test_real_session_override_strict_picked_up() -> None:
    """Real Session class: override = 'strict' must reach PolicyContext."""
    s = _make_session(id="s-strict", confirmation_mode_override="strict")

    ctx = PolicyContext.from_session(s)
    assert ctx.confirmation_mode == ConfirmationMode.STRICT, (
        "from_session must honour Session.confirmation_mode_override. "
        "Pre-C21 P1-1 it read the non-existent 'confirmation_mode' attr "
        "and always returned the default (DEFAULT)."
    )


def test_real_session_override_dont_ask() -> None:
    """The two 'newer' modes (accept_edits / dont_ask) only reachable via
    override — must work."""
    s = _make_session(id="s-dontask", confirmation_mode_override="dont_ask")
    ctx = PolicyContext.from_session(s)
    assert ctx.confirmation_mode == ConfirmationMode.DONT_ASK


def test_real_session_no_override_falls_back_to_default() -> None:
    """Session with override=None uses _coerce_mode's DEFAULT fallback.

    Note: DEFAULT ≠ TRUST. DEFAULT means "consult the global confirmation
    settings"; TRUST is one specific mode. ``_coerce_mode(None) -> DEFAULT``
    is the documented fallback.
    """
    s = _make_session(id="s-noov")
    assert s.confirmation_mode_override is None

    ctx = PolicyContext.from_session(s)
    assert ctx.confirmation_mode == ConfirmationMode.DEFAULT


def test_real_session_session_role_still_works() -> None:
    """session_role is independent of confirmation_mode — sanity check that
    the P1-1 fix didn't break the role field which is read by the same path.

    ``Session.session_role`` is declared as ``str`` (for sessions.json
    back-compat); from_session coerces it back to enum.
    """
    s = _make_session(
        id="s-plan", session_role="plan", confirmation_mode_override="strict"
    )
    ctx = PolicyContext.from_session(s)
    assert ctx.session_role == SessionRole.PLAN
    assert ctx.confirmation_mode == ConfirmationMode.STRICT


class _LegacyFakeSession:
    """Mimics the pre-C8 fake Session used by test_policy_v2_skeleton tests
    that named their attribute ``confirmation_mode`` directly."""

    id = "legacy"
    workspace = "/tmp/work"
    confirmation_mode = "strict"
    metadata: dict = {}


def test_backward_compat_with_confirmation_mode_attribute() -> None:
    """Old-style fake Session uses ``confirmation_mode`` — still supported
    so the existing test_policy_v2_skeleton suite doesn't regress."""
    ctx = PolicyContext.from_session(_LegacyFakeSession())
    assert ctx.confirmation_mode == ConfirmationMode.STRICT


def test_override_takes_precedence_over_legacy_attr() -> None:
    """If a session has both ``confirmation_mode_override`` AND a legacy
    ``confirmation_mode``, the override wins (C21 reading order)."""

    class _MixedSession:
        id = "mixed"
        workspace = "/tmp/work"
        confirmation_mode_override = "strict"
        confirmation_mode = "trust"  # legacy attr, should lose
        metadata: dict = {}

    ctx = PolicyContext.from_session(_MixedSession())
    assert ctx.confirmation_mode == ConfirmationMode.STRICT, (
        "When both attrs are present, confirmation_mode_override must win — "
        "it represents the post-C8 design where the override field is "
        "the canonical session-scoped knob."
    )


def test_override_none_falls_back_to_legacy_attr() -> None:
    """``confirmation_mode_override=None`` is the 'no session override' marker;
    if a legacy attr is present, it should still be honoured."""

    class _PartialSession:
        id = "partial"
        workspace = "/tmp/work"
        confirmation_mode_override = None  # explicitly opted out
        confirmation_mode = "accept_edits"
        metadata: dict = {}

    ctx = PolicyContext.from_session(_PartialSession())
    assert ctx.confirmation_mode == ConfirmationMode.ACCEPT_EDITS


def test_neither_field_present_uses_default() -> None:
    """Truly legacy session with no role / mode attributes at all."""

    class _NoAttrSession:
        id = "ancient"
        workspace = "/tmp/work"
        metadata: dict = {}

    ctx = PolicyContext.from_session(_NoAttrSession())
    assert ctx.confirmation_mode == ConfirmationMode.DEFAULT
    assert ctx.session_role == SessionRole.AGENT
    assert ctx.workspace == Path("/tmp/work")
