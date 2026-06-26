"""Tests for openakita.plugins.sandbox — safe_call, safe_call_sync, PluginErrorTracker."""

from __future__ import annotations

import asyncio

from openakita.plugins.sandbox import (
    MAX_CONSECUTIVE_ERRORS,
    PluginErrorTracker,
    safe_call,
    safe_call_sync,
)

# ---------- safe_call (async) ----------


class TestSafeCall:
    async def test_normal_function_returns_result(self):
        async def ok():
            return 42

        result = await safe_call(ok())
        assert result == 42

    async def test_timeout_returns_default(self):
        async def slow():
            await asyncio.sleep(10)
            return "late"

        result = await safe_call(slow(), timeout=0.05, default="fallback")
        assert result == "fallback"

    async def test_exception_returns_default(self):
        async def bad():
            raise RuntimeError("boom")

        result = await safe_call(bad(), default="safe")
        assert result == "safe"

    async def test_default_is_none(self):
        async def bad():
            raise ValueError("err")

        result = await safe_call(bad())
        assert result is None

    async def test_records_error_on_timeout(self):
        tracker = PluginErrorTracker()

        async def slow():
            await asyncio.sleep(10)

        await safe_call(
            slow(),
            timeout=0.05,
            plugin_id="p1",
            context="test",
            error_tracker=tracker,
        )
        errors = tracker.get_errors("p1")
        assert len(errors) == 1
        assert errors[0]["error"] == "timeout"

    async def test_records_error_on_exception(self):
        tracker = PluginErrorTracker()

        async def bad():
            raise ValueError("oops")

        await safe_call(
            bad(),
            plugin_id="p1",
            context="test",
            error_tracker=tracker,
        )
        errors = tracker.get_errors("p1")
        assert len(errors) == 1
        assert "oops" in errors[0]["error"]


# ---------- safe_call_sync ----------


class TestSafeCallSync:
    def test_normal_function_returns_result(self):
        def ok():
            return 99

        assert safe_call_sync(ok) == 99

    def test_exception_returns_default(self):
        def bad():
            raise RuntimeError("fail")

        assert safe_call_sync(bad, default="safe") == "safe"

    def test_default_is_none(self):
        def bad():
            raise ValueError("err")

        assert safe_call_sync(bad) is None

    def test_passes_args_and_kwargs(self):
        def add(a, b, extra=0):
            return a + b + extra

        assert safe_call_sync(add, 1, 2, extra=10) == 13

    def test_records_error_on_exception(self):
        tracker = PluginErrorTracker()

        def bad():
            raise RuntimeError("sync-fail")

        safe_call_sync(
            bad,
            plugin_id="p1",
            context="sync-test",
            error_tracker=tracker,
        )
        errors = tracker.get_errors("p1")
        assert len(errors) == 1
        assert "sync-fail" in errors[0]["error"]


# ---------- PluginErrorTracker ----------


class TestPluginErrorTracker:
    def test_record_error_tracks(self):
        tracker = PluginErrorTracker()
        tracker.record_error("p1", "ctx", "err1")
        errors = tracker.get_errors("p1")
        assert len(errors) == 1
        assert errors[0]["context"] == "ctx"
        assert errors[0]["error"] == "err1"

    def test_not_disabled_below_threshold(self):
        tracker = PluginErrorTracker()
        for i in range(MAX_CONSECUTIVE_ERRORS - 1):
            result = tracker.record_error("p1", "ctx", f"err{i}")
            assert result is False
        assert not tracker.is_disabled("p1")

    def test_auto_disable_at_threshold(self):
        tracker = PluginErrorTracker()
        for i in range(MAX_CONSECUTIVE_ERRORS):
            result = tracker.record_error("p1", "ctx", f"err{i}")
        assert result is True
        assert tracker.is_disabled("p1")

    def test_reset_clears_errors_and_disabled(self):
        tracker = PluginErrorTracker()
        for i in range(MAX_CONSECUTIVE_ERRORS):
            tracker.record_error("p1", "ctx", f"err{i}")
        assert tracker.is_disabled("p1")
        tracker.reset("p1")
        assert not tracker.is_disabled("p1")
        assert tracker.get_errors("p1") == []

    def test_different_plugins_tracked_independently(self):
        tracker = PluginErrorTracker()
        for i in range(MAX_CONSECUTIVE_ERRORS):
            tracker.record_error("p1", "ctx", f"err{i}")
        assert tracker.is_disabled("p1")
        assert not tracker.is_disabled("p2")

    def test_is_disabled_unknown_plugin(self):
        tracker = PluginErrorTracker()
        assert not tracker.is_disabled("unknown")

    def test_get_errors_unknown_plugin(self):
        tracker = PluginErrorTracker()
        assert tracker.get_errors("unknown") == []
