"""Tests for TitleGenerator — LLM title generation + humanize mapping."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from seeagent.api.adapters.title_generator import TitleGenerator


class TestHumanizeToolTitle:
    def setup_method(self):
        self.gen = TitleGenerator(brain=None, user_messages=[])

    def test_web_search(self):
        title = self.gen.humanize_tool_title("web_search", {"query": "Karpathy 2026"})
        assert "Karpathy 2026" in title

    def test_news_search(self):
        title = self.gen.humanize_tool_title("news_search", {"query": "AI"})
        assert "AI" in title

    def test_browser_task(self):
        title = self.gen.humanize_tool_title("browser_task", {})
        assert title  # non-empty

    def test_unknown_tool_fallback(self):
        title = self.gen.humanize_tool_title("unknown_tool", {})
        assert title  # should return a fallback
