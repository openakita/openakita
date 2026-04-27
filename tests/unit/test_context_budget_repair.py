import pytest

from openakita.core.context_manager import ContextManager
from openakita.core.loop_budget_guard import LoopBudgetGuard
from openakita.core.microcompact import microcompact
from openakita.prompt.budget import BudgetConfig
from openakita.prompt.builder import _build_catalogs_section


class DummyBrain:
    model = "test-model"


class DummyPluginCatalog:
    def get_catalog(self) -> str:
        return "PLUGIN-CATALOG\n" + ("插件描述 " * 5000)


def test_context_pressure_includes_system_tools_and_real_usage():
    cm = ContextManager(DummyBrain())
    messages = [{"role": "user", "content": "短消息"}]
    tools = [{"name": "big_tool", "input_schema": {"type": "object", "properties": {"x": "y" * 5000}}}]

    pressure = cm.calculate_context_pressure(
        messages,
        system_prompt="系统提示 " * 2000,
        tools=tools,
        max_tokens=20000,
        last_real_input_tokens=40000,
    )

    assert pressure.system_tokens > 0
    assert pressure.tools_tokens > 0
    assert pressure.estimated_total_tokens >= (
        pressure.system_tokens + pressure.tools_tokens + pressure.messages_tokens
    )
    assert pressure.calibrated_total_tokens >= 36000
    assert pressure.trigger_tokens > pressure.messages_tokens


def test_context_pressure_triggers_when_system_tools_are_large():
    cm = ContextManager(DummyBrain())
    messages = [{"role": "user", "content": "短消息"}]
    pressure = cm.calculate_context_pressure(
        messages,
        system_prompt="系统提示 " * 3000,
        tools=[
            {
                "name": "large_schema",
                "input_schema": {"type": "object", "description": "x" * 20000},
            }
        ],
        max_tokens=12000,
    )

    assert pressure.messages_tokens < pressure.soft_limit
    assert pressure.trigger_tokens > pressure.soft_limit


@pytest.mark.asyncio
async def test_compress_can_trigger_from_real_usage_even_when_messages_small(monkeypatch):
    cm = ContextManager(DummyBrain())
    calls = {"large_tool": 0}

    async def fake_large_tool_results(messages, threshold=None):
        calls["large_tool"] += 1
        return messages

    monkeypatch.setattr(cm, "_compress_large_tool_results", fake_large_tool_results)

    messages = [
        {"role": "user", "content": "任务"},
        {"role": "assistant", "content": "处理中"},
        {"role": "user", "content": "继续"},
    ]
    result = await cm.compress_if_needed(
        messages,
        max_tokens=200000,
        last_real_input_tokens=190000,
        force=False,
    )

    assert result
    assert calls["large_tool"] >= 1


def test_loop_budget_token_anomaly_warns_before_terminating():
    guard = LoopBudgetGuard(max_total_tool_calls=10, token_anomaly_threshold=100)
    guard.record_tool_calls([{"name": "web_search"} for _ in range(5)])

    first = guard.check_token_growth(150, 1, max_recoveries=1)
    assert first.should_warn
    assert not first.should_stop

    guard.check_token_growth(150, 1, recovered=True)
    second = guard.check_token_growth(150, 1, max_recoveries=1)
    assert second.should_stop
    assert second.exit_reason == "token_growth_terminated"


def test_loop_budget_readonly_stagnation_has_soft_warning():
    guard = LoopBudgetGuard(readonly_stagnation_limit=2, readonly_stagnation_hard_limit=4)
    call = [{"name": "read_file"}]
    results = [{"content": "same"}]

    assert not guard.record_tool_results(call, results).should_stop
    assert not guard.record_tool_results(call, results).should_stop
    warn = guard.record_tool_results(call, results)
    assert warn.should_warn
    assert not warn.should_stop


def test_microcompact_dedupes_cached_and_repeated_tool_results():
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "a",
                    "cache_key": "web_search:abc",
                    "tool_name": "web_search",
                    "content": "[系统缓存:abc] cached summary",
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "b",
                    "cache_key": "web_search:abc",
                    "tool_name": "web_search",
                    "content": "[系统缓存:abc] cached summary",
                }
            ],
        },
        {"role": "assistant", "content": "recent"},
        {"role": "user", "content": "recent"},
        {"role": "assistant", "content": "recent"},
    ]

    compacted = microcompact(messages, current_time=1)
    assert "duplicate merged" in compacted[1]["content"][0]["content"]


def test_plugin_catalog_is_budgeted():
    section = _build_catalogs_section(
        tool_catalog=None,
        skill_catalog=None,
        mcp_catalog=None,
        plugin_catalog=DummyPluginCatalog(),
        budget_tokens=300,
    )

    assert "PLUGIN-CATALOG" in section
    assert len(section) < len(DummyPluginCatalog().get_catalog())
