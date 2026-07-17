from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openakita.agent import Agent
from openakita.tools.handlers.tool_search import ToolSearchHandler


@pytest.mark.asyncio
async def test_tool_search_promotes_deferred_tool_for_next_main_chat_turn():
    tools = [
        {
            "name": "run_shell",
            "category": "File System",
            "description": "Run a shell command",
            "input_schema": {"type": "object"},
        },
        {
            "name": "web_search",
            "category": "Web Search",
            "description": "Search the web for current information",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ]
    agent = SimpleNamespace(_tools=tools, _discovered_tools=set())

    before = Agent._stable_main_chat_tool_set(agent, tools)
    assert before[1]["_deferred"] is True

    result = await ToolSearchHandler(agent)._search({"query": "web search"})
    result_schemas = json.loads(result.split("\n\n", 1)[1])
    assert result_schemas[0]["name"] == "web_search"
    assert "web_search" in agent._discovered_tools

    after = Agent._stable_main_chat_tool_set(agent, tools)
    assert after[1].get("_deferred") is None
    assert after[1]["_promoted"] is True
