# tests/unit/test_seecrab_adapter.py
"""Tests for SeeCrabAdapter — raw event stream → refined SSE events."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from seeagent.api.adapters.seecrab_adapter import SeeCrabAdapter


async def _events_from(raw_events: list[dict], user_messages=None) -> list[dict]:
    """Helper: run adapter on a list of raw events, collect output."""
    async def gen():
        for e in raw_events:
            yield e

    adapter = SeeCrabAdapter(brain=None, user_messages=user_messages or [])
    result = []
    async for event in adapter.transform(gen(), reply_id="test_reply"):
        result.append(event)
    return result


class TestBasicFlow:
    @pytest.mark.asyncio
    async def test_empty_stream(self):
        events = await _events_from([])
        types = [e["type"] for e in events]
        assert "timer_update" in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_thinking_passthrough(self):
        events = await _events_from([
            {"type": "thinking_delta", "content": "thinking..."},
            {"type": "thinking_end", "duration_ms": 500},
        ])
        thinking = [e for e in events if e["type"] == "thinking"]
        assert len(thinking) >= 1
        assert thinking[0]["content"] == "thinking..."

    @pytest.mark.asyncio
    async def test_text_delta_becomes_ai_text(self):
        events = await _events_from([
            {"type": "text_delta", "content": "Hello world"},
        ])
        ai_texts = [e for e in events if e["type"] == "ai_text"]
        assert len(ai_texts) == 1
        assert ai_texts[0]["content"] == "Hello world"

    @pytest.mark.asyncio
    async def test_ttft_triggered_on_first_token(self):
        events = await _events_from([
            {"type": "text_delta", "content": "Hi"},
        ])
        ttft_events = [e for e in events if e.get("type") == "timer_update" and e.get("phase") == "ttft"]
        # Should have running + done
        assert any(e["state"] == "done" for e in ttft_events)


class TestToolCallFlow:
    @pytest.mark.asyncio
    async def test_whitelist_tool_creates_card(self):
        events = await _events_from([
            {"type": "tool_call_start", "tool": "web_search", "args": {"query": "test"}, "id": "t1"},
            {"type": "tool_call_end", "tool": "web_search", "result": "results", "id": "t1", "is_error": False},
            {"type": "text_delta", "content": "Summary"},
        ])
        step_cards = [e for e in events if e["type"] == "step_card"]
        assert len(step_cards) >= 1

    @pytest.mark.asyncio
    async def test_hidden_tool_no_card(self):
        events = await _events_from([
            {"type": "tool_call_start", "tool": "read_file", "args": {"path": "x"}, "id": "t1"},
            {"type": "tool_call_end", "tool": "read_file", "result": "data", "id": "t1", "is_error": False},
            {"type": "text_delta", "content": "Done"},
        ])
        step_cards = [e for e in events if e["type"] == "step_card"]
        assert len(step_cards) == 0


class TestAskUser:
    @pytest.mark.asyncio
    async def test_ask_user_maps_fields(self):
        events = await _events_from([
            {
                "type": "ask_user",
                "question": "Which?",
                "options": [{"id": "a", "label": "Option A"}],
            },
        ])
        ask = next(e for e in events if e["type"] == "ask_user")
        assert ask["question"] == "Which?"
        assert ask["options"][0]["value"] == "a"
        assert ask["options"][0]["label"] == "Option A"

    @pytest.mark.asyncio
    async def test_title_update_queue_drained(self):
        """Verify title_update_queue events are yielded during transform."""
        async def raw():
            yield {"type": "tool_call_start", "tool": "load_skill",
                   "args": {"skill": "test_skill"}, "id": "t1"}
            # Give async title task a moment to enqueue
            await asyncio.sleep(0.05)
            yield {"type": "text_delta", "content": "Done"}

        adapter = SeeCrabAdapter(brain=None, user_messages=[])
        events = []
        async for e in adapter.transform(raw(), reply_id="r1"):
            events.append(e)
        # Should have at least one step_card with non-placeholder title
        # (from queue drain or from flush)
        step_cards = [e for e in events if e.get("type") == "step_card"]
        assert len(step_cards) >= 1


class TestAgentHeader:
    @pytest.mark.asyncio
    async def test_agent_header_passthrough(self):
        """agent_header events should pass through to output."""
        events = await _events_from([
            {"type": "agent_header", "agent_id": "researcher", "agent_name": "研究员"},
            {"type": "text_delta", "content": "Hello"},
        ])
        headers = [e for e in events if e["type"] == "agent_header"]
        assert len(headers) == 1
        assert headers[0]["agent_id"] == "researcher"
        assert headers[0]["agent_name"] == "研究员"

    @pytest.mark.asyncio
    async def test_agent_switch_flushes_aggregator(self):
        """Switching agents should flush the previous agent's aggregator."""
        events = await _events_from([
            {"type": "tool_call_start", "tool": "load_skill", "args": {}, "id": "t1"},
            {"type": "agent_header", "agent_id": "sub", "agent_name": "Sub"},
            {"type": "text_delta", "content": "Done"},
        ])
        step_cards = [e for e in events if e["type"] == "step_card"]
        # The skill card from main should be flushed (completed) on agent switch
        completed = [c for c in step_cards if c["status"] == "completed"]
        assert len(completed) >= 1

    @pytest.mark.asyncio
    async def test_sub_agent_tools_get_own_cards(self):
        """Sub-agent tool calls should produce their own step cards."""
        events = await _events_from([
            {"type": "agent_header", "agent_id": "sub", "agent_name": "Sub"},
            {"type": "tool_call_start", "tool": "web_search", "args": {"query": "test"}, "id": "t1"},
            {"type": "tool_call_end", "tool": "web_search", "result": "results", "id": "t1", "is_error": False},
            {"type": "agent_header", "agent_id": "main", "agent_name": "SeeAgent"},
            {"type": "text_delta", "content": "Done"},
        ])
        step_cards = [e for e in events if e["type"] == "step_card"]
        assert len(step_cards) >= 1
        assert any("test" in c.get("title", "") for c in step_cards)

    @pytest.mark.asyncio
    async def test_empty_agent_id_defaults_to_sub_agent(self):
        """Empty agent_id in agent_header should fallback, not produce 'main'."""
        events = await _events_from([
            {"type": "agent_header", "agent_id": "", "agent_name": "Sub"},
            {"type": "tool_call_start", "tool": "web_search", "args": {"query": "test"}, "id": "t1"},
            {"type": "tool_call_end", "tool": "web_search", "result": "results", "id": "t1", "is_error": False},
            {"type": "agent_header", "agent_id": "main", "agent_name": "SeeAgent"},
            {"type": "text_delta", "content": "Done"},
        ])
        step_cards = [e for e in events if e["type"] == "step_card"]
        for card in step_cards:
            if "test" in card.get("title", ""):
                assert card["agent_id"] != "main", "Empty agent_id should not default to 'main'"
                assert card["agent_id"] != "", "agent_id should not be empty string"

    @pytest.mark.asyncio
    async def test_sub_agent_step_cards_carry_correct_agent_id(self):
        """Step cards from sub-agent should have the sub-agent's agent_id."""
        events = await _events_from([
            {"type": "agent_header", "agent_id": "researcher", "agent_name": "研究员"},
            {"type": "tool_call_start", "tool": "web_search", "args": {"query": "test"}, "id": "t1"},
            {"type": "tool_call_end", "tool": "web_search", "result": "results", "id": "t1", "is_error": False},
            {"type": "agent_header", "agent_id": "main", "agent_name": "SeeAgent"},
            {"type": "text_delta", "content": "Done"},
        ])
        step_cards = [e for e in events if e["type"] == "step_card"]
        search_cards = [c for c in step_cards if "test" in c.get("title", "")]
        assert len(search_cards) >= 1
        for card in search_cards:
            assert card["agent_id"] == "researcher"

    @pytest.mark.asyncio
    async def test_sub_agent_ai_text_carries_agent_id(self):
        """ai_text events from sub-agent should carry the sub-agent's agent_id."""
        events = await _events_from([
            {"type": "agent_header", "agent_id": "researcher", "agent_name": "研究员"},
            {"type": "text_delta", "content": "Sub-agent summary"},
            {"type": "agent_header", "agent_id": "main", "agent_name": "SeeAgent"},
            {"type": "text_delta", "content": "Main summary"},
        ])
        ai_texts = [e for e in events if e["type"] == "ai_text"]
        assert len(ai_texts) == 2
        assert ai_texts[0]["agent_id"] == "researcher"
        assert ai_texts[0]["content"] == "Sub-agent summary"
        assert ai_texts[1]["agent_id"] == "main"
        assert ai_texts[1]["content"] == "Main summary"


