"""L1 Unit Tests: Token tracking context and recording."""

import sqlite3

from openakita.core.token_tracking import (
    TokenBudgetState,
    TokenTrackingContext,
    ensure_token_usage_schema_sync,
    get_token_budget,
    get_tracking_context,
    record_usage,
    reset_token_budget,
    reset_tracking_context,
    set_token_budget,
    set_tracking_context,
    token_budget_exceeded,
    token_budget_status,
)


class TestTokenTrackingContext:
    def test_default_values(self):
        ctx = TokenTrackingContext()
        assert ctx.session_id == ""
        assert ctx.request_id == ""
        assert ctx.turn_id == ""
        assert ctx.operation_type == "unknown"
        assert ctx.channel == ""
        assert ctx.iteration == 0

    def test_custom_values(self):
        ctx = TokenTrackingContext(
            session_id="s1", request_id="r1", turn_id="t1", operation_type="chat",
            channel="telegram", user_id="u1", iteration=3,
        )
        assert ctx.session_id == "s1"
        assert ctx.request_id == "r1"
        assert ctx.turn_id == "t1"
        assert ctx.operation_type == "chat"
        assert ctx.channel == "telegram"
        assert ctx.iteration == 3


class TestContextVars:
    def test_set_and_get(self):
        ctx = TokenTrackingContext(session_id="test-session")
        token = set_tracking_context(ctx)
        try:
            retrieved = get_tracking_context()
            assert retrieved is not None
            assert retrieved.session_id == "test-session"
        finally:
            reset_tracking_context(token)

    def test_reset_clears_context(self):
        ctx = TokenTrackingContext(session_id="temp")
        token = set_tracking_context(ctx)
        reset_tracking_context(token)
        # After reset, should return None or previous value
        result = get_tracking_context()
        assert result is None or result.session_id != "temp"

    def test_nested_contexts(self):
        ctx1 = TokenTrackingContext(session_id="outer")
        t1 = set_tracking_context(ctx1)
        assert get_tracking_context().session_id == "outer"

        ctx2 = TokenTrackingContext(session_id="inner")
        t2 = set_tracking_context(ctx2)
        assert get_tracking_context().session_id == "inner"

        reset_tracking_context(t2)
        assert get_tracking_context().session_id == "outer"
        reset_tracking_context(t1)


class TestTokenBudget:
    def test_record_usage_updates_active_budget_without_db_writer(self):
        token = set_token_budget(TokenBudgetState(name="background", max_tokens=100))
        try:
            record_usage(input_tokens=40, output_tokens=30)

            status = token_budget_status()
            assert status["used_tokens"] == 70
            assert status["remaining_tokens"] == 30
            assert token_budget_exceeded() is False

            record_usage(input_tokens=31)

            assert token_budget_exceeded() is True
            assert get_token_budget().used_tokens == 101
        finally:
            reset_token_budget(token)


class TestTokenUsageSchemaMigration:
    def test_adds_request_columns_before_indexes(self, tmp_path):
        db_path = tmp_path / "old.sqlite"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                CREATE TABLE token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    session_id TEXT,
                    endpoint_name TEXT,
                    operation_type TEXT
                )
                """
            )
            conn.commit()

            ensure_token_usage_schema_sync(conn)

            columns = {row[1] for row in conn.execute("PRAGMA table_info(token_usage)")}
            assert {"request_id", "turn_id", "agent_profile_id", "estimated_cost"} <= columns

            indexes = {
                row[1] for row in conn.execute("PRAGMA index_list(token_usage)").fetchall()
            }
            assert "idx_token_usage_request" in indexes
            assert "idx_token_usage_op" in indexes
        finally:
            conn.close()

