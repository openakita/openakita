"""Tests for MultiAgentAdapter — multi-stream merging."""
from __future__ import annotations

import asyncio

import pytest

from openakita.api.adapters.multi_agent_adapter import MultiAgentAdapter


async def _fake_stream(agent_id: str, events: list[dict]):
    """Create a fake refined event stream."""
    for e in events:
        e["agent_id"] = agent_id
        yield e
        await asyncio.sleep(0.01)


class TestMergeStreams:
    pytestmark = pytest.mark.asyncio

    async def test_single_agent_passthrough(self):
        adapter = MultiAgentAdapter()
        streams = {
            "agent_a": (
                {"name": "Agent A", "description": "test"},
                _fake_stream("agent_a", [
                    {"type": "thinking", "content": "..."},
                    {"type": "ai_text", "content": "hello"},
                    {"type": "done"},
                ]),
            ),
        }
        events = []
        async for e in adapter.merge_streams(streams):
            events.append(e)
        types = [e["type"] for e in events]
        assert "thinking" in types
        assert "ai_text" in types

    async def test_two_agents_with_headers(self):
        adapter = MultiAgentAdapter()
        streams = {
            "agent_a": (
                {"name": "Researcher", "description": "research"},
                _fake_stream("agent_a", [
                    {"type": "ai_text", "content": "research done"},
                    {"type": "done"},
                ]),
            ),
            "agent_b": (
                {"name": "Coder", "description": "code"},
                _fake_stream("agent_b", [
                    {"type": "ai_text", "content": "code done"},
                    {"type": "done"},
                ]),
            ),
        }
        events = []
        async for e in adapter.merge_streams(streams):
            events.append(e)
        headers = [e for e in events if e["type"] == "agent_header"]
        assert len(headers) >= 2
        agent_names = {h["agent_name"] for h in headers}
        assert "Researcher" in agent_names
        assert "Coder" in agent_names

    async def test_error_isolation(self):
        adapter = MultiAgentAdapter()

        async def failing_stream(agent_id, events):
            yield {"type": "ai_text", "content": "before", "agent_id": agent_id}
            raise RuntimeError("agent crash")

        streams = {
            "agent_a": (
                {"name": "Fail"},
                failing_stream("agent_a", []),
            ),
            "agent_b": (
                {"name": "OK"},
                _fake_stream("agent_b", [
                    {"type": "ai_text", "content": "ok"},
                    {"type": "done"},
                ]),
            ),
        }
        events = []
        async for e in adapter.merge_streams(streams):
            events.append(e)
        # agent_b should still deliver its events
        ok_texts = [e for e in events if e.get("content") == "ok"]
        assert len(ok_texts) == 1
        # agent_a error should be surfaced
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) >= 1
