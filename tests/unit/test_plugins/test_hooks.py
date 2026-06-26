"""Tests for openakita.plugins.hooks.HookRegistry."""

from __future__ import annotations

import asyncio

import pytest

from openakita.plugins.hooks import HookRegistry
from openakita.plugins.sandbox import PluginErrorTracker


@pytest.fixture()
def tracker():
    return PluginErrorTracker()


@pytest.fixture()
def registry(tracker):
    return HookRegistry(error_tracker=tracker)


# ---------- register ----------


class TestRegister:
    def test_register_valid_hook(self, registry):
        registry.register("on_init", lambda: "ok", plugin_id="p1")
        assert len(registry.get_hooks("on_init")) == 1

    def test_register_unknown_hook_raises(self, registry):
        with pytest.raises(ValueError, match="Unknown hook"):
            registry.register("on_nonexistent", lambda: None, plugin_id="p1")

    def test_register_sets_plugin_id_on_callback(self, registry):
        def cb():
            return None

        registry.register("on_init", cb, plugin_id="p1")
        assert cb.__plugin_id__ == "p1"


# ---------- dispatch ----------


class TestDispatch:
    async def test_dispatch_returns_results(self, registry):
        async def hook(**kw):
            return 42

        registry.register("on_init", hook, plugin_id="p1")
        results = await registry.dispatch("on_init")
        assert results == [42]

    async def test_dispatch_no_callbacks_returns_empty(self, registry):
        results = await registry.dispatch("on_init")
        assert results == []

    async def test_dispatch_multiple_callbacks_order(self, registry):
        call_order = []

        async def hook_a(**kw):
            call_order.append("a")
            return "a"

        async def hook_b(**kw):
            call_order.append("b")
            return "b"

        registry.register("on_init", hook_a, plugin_id="p1")
        registry.register("on_init", hook_b, plugin_id="p2")
        results = await registry.dispatch("on_init")
        assert call_order == ["a", "b"]
        assert results == ["a", "b"]

    async def test_dispatch_sync_callback_works(self, registry):
        def sync_hook(**kw):
            return "sync_result"

        registry.register("on_init", sync_hook, plugin_id="p1")
        results = await registry.dispatch("on_init")
        assert results == ["sync_result"]


# ---------- Exception isolation ----------


class TestExceptionIsolation:
    async def test_exception_caught_others_still_called(self, registry):
        async def bad(**kw):
            raise RuntimeError("boom")

        async def good(**kw):
            return "ok"

        registry.register("on_init", bad, plugin_id="p1")
        registry.register("on_init", good, plugin_id="p2")
        results = await registry.dispatch("on_init")
        assert results == ["ok"]

    async def test_timeout_skipped_others_proceed(self, registry):
        async def slow(**kw):
            await asyncio.sleep(10)
            return "late"

        async def fast(**kw):
            return "fast"

        registry.register("on_init", slow, plugin_id="p1")
        registry.set_timeout("on_init", "p1", 0.05)
        registry.register("on_init", fast, plugin_id="p2")
        results = await registry.dispatch("on_init")
        assert results == ["fast"]


# ---------- unregister_plugin ----------


class TestUnregisterPlugin:
    def test_removes_all_hooks_for_plugin(self, registry):
        registry.register("on_init", lambda: None, plugin_id="p1")
        registry.register("on_shutdown", lambda: None, plugin_id="p1")
        registry.register("on_init", lambda: None, plugin_id="p2")
        removed = registry.unregister_plugin("p1")
        assert removed == 2
        assert len(registry.get_hooks("on_init")) == 1
        assert len(registry.get_hooks("on_shutdown")) == 0

    def test_unregister_nonexistent_plugin_returns_zero(self, registry):
        removed = registry.unregister_plugin("ghost")
        assert removed == 0


# ---------- Error tracking integration ----------


class TestErrorTracking:
    async def test_error_recorded_on_exception(self, registry, tracker):
        async def bad(**kw):
            raise ValueError("fail")

        registry.register("on_init", bad, plugin_id="p1")
        await registry.dispatch("on_init")
        errors = tracker.get_errors("p1")
        assert len(errors) == 1
        assert errors[0]["error"] == "fail"

    async def test_error_recorded_on_timeout(self, registry, tracker):
        async def slow(**kw):
            await asyncio.sleep(10)

        registry.register("on_init", slow, plugin_id="p1")
        registry.set_timeout("on_init", "p1", 0.05)
        await registry.dispatch("on_init")
        errors = tracker.get_errors("p1")
        assert len(errors) == 1
        assert errors[0]["error"] == "timeout"

    async def test_auto_disable_after_max_errors(self, registry, tracker):
        async def bad(**kw):
            raise RuntimeError("boom")

        registry.register("on_init", bad, plugin_id="p1")
        for _ in range(10):
            await registry.dispatch("on_init")
        assert tracker.is_disabled("p1")

    async def test_disabled_plugin_skipped_in_dispatch(self, registry, tracker):
        call_count = 0

        async def counting(**kw):
            nonlocal call_count
            call_count += 1
            return call_count

        registry.register("on_init", counting, plugin_id="p1")
        tracker._disabled.add("p1")
        results = await registry.dispatch("on_init")
        assert results == []
        assert call_count == 0


# ---------- stats / clear ----------


class TestStatsAndClear:
    def test_stats_counts_hooks(self, registry):
        registry.register("on_init", lambda: None, plugin_id="p1")
        registry.register("on_init", lambda: None, plugin_id="p2")
        registry.register("on_shutdown", lambda: None, plugin_id="p1")
        assert registry.stats == {"on_init": 2, "on_shutdown": 1}

    def test_clear_removes_all(self, registry):
        registry.register("on_init", lambda: None, plugin_id="p1")
        registry.clear()
        assert registry.stats == {}
        assert registry.get_hooks("on_init") == []
