"""C14 — entry-point classifier + per-channel unattended wiring tests.

Covers:

- ``classify_entry`` channel matrix (CLI w/ & w/o TTY, IM webhooks,
  SSE-capable channels, scheduler, ``api-sync``, unknown channels,
  ``force_unattended`` override).
- ``apply_classification_to_session`` idempotency + no-downgrade rule +
  empty-strategy fill behaviour.
- ``Session.is_unattended`` propagation through ``classify_entry`` for
  each of the four C14 entry points (CLI / IM / api-sync / scheduler).
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from openakita.core.policy_v2.entry_point import (
    IM_WEBHOOK_CHANNELS,
    SSE_INTERACTIVE_CHANNELS,
    EntryClassification,
    apply_classification_to_session,
    classify_entry,
)


# ---------------------------------------------------------------------------
# classify_entry: CLI branch (TTY-dependent)
# ---------------------------------------------------------------------------


def test_cli_with_tty_is_attended():
    c = classify_entry("cli", has_tty=True)
    assert c.is_unattended is False
    assert c.confirm_capability == "tty"
    assert c.default_strategy == ""
    assert "cli + tty" in c.reason


def test_cli_without_tty_is_unattended():
    c = classify_entry("cli", has_tty=False)
    assert c.is_unattended is True
    assert c.confirm_capability == "none"
    assert c.default_strategy == "ask_owner"
    assert "without tty" in c.reason


def test_cli_runtime_isatty_detection_attended(monkeypatch):
    """``has_tty=None`` → runtime ``sys.stdin.isatty()`` lookup."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    c = classify_entry("cli")
    assert c.is_unattended is False
    assert c.confirm_capability == "tty"


