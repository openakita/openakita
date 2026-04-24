"""L1 Unit Tests: SessionLogBuffer operations."""

import pytest

from openakita.logging.session_buffer import SessionLogBuffer, LogEntry


class TestLogEntry:
    def test_create_entry(self):
        e = LogEntry(timestamp="2026-01-01T00:00:00", level="INFO", module="core", message="Started")
        assert e.level == "INFO"

    def test_to_dict(self):
        e = LogEntry(timestamp="2026-01-01", level="ERROR", module="llm", message="Timeout")
        d = e.to_dict()
        assert d["level"] == "ERROR"
        assert d["module"] == "llm"

    def test_str(self):
        e = LogEntry(timestamp="2026-01-01", level="WARNING", module="mem", message="Low memory")
        s = str(e)
        assert "WARNING" in s


class TestSessionLogBuffer:
    @pytest.fixture
    def buffer(self):
        return SessionLogBuffer(max_entries_per_session=100)

    def test_empty_buffer(self, buffer):
        logs = buffer.get_logs()
        assert isinstance(logs, list)

    def test_add_and_get_logs(self, buffer):
        buffer.add_log("INFO", "test", "Hello world")
        logs = buffer.get_logs(count=10)
        assert len(logs) >= 1

    def test_session_scoped_logs(self, buffer):
        buffer.add_log("INFO", "core", "Global log")
        buffer.add_log("INFO", "core", "Session log", session_id="s1")
        buffer.add_log("INFO", "core", "Other session", session_id="s2")
        s1_logs = buffer.get_logs(session_id="s1", include_global=False)
        assert all(l.get("session_id") == "s1" for l in s1_logs if "session_id" in l)

    def test_level_filter(self, buffer):
        buffer.add_log("INFO", "mod", "Info msg")
        buffer.add_log("ERROR", "mod", "Error msg")
        errors = buffer.get_logs(level_filter="ERROR")
        assert all(l.get("level") == "ERROR" for l in errors)

    def test_formatted_output(self, buffer):
        buffer.add_log("INFO", "test", "Formatted log")
        text = buffer.get_logs_formatted(count=5)
        assert isinstance(text, str)

    def test_clear_session(self, buffer):
        buffer.add_log("INFO", "test", "To clear", session_id="s1")
        buffer.clear_session("s1")
        logs = buffer.get_logs(session_id="s1", include_global=False)
        assert len(logs) == 0

    def test_clear_all(self, buffer):
        buffer.add_log("INFO", "test", "A")
        buffer.add_log("INFO", "test", "B")
        buffer.clear_all()
        assert len(buffer.get_logs()) == 0

    def test_stats(self, buffer):
        buffer.add_log("INFO", "m", "x")
        stats = buffer.get_stats()
        assert isinstance(stats, dict)

    def test_set_current_session(self, buffer):
        buffer.set_current_session("my-session")
        assert buffer.get_current_session() == "my-session"