class TestEventBusMerge:
    @pytest.mark.asyncio
    async def test_event_bus_events_merged_into_stream(self):
        """Events put into event_bus should appear in the output."""
        event_bus = asyncio.Queue()

        async def raw():
            # Simulate a blocking tool call — put sub-agent events into bus
            yield {"type": "tool_call_start", "tool": "delegate_to_agent",
                   "args": {"agent_id": "sub", "message": "task"}, "id": "t1"}
            # Sub-agent events arrive via bus during the "blocking" period
            await event_bus.put({
                "type": "agent_header", "agent_id": "sub", "agent_name": "Sub",
            })
            await event_bus.put({
                "type": "tool_call_start", "tool": "web_search",
                "args": {"query": "test"}, "id": "t2",
            })
            await event_bus.put({
                "type": "tool_call_end", "tool": "web_search",
                "result": "found", "id": "t2", "is_error": False,
            })
            await event_bus.put({
                "type": "agent_header", "agent_id": "main", "agent_name": "SeeAgent",
            })
            # Main agent resumes
            yield {"type": "tool_call_end", "tool": "delegate_to_agent",
                   "result": "done", "id": "t1", "is_error": False}
            yield {"type": "text_delta", "content": "Summary"}

        adapter = SeeCrabAdapter(brain=None, user_messages=[])
        events = []
        async for e in adapter.transform(raw(), reply_id="r1", event_bus=event_bus):
            events.append(e)

        headers = [e for e in events if e["type"] == "agent_header"]
        assert len(headers) >= 2  # sub + main
        step_cards = [e for e in events if e["type"] == "step_card"]
        assert any("test" in c.get("title", "") for c in step_cards)

    @pytest.mark.asyncio
    async def test_no_event_bus_works_as_before(self):
        """Without event_bus, transform should work identically to before."""
        events = await _events_from([
            {"type": "text_delta", "content": "Hello"},
        ])
        ai_texts = [e for e in events if e["type"] == "ai_text"]
        assert len(ai_texts) == 1
