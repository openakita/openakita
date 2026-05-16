"""Unit tests for `_apply_agent_profile` in `openakita.api.routes.chat`.

The HTTP chat path is what the desktop `/agent` slash command now relies on:
the frontend sets `selectedAgent` and the next `POST /api/chat` carries
`agent_profile_id`, after which `_apply_agent_profile` is supposed to:

1. write the new profile into `session.context.agent_profile_id`;
2. append an entry to `session.context.agent_switch_history`;
3. reject unknown profile ids (so the desktop slash command and IM
   `/切换` give the same semantics).

These cases lock that contract.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openakita.api.routes.chat import _apply_agent_profile
from openakita.sessions.session import SessionContext


def _make_session(profile_id: str = "default") -> SimpleNamespace:
    ctx = SessionContext()
    ctx.agent_profile_id = profile_id
    return SimpleNamespace(context=ctx)


class TestApplyAgentProfileHappyPath:
    def test_switch_to_system_preset_persists_into_context(self):
        session = _make_session(profile_id="default")

        applied = _apply_agent_profile(session, "content-creator")

        assert applied is True
        assert session.context.agent_profile_id == "content-creator"

    def test_switch_records_history_entry_with_from_to_and_timestamp(self):
        session = _make_session(profile_id="default")

        _apply_agent_profile(session, "content-creator")

        history = session.context.agent_switch_history
        assert len(history) == 1
        entry = history[0]
        assert entry["from"] == "default"
        assert entry["to"] == "content-creator"
        assert isinstance(entry["at"], str) and entry["at"]

    def test_switch_to_same_profile_is_noop_no_history(self):
        session = _make_session(profile_id="default")

        applied = _apply_agent_profile(session, "default")

        assert applied is True
        assert session.context.agent_profile_id == "default"
        assert session.context.agent_switch_history == []

    def test_multiple_switches_append_in_order(self):
        session = _make_session(profile_id="default")

        _apply_agent_profile(session, "content-creator")
        _apply_agent_profile(session, "office-doc")
        _apply_agent_profile(session, "default")

        history = session.context.agent_switch_history
        assert [(e["from"], e["to"]) for e in history] == [
            ("default", "content-creator"),
            ("content-creator", "office-doc"),
            ("office-doc", "default"),
        ]
        assert session.context.agent_profile_id == "default"


class TestApplyAgentProfileValidation:
    def test_unknown_profile_id_returns_false_and_does_not_mutate(self):
        session = _make_session(profile_id="default")

        applied = _apply_agent_profile(session, "this-profile-does-not-exist-xyz")

        assert applied is False
        assert session.context.agent_profile_id == "default"
        assert session.context.agent_switch_history == []

    def test_session_without_context_returns_false(self):
        session = SimpleNamespace()  # no `context` attribute at all

        applied = _apply_agent_profile(session, "engineer")

        assert applied is False


class TestApplyAgentProfileCustomStore:
    def test_custom_profile_in_store_is_accepted(self, monkeypatch):
        """Custom profiles (added via the agent manager) must also resolve."""

        from openakita.agents import profile as profile_mod

        class _FakeStore:
            def get(self, profile_id):
                if profile_id == "my-custom-bot":
                    return SimpleNamespace(id="my-custom-bot", name="My Custom Bot")
                return None

        fake_store = _FakeStore()
        monkeypatch.setattr(profile_mod, "get_profile_store", lambda: fake_store)

        session = _make_session(profile_id="default")
        applied = _apply_agent_profile(session, "my-custom-bot")

        assert applied is True
        assert session.context.agent_profile_id == "my-custom-bot"
        assert session.context.agent_switch_history[-1]["to"] == "my-custom-bot"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
