"""L1 Unit Tests: Session state machine, message management."""

import pytest
from datetime import datetime, timedelta

from openakita.sessions.session import Session, SessionConfig, SessionContext, SessionState


class TestSessionCreation:
    def test_default_state_is_active(self):
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        assert s.state == SessionState.ACTIVE

    def test_context_starts_empty(self):
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        assert s.context.messages == []
        assert s.context.current_task is None

    def test_custom_channel(self):
        s = Session(id="s1", channel="telegram", chat_id="tg-123", user_id="u1")
        assert s.channel == "telegram"
        assert s.chat_id == "tg-123"


class TestSessionState:
    def test_all_states_exist(self):
        assert SessionState.ACTIVE.value == "active"
        assert SessionState.IDLE.value == "idle"
        assert SessionState.EXPIRED.value == "expired"
        assert SessionState.CLOSED.value == "closed"

    def test_state_is_settable(self):
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        s.state = SessionState.IDLE
        assert s.state == SessionState.IDLE
        s.state = SessionState.EXPIRED
        assert s.state == SessionState.EXPIRED


class TestSessionContext:
    def test_add_messages(self):
        ctx = SessionContext()
        ctx.messages.append({"role": "user", "content": "hello"})
        ctx.messages.append({"role": "assistant", "content": "hi"})
        assert len(ctx.messages) == 2

    def test_variables_dict(self):
        ctx = SessionContext()
        ctx.variables["key"] = "value"
        assert ctx.variables["key"] == "value"

    def test_task_lifecycle(self):
        ctx = SessionContext()
        assert ctx.current_task is None
        ctx.current_task = "Write a poem"
        assert ctx.current_task == "Write a poem"
        ctx.current_task = None
        assert ctx.current_task is None

    def test_summary_field(self):
        ctx = SessionContext()
        assert ctx.summary is None
        ctx.summary = "User asked about Python"
        assert ctx.summary == "User asked about Python"


class TestSessionConfig:
    def test_default_config(self):
        config = SessionConfig()
        assert isinstance(config, SessionConfig)


class TestSessionTimestamps:
    def test_created_at_set(self):
        before = datetime.now()
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        after = datetime.now()
        assert before <= s.created_at <= after

    def test_last_active_set(self):
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        assert s.last_active is not None

