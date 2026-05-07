"""L1 unit tests for centralised session cache reset."""

from __future__ import annotations

from types import SimpleNamespace

from openakita.core.session_caches import clear_session_caches


def test_clear_session_caches_clears_known_agent_state():
    engine = SimpleNamespace(_readonly_tool_cache={"key": {"summary": "x"}})
    ctx = SimpleNamespace(_previous_summaries={"conv": "summary"})
    agent = SimpleNamespace(
        reasoning_engine=engine,
        context_manager=ctx,
        _last_browser_navigate_url="https://example.com",
        _last_link_diagnostic={"requested_url": "https://example.com"},
    )

    cleared = clear_session_caches(agent)

    assert cleared["web_fetch"] is True
    assert cleared.get("readonly_tool_cache") is True
    assert cleared.get("browser_navigation_memory") is True
    assert cleared.get("last_link_diagnostic") is True
    assert cleared.get("context_summaries") is True
    assert engine._readonly_tool_cache == {}
    assert ctx._previous_summaries == {}
    assert agent._last_browser_navigate_url == ""
    assert agent._last_link_diagnostic is None


def test_clear_session_caches_without_agent_still_clears_module_caches():
    cleared = clear_session_caches(None)
    assert cleared["web_fetch"] is True