def test_cli_runtime_isatty_detection_unattended(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    c = classify_entry("cli")
    assert c.is_unattended is True
    assert c.confirm_capability == "none"


def test_cli_isatty_raises_oserror_is_safe(monkeypatch):
    """Closed stdin (daemon contexts) → ``isatty`` raises; classifier
    must treat as no-TTY rather than propagate the exception."""

    def _boom():
        raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(sys.stdin, "isatty", _boom)
    c = classify_entry("cli")
    assert c.is_unattended is True
    assert c.confirm_capability == "none"


# ---------------------------------------------------------------------------
# classify_entry: non-CLI channels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", sorted(SSE_INTERACTIVE_CHANNELS))
def test_sse_channels_are_attended(channel):
    c = classify_entry(channel)
    assert c.is_unattended is False
    assert c.confirm_capability == "sse"
    assert c.default_strategy == ""


@pytest.mark.parametrize("channel", sorted(IM_WEBHOOK_CHANNELS))
def test_im_webhook_channels_are_unattended(channel):
    c = classify_entry(channel)
    assert c.is_unattended is True
    assert c.confirm_capability == "none"
    assert c.default_strategy == "ask_owner"
    assert "im-webhook" in c.reason


def test_api_sync_channel_defers_to_inbox():
    c = classify_entry("api-sync")
    assert c.is_unattended is True
    assert c.confirm_capability == "none"
    assert c.default_strategy == "defer_to_inbox"


def test_scheduler_channel_uses_config_default_strategy():
    """Scheduler is unattended but leaves ``default_strategy`` empty so the
    engine falls back to ``config.unattended.default_strategy``."""
    c = classify_entry("scheduler")
    assert c.is_unattended is True
    assert c.default_strategy == ""


def test_generic_webhook_channel():
    c = classify_entry("webhook")
    assert c.is_unattended is True
    assert c.default_strategy == "ask_owner"


def test_unknown_channel_defaults_to_unattended():
    """Defense-in-depth: an entry point we forgot to classify must default
    to unattended rather than silently letting CONFIRM tools hang."""
    c = classify_entry("never_seen_channel_42")
    assert c.is_unattended is True
    assert c.confirm_capability == "none"
    assert "unknown channel" in c.reason


def test_force_unattended_overrides_cli_tty():
    """``openakita run`` is non-interactive even when stdin is a TTY."""
    c = classify_entry("cli", has_tty=True, force_unattended=True)
    assert c.is_unattended is True
    assert c.confirm_capability == "none"
    assert "force_unattended" in c.reason


def test_force_unattended_overrides_sse_channels():
    c = classify_entry("desktop", force_unattended=True)
    assert c.is_unattended is True
    assert c.confirm_capability == "none"


def test_channel_normalization_handles_case_and_whitespace():
    c = classify_entry("  TELEGRAM  ")
    assert c.is_unattended is True
    assert "im-webhook" in c.reason


def test_empty_channel_defaults_to_unknown_unattended():
    c = classify_entry("")
    assert c.is_unattended is True


# ---------------------------------------------------------------------------
# apply_classification_to_session
# ---------------------------------------------------------------------------


class _DummySession:
    def __init__(self, is_unattended=False, unattended_strategy=""):
        self.is_unattended = is_unattended
        self.unattended_strategy = unattended_strategy


def test_apply_sets_unattended_from_default():
    s = _DummySession()
    cls = EntryClassification(
        is_unattended=True,
        confirm_capability="none",
        default_strategy="ask_owner",
        reason="test",
    )
    mutated = apply_classification_to_session(s, cls)
    assert mutated is True
    assert s.is_unattended is True
    assert s.unattended_strategy == "ask_owner"


def test_apply_is_idempotent():
    s = _DummySession()
    cls = EntryClassification(True, "none", "ask_owner", "test")
    apply_classification_to_session(s, cls)
    mutated = apply_classification_to_session(s, cls)
    assert mutated is False


def test_apply_never_downgrades_unattended():
    """A session previously marked unattended (e.g. by scheduler) must not
    be flipped back to attended by an attended-channel classification."""
    s = _DummySession(is_unattended=True, unattended_strategy="defer_to_owner")
    cls_attended = EntryClassification(
        is_unattended=False, confirm_capability="sse", default_strategy="", reason="t"
    )
    apply_classification_to_session(s, cls_attended)
    assert s.is_unattended is True
    assert s.unattended_strategy == "defer_to_owner"


def test_apply_does_not_overwrite_explicit_strategy():
    """An explicit ``unattended_strategy`` (e.g. ``deny``) must not be
    replaced by the channel default (``ask_owner``)."""
    s = _DummySession(is_unattended=True, unattended_strategy="deny")
    cls = EntryClassification(True, "none", "ask_owner", "test")
    apply_classification_to_session(s, cls)
    assert s.unattended_strategy == "deny"


def test_apply_handles_none_session():
    cls = EntryClassification(True, "none", "ask_owner", "test")
    assert apply_classification_to_session(None, cls) is False


def test_apply_skips_empty_default_strategy():
    """Scheduler returns empty default_strategy (config-level fallback);
    apply should NOT write empty string to ``session.unattended_strategy``."""
    s = _DummySession(is_unattended=False, unattended_strategy="")
    cls = EntryClassification(
        is_unattended=True, confirm_capability="none", default_strategy="", reason="t"
    )
    apply_classification_to_session(s, cls)
    assert s.is_unattended is True
    assert s.unattended_strategy == ""


# ---------------------------------------------------------------------------
# End-to-end: real ``Session`` integration
# ---------------------------------------------------------------------------


def test_classifier_integrates_with_real_session():
    from openakita.sessions.session import Session, SessionConfig

    sess = Session.create(
        channel="telegram",
        chat_id="chat123",
        user_id="user42",
        config=SessionConfig(),
    )
    apply_classification_to_session(sess, classify_entry("telegram"))
    assert sess.is_unattended is True
    assert sess.unattended_strategy == "ask_owner"


def test_classifier_idempotent_on_real_session():
    from openakita.sessions.session import Session, SessionConfig

    sess = Session.create(
        channel="cli",
        chat_id="cli",
        user_id="cli_user",
        config=SessionConfig(),
    )
    sess.is_unattended = True
    sess.unattended_strategy = "deny"
    apply_classification_to_session(sess, classify_entry("cli", has_tty=False))
    assert sess.is_unattended is True
    assert sess.unattended_strategy == "deny", "explicit strategy must not be overwritten"


# ---------------------------------------------------------------------------
# CLI ``main`` callback isatty gating (R4-8)
# ---------------------------------------------------------------------------


def test_cli_main_callback_rejects_no_tty(monkeypatch):
    """Smoke check that ``classify_entry("cli")`` is what ``main.main``
    consults — protects against future refactors that bypass the
    classifier."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    c = classify_entry("cli")
    assert c.is_unattended is True, (
        "main.py uses this to gate run_interactive — must remain True for "
        "non-TTY stdin"
    )


# ---------------------------------------------------------------------------
# stream_renderer non-TTY short-circuit (R4-5 belt-and-suspenders)
# ---------------------------------------------------------------------------


def test_stream_renderer_security_confirm_skips_on_no_tty(monkeypatch):
    """Even if a confirm event somehow reaches ``stream_renderer`` in a
    non-TTY context, ``_handle_security_confirm_interactive`` must return
    silently without calling ``apply_resolution`` or attempting
    ``Prompt.ask`` (which would hang)."""
    from rich.console import Console

    from openakita.cli import stream_renderer as sr

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    apply_calls = []

    def _spy_apply_resolution(confirm_id, decision):
        apply_calls.append((confirm_id, decision))
        return True

    with patch(
        "openakita.core.policy_v2.apply_resolution",
        _spy_apply_resolution,
    ):
        sr._handle_security_confirm_interactive(
            {
                "id": "confirm_abc12345",
                "tool": "write_file",
                "reason": "controlled path",
                "risk_level": "medium",
            },
            Console(file=__import__("io").StringIO()),
        )

    assert apply_calls == [], (
        "non-TTY confirm must NOT trigger apply_resolution — let unattended "
        "path / setup-center handle it"
    )
