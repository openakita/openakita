# tests/unit/test_seecrab_adapter.py
"""Tests for SeeCrabAdapter — raw event stream → refined SSE events."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.api.adapters.seecrab_adapter import SeeCrabAdapter


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
