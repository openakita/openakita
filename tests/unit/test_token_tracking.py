"""L1 Unit Tests: Token tracking context and recording."""

import pytest
from openakita.core.token_tracking import (
    TokenTrackingContext,
    set_tracking_context,
    get_tracking_context,
    reset_tracking_context,
)


class TestTokenTrackingContext:
    def test_default_values(self):
        ctx = TokenTrackingContext()
        assert ctx.session_id == ""
        assert ctx.operation_type == "unknown"
        assert ctx.channel == ""
        assert ctx.iteration == 0

    def test_custom_values(self):
        ctx = TokenTrackingContext(
            session_id="s1", operation_type="chat",
            channel="telegram", user_id="u1", iteration=3,
        )
        assert ctx.session_id == "s1"
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

