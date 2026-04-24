"""
Tests for session persistence and context recovery.

Covers:
- Fix-1: Session no longer expires after 30 minutes
- Fix-2: turn_index continuity across start_session calls
- Fix-3: getChatHistory SQLite fallback
- Fix-4: search_memory uses RetrievalEngine
- Fix-5: Session backfill from SQLite on restart
"""

import json
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_manager(tmp_path, **kwargs):
    from openakita.sessions import SessionManager
    return SessionManager(storage_path=tmp_path, **kwargs)


def _make_unified_store(tmp_path):
    from openakita.memory.unified_store import UnifiedStore
    db_path = tmp_path / "test.db"
    return UnifiedStore(db_path=db_path)


# ===========================================================================
# Fix-1: Session no longer expires after 30 minutes
# ===========================================================================

class TestNoSessionExpiry:
    """Session should persist across long idle periods."""

    def test_session_survives_30_minutes(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        session = sm.get_session("telegram", "123", "user1")
        session.add_message("user", "hello")

        session.last_active = datetime.now() - timedelta(minutes=45)

        recovered = sm.get_session("telegram", "123", "user1")
        assert recovered is session
        assert len(recovered.context.messages) == 1

    def test_session_survives_hours(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        session = sm.get_session("telegram", "123", "user1")
        session.add_message("user", "hello")

        session.last_active = datetime.now() - timedelta(hours=5)

        recovered = sm.get_session("telegram", "123", "user1")
        assert recovered is session
        assert recovered.context.messages[0]["content"] == "hello"

    def test_session_expires_after_30_days(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        session = sm.get_session("telegram", "123", "user1")

        session.last_active = datetime.now() - timedelta(days=31)

        assert session.is_expired()

    def test_load_sessions_preserves_messages(self, tmp_path):
        """_load_sessions should NOT clear messages for stale sessions."""
        from openakita.sessions.session import Session, SessionConfig, SessionContext

        session = Session(
            id="test_id",
            channel="telegram",
            chat_id="123",
            user_id="user1",
            last_active=datetime.now() - timedelta(hours=3),
            context=SessionContext(messages=[
                {"role": "user", "content": "old message", "timestamp": "2026-01-01T00:00:00"},
            ]),
        )
        sessions_file = tmp_path / "sessions.json"
        sessions_file.write_text(json.dumps([session.to_dict()], ensure_ascii=False), encoding="utf-8")

        sm = _make_session_manager(tmp_path)
        loaded = sm.get_session("telegram", "123", "user1", create_if_missing=False)
        assert loaded is not None
        assert len(loaded.context.messages) == 1
        assert loaded.context.messages[0]["content"] == "old message"


# ===========================================================================
# Fix-1 continued: max_history still works
# ===========================================================================

class TestMaxHistoryTruncation:

    def test_truncation_fires_at_limit(self, tmp_path):
        from openakita.sessions.session import Session, SessionConfig

        session = Session(
            id="trunc_test",
            channel="test",
            chat_id="1",
            user_id="u",
            config=SessionConfig(max_history=10),
        )
        for i in range(15):
            session.add_message("user" if i % 2 == 0 else "assistant", f"msg {i}")

        assert len(session.context.messages) <= 10


# ===========================================================================
# Fix-2: turn_index continuity
# ===========================================================================

class TestTurnIndexContinuity:

    def test_get_max_turn_index_empty(self, tmp_path):
        store = _make_unified_store(tmp_path)
        assert store.get_max_turn_index("nonexistent") == 0

    def test_get_max_turn_index_after_saves(self, tmp_path):
        store = _make_unified_store(tmp_path)
        for i in range(5):
            store.save_turn(session_id="s1", turn_index=i, role="user", content=f"msg {i}")
        assert store.get_max_turn_index("s1") == 5

    def test_start_session_loads_offset(self, tmp_path):
        store = _make_unified_store(tmp_path)
        for i in range(3):
            store.save_turn(session_id="telegram__123__user1", turn_index=i, role="user", content=f"m{i}")

        from openakita.memory.manager import MemoryManager
        mm = MemoryManager.__new__(MemoryManager)
        mm.store = store
        mm._current_session_id = None
        mm._session_turns = []
        mm._recent_messages = []
        mm._turn_offset = 0

        mm.start_session("telegram__123__user1")

        assert mm._turn_offset == 3

    def test_record_turn_uses_offset(self, tmp_path):
        store = _make_unified_store(tmp_path)
        for i in range(3):
            store.save_turn(session_id="s1", turn_index=i, role="user", content=f"old{i}")

        from openakita.memory.manager import MemoryManager
        from openakita.memory.consolidator import MemoryConsolidator

        mm = MemoryManager.__new__(MemoryManager)
        mm.store = store
        mm._current_session_id = None
        mm._session_turns = []
        mm._recent_messages = []
        mm._turn_offset = 0
        mm._memories_lock = __import__("threading").Lock()
        mm.consolidator = MagicMock(spec=MemoryConsolidator)
        mm.extractor = MagicMock()

        mm.start_session("s1")
        assert mm._turn_offset == 3

        mm.record_turn("user", "new message")

        turns = store.get_session_turns("s1")
        indices = [t["turn_index"] for t in turns]
        assert 3 in indices
        assert turns[-1]["content"] == "new message"

    def test_no_overwrite_of_old_turns(self, tmp_path):
        store = _make_unified_store(tmp_path)
        store.save_turn(session_id="s1", turn_index=0, role="user", content="original")

        from openakita.memory.manager import MemoryManager
        from openakita.memory.consolidator import MemoryConsolidator

        mm = MemoryManager.__new__(MemoryManager)
        mm.store = store
        mm._current_session_id = None
        mm._session_turns = []
        mm._recent_messages = []
        mm._turn_offset = 0
        mm._memories_lock = __import__("threading").Lock()
        mm.consolidator = MagicMock(spec=MemoryConsolidator)
        mm.extractor = MagicMock()

        mm.start_session("s1")
        mm.record_turn("user", "after restart")

        turns = store.get_session_turns("s1")
        contents = [t["content"] for t in turns]
        assert "original" in contents
        assert "after restart" in contents


# ===========================================================================
# Fix-2 continued: get_recent_turns
# ===========================================================================

class TestGetRecentTurns:

    def test_returns_chronological_order(self, tmp_path):
        store = _make_unified_store(tmp_path)
        for i in range(10):
            store.save_turn(session_id="s1", turn_index=i, role="user", content=f"msg{i}")
        recent = store.get_recent_turns("s1", limit=3)
        assert len(recent) == 3
        assert recent[0]["content"] == "msg7"
        assert recent[-1]["content"] == "msg9"

    def test_empty_session(self, tmp_path):
        store = _make_unified_store(tmp_path)
        assert store.get_recent_turns("nope") == []


# ===========================================================================
# Fix-3: getChatHistory SQLite fallback
# ===========================================================================

class TestGetChatHistoryFallback:

    def test_fallback_method_returns_none_without_memory_manager(self):
        from openakita.tools.handlers.im_channel import IMChannelHandler

        agent = MagicMock()
        agent.memory_manager = None
        handler = IMChannelHandler(agent)

        session = MagicMock()
        session.session_key = "telegram:123:user1"
        result = handler._fallback_history_from_sqlite(session, 20)
        assert result is None

    def test_fallback_method_returns_data(self, tmp_path):
        from openakita.tools.handlers.im_channel import IMChannelHandler

        store = _make_unified_store(tmp_path)
        store.save_turn(session_id="telegram__123__user1", turn_index=0, role="user", content="hello world")
        store.save_turn(session_id="telegram__123__user1", turn_index=1, role="assistant", content="hi there")

        mm = MagicMock()
        mm.store = store
        agent = MagicMock()
        agent.memory_manager = mm

        handler = IMChannelHandler(agent)
        session = MagicMock()
        session.session_key = "telegram:123:user1"

        result = handler._fallback_history_from_sqlite(session, 20)
        assert result is not None
        assert "hello world" in result
        assert "hi there" in result
        assert "持久化存储恢复" in result

    def test_fallback_safe_id_sanitization(self, tmp_path):
        from openakita.tools.handlers.im_channel import IMChannelHandler

        store = _make_unified_store(tmp_path)
        store.save_turn(session_id="desktop__conv_123__desktop_user", turn_index=0,
                        role="user", content="test msg")

        mm = MagicMock()
        mm.store = store
        agent = MagicMock()
        agent.memory_manager = mm

        handler = IMChannelHandler(agent)
        session = MagicMock()
        session.session_key = "desktop:conv_123:desktop_user"

        result = handler._fallback_history_from_sqlite(session, 20)
        assert result is not None
        assert "test msg" in result


# ===========================================================================
# Fix-4: search_memory uses RetrievalEngine
# ===========================================================================

class TestSearchMemorySemantic:

    def test_uses_retrieval_engine_when_available(self):
        from openakita.tools.handlers.memory import MemoryHandler
        from openakita.memory.retrieval import RetrievalCandidate

        candidate = RetrievalCandidate(
            content="用户喜欢Python编程",
            source_type="semantic",
            relevance=0.9,
        )
        engine = MagicMock()
        engine.retrieve_candidates.return_value = [candidate]

        mm = MagicMock()
        mm.retrieval_engine = engine
        mm._recent_messages = []

        agent = MagicMock()
        agent.memory_manager = mm

        handler = MemoryHandler(agent)
        result = handler._search_memory({"query": "用户的编程偏好"})
        assert "Python编程" in result
        assert "semantic" in result or "Python编程" in result
        engine.retrieve_candidates.assert_called_once()

    def test_falls_back_to_substring_on_no_engine(self):
        from openakita.tools.handlers.memory import MemoryHandler
        from openakita.memory.types import Memory, MemoryType

        mm = MagicMock()
        mm.retrieval_engine = None
        mm.search_memories.return_value = [
            Memory(content="用户喜欢红色", type=MemoryType.PREFERENCE),
        ]

        agent = MagicMock()
        agent.memory_manager = mm

        handler = MemoryHandler(agent)
        result = handler._search_memory({"query": "红色"})
        assert "红色" in result
        mm.search_memories.assert_called_once()

    def test_falls_back_on_type_filter(self):
        from openakita.tools.handlers.memory import MemoryHandler
        from openakita.memory.types import Memory, MemoryType

        engine = MagicMock()
        mm = MagicMock()
        mm.retrieval_engine = engine
        mm.search_memories.return_value = [
            Memory(content="fact content", type=MemoryType.FACT),
        ]

        agent = MagicMock()
        agent.memory_manager = mm

        handler = MemoryHandler(agent)
        result = handler._search_memory({"query": "test", "type": "fact"})
        engine.retrieve_candidates.assert_not_called()
        mm.search_memories.assert_called_once()


# ===========================================================================
# Fix-5: Session backfill from SQLite
# ===========================================================================

class TestSessionBackfillOnRestart:

    def test_backfill_recovers_missing_messages(self, tmp_path):
        from openakita.sessions.session import Session, SessionContext

        session = Session(
            id="test_bf",
            channel="telegram",
            chat_id="123",
            user_id="user1",
            context=SessionContext(messages=[
                {"role": "user", "content": "msg1", "timestamp": "2026-02-20T10:00:00"},
            ]),
        )
        sessions_file = tmp_path / "sessions.json"
        sessions_file.write_text(json.dumps([session.to_dict()], ensure_ascii=False), encoding="utf-8")

        def mock_loader(safe_id):
            return [
                {"role": "user", "content": "msg1", "timestamp": "2026-02-20T10:00:00"},
                {"role": "assistant", "content": "reply1", "timestamp": "2026-02-20T10:00:05"},
                {"role": "user", "content": "msg2", "timestamp": "2026-02-20T10:01:00"},
            ]

        sm = _make_session_manager(tmp_path)
        sm.set_turn_loader(mock_loader)
        count = sm.backfill_sessions_from_store()

        assert count == 2
        loaded = sm.get_session("telegram", "123", "user1", create_if_missing=False)
        assert loaded is not None
        contents = [m["content"] for m in loaded.context.messages]
        assert "msg1" in contents
        assert "reply1" in contents
        assert "msg2" in contents

    def test_backfill_empty_session_from_sqlite(self, tmp_path):
        """If session has no messages but SQLite has data, recover all."""
        from openakita.sessions.session import Session, SessionContext

        session = Session(
            id="test_empty",
            channel="telegram",
            chat_id="456",
            user_id="user2",
            context=SessionContext(messages=[]),
        )
        sessions_file = tmp_path / "sessions.json"
        sessions_file.write_text(json.dumps([session.to_dict()], ensure_ascii=False), encoding="utf-8")

        def mock_loader(safe_id):
            return [
                {"role": "user", "content": "recovered1", "timestamp": "2026-02-20T09:00:00"},
                {"role": "assistant", "content": "recovered2", "timestamp": "2026-02-20T09:00:05"},
            ]

        sm = _make_session_manager(tmp_path)
        sm.set_turn_loader(mock_loader)
        count = sm.backfill_sessions_from_store()

        assert count == 2
        loaded = sm.get_session("telegram", "456", "user2", create_if_missing=False)
        contents = [m["content"] for m in loaded.context.messages]
        assert "recovered1" in contents
        assert "recovered2" in contents

    def test_no_backfill_without_loader(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        assert sm.backfill_sessions_from_store() == 0

    def test_no_duplicate_on_backfill(self, tmp_path):
        """If SQLite and session have the same data, no duplicates."""
        from openakita.sessions.session import Session, SessionContext

        session = Session(
            id="test_nodup",
            channel="test",
            chat_id="1",
            user_id="u",
            context=SessionContext(messages=[
                {"role": "user", "content": "msg1", "timestamp": "2026-02-20T12:00:00"},
            ]),
        )
        sessions_file = tmp_path / "sessions.json"
        sessions_file.write_text(json.dumps([session.to_dict()], ensure_ascii=False), encoding="utf-8")

        def mock_loader(safe_id):
            return [
                {"role": "user", "content": "msg1", "timestamp": "2026-02-20T12:00:00"},
            ]

        sm = _make_session_manager(tmp_path)
        sm.set_turn_loader(mock_loader)
        count = sm.backfill_sessions_from_store()
        assert count == 0


# ===========================================================================
# Fix-5 continued: Graceful shutdown saves
# ===========================================================================

class TestGracefulShutdownSave:

    @pytest.mark.asyncio
    async def test_stop_saves_sessions(self, tmp_path):
        sm = _make_session_manager(tmp_path)
        await sm.start()

        session = sm.get_session("telegram", "123", "user1")
        session.add_message("user", "important message")
        sm.mark_dirty()

        await sm.stop()

        sessions_file = tmp_path / "sessions.json"
        assert sessions_file.exists()
        data = json.loads(sessions_file.read_text(encoding="utf-8"))
        assert len(data) == 1
        msgs = data[0]["context"]["messages"]
        assert any(m["content"] == "important message" for m in msgs)

