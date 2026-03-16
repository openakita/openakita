"""Tests for CardBuilder — step card event assembly."""
from __future__ import annotations

from seeagent.api.adapters.card_builder import CardBuilder


class TestBuildStepCard:
    def setup_method(self):
        self.builder = CardBuilder()

    def test_basic_card(self):
        card = self.builder.build_step_card(
            step_id="s1", title="搜索测试", status="running",
            source_type="tool", tool_name="web_search",
        )
        assert card["type"] == "step_card"
        assert card["step_id"] == "s1"
        assert card["title"] == "搜索测试"
        assert card["status"] == "running"
        assert card["card_type"] == "search"
        assert card["agent_id"] == "main"
        assert card["absorbed_calls"] == []

    def test_completed_with_io(self):
        card = self.builder.build_step_card(
            step_id="s2", title="done", status="completed",
            source_type="skill", tool_name="load_skill",
            duration=3.2,
            input_data={"query": "test"},
            output_data="result text",
            absorbed_calls=[{"tool": "web_search", "duration": 1.0}],
        )
        assert card["duration"] == 3.2
        assert card["input"] == {"query": "test"}
        assert card["output"] == "result text"
        assert len(card["absorbed_calls"]) == 1

    def test_plan_step_card(self):
        card = self.builder.build_step_card(
            step_id="s3", title="步骤1", status="running",
            source_type="plan_step", tool_name="web_search",
            plan_step_index=1,
        )
        assert card["plan_step_index"] == 1
        assert card["source_type"] == "plan_step"


class TestGetCardType:
    def setup_method(self):
        self.builder = CardBuilder()

    def test_search_types(self):
        assert self.builder._get_card_type("web_search") == "search"
        assert self.builder._get_card_type("news_search") == "search"

    def test_code_types(self):
        assert self.builder._get_card_type("code_execute") == "code"

    def test_file_types(self):
        assert self.builder._get_card_type("deliver_artifacts") == "default"

    def test_browser_wildcard(self):
        assert self.builder._get_card_type("browser_task") == "browser"
        assert self.builder._get_card_type("browser_navigate") == "browser"

    def test_default_fallback(self):
        assert self.builder._get_card_type("unknown_tool") == "default"
        assert self.builder._get_card_type("load_skill") == "default"
