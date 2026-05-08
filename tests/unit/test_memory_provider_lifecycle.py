from __future__ import annotations

import pytest

from openakita.memory.mcp_provider import MCPMemoryProvider
from openakita.plugins.hooks import HookRegistry
from openakita.prompt.builder import _build_memory_section


class FakeMCPResult:
    def __init__(self, success=True, data=None, error=None):
        self.success = success
        self.data = data
        self.error = error


class FakeMCPClient:
    def __init__(self):
        self.connected = False
        self.calls = []

    def is_connected(self, server):
        return self.connected

    async def connect(self, server):
        self.connected = True
        return FakeMCPResult(success=True, data={"server": server})

    async def call_tool(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        if tool == "search_memory":
            return FakeMCPResult(
                data={"items": [{"content": f"remembered:{arguments['query']}", "score": 0.9}]}
            )
        return FakeMCPResult(data={"ok": True})


class FakeMemoryManager:
    memory_md_path = None
    store = None

    def __init__(self):
        self.queries = []

    def get_injection_context(self, task_description="", max_related=5):
        self.queries.append(task_description)
        return f"external memory for {task_description}"


@pytest.mark.asyncio
async def test_mcp_memory_provider_retrieves_and_records_without_user_prompting():
    client = FakeMCPClient()
    provider = MCPMemoryProvider(
        client=client,
        server="memos",
        tools={"search": "search_memory", "record_turn": "add_message"},
    )

    items = await provider.retrieve("Akita", limit=3)
    await provider.record_turn("user", "hello")

    assert items[0]["content"] == "remembered:Akita"
    assert client.calls == [
        ("memos", "search_memory", {"query": "Akita", "limit": 3}),
        ("memos", "add_message", {"role": "user", "content": "hello"}),
    ]


def test_memory_section_retrieves_for_current_task_without_keywords():
    mm = FakeMemoryManager()

    section = _build_memory_section(
        memory_manager=mm,
        task_description="用户问 MemOS 配置",
        budget_tokens=500,
        memory_keywords=[],
    )

    assert mm.queries == ["用户问 MemOS 配置"]
    assert "external memory for 用户问 MemOS 配置" in section


def test_openclaw_lifecycle_hook_aliases_are_supported():
    registry = HookRegistry()

    def before_agent_start(**kwargs):
        return "context"

    def agent_end(**kwargs):
        return kwargs["status"]

    registry.register("before_agent_start", before_agent_start, plugin_id="p")
    registry.register("agent_end", agent_end, plugin_id="p")

    assert registry.dispatch_sync("before_agent_start") == ["context"]
    assert registry.dispatch_sync("agent_end", status="completed") == ["completed"]
