"""L3 Integration Tests: SessionManager lifecycle."""

import pytest
from pathlib import Path

from openakita.sessions.manager import SessionManager
from openakita.sessions.session import SessionState


@pytest.fixture
def session_mgr(tmp_workspace):
    return SessionManager(storage_path=tmp_workspace / "data" / "sessions")


class TestSessionManagerCreate:
    def test_get_creates_session(self, session_mgr):
        session = session_mgr.get_session("cli", "chat-1", "user-1")
        assert session is not None
        assert session.channel == "cli"
        assert session.chat_id == "chat-1"
        assert session.state == SessionState.ACTIVE

    def test_same_chat_reuses_session(self, session_mgr):
        s1 = session_mgr.get_session("cli", "chat-1", "user-1")
        s2 = session_mgr.get_session("cli", "chat-1", "user-1")
        assert s1.id == s2.id

    def test_different_chat_creates_new(self, session_mgr):
        s1 = session_mgr.get_session("cli", "chat-1", "user-1")
        s2 = session_mgr.get_session("cli", "chat-2", "user-1")
        assert s1.id != s2.id

    def test_different_channel_creates_new(self, session_mgr):
        s1 = session_mgr.get_session("cli", "chat-1", "user-1")
        s2 = session_mgr.get_session("telegram", "chat-1", "user-1")
        assert s1.id != s2.id

    def test_create_if_missing_false(self, session_mgr):
        session = session_mgr.get_session("cli", "nonexistent", "u1", create_if_missing=False)
        assert session is None
