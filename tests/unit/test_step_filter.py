"""Tests for StepFilter — tool call classification."""
from __future__ import annotations

from openakita.api.adapters.seecrab_models import FilterResult, StepFilterConfig
from openakita.api.adapters.step_filter import StepFilter


class TestClassify:
    def setup_method(self):
        self.f = StepFilter()

    def test_skill_triggers(self):
        assert self.f.classify("load_skill", {}) == FilterResult.SKILL_TRIGGER
        assert self.f.classify("run_skill_script", {}) == FilterResult.SKILL_TRIGGER
        assert self.f.classify("get_skill_info", {}) == FilterResult.HIDDEN

    def test_mcp_trigger(self):
        assert self.f.classify("call_mcp_tool", {"server": "gh"}) == FilterResult.MCP_TRIGGER

    def test_whitelist(self):
        assert self.f.classify("web_search", {"query": "test"}) == FilterResult.WHITELIST
        assert self.f.classify("deliver_artifacts", {}) == FilterResult.WHITELIST

    def test_hidden(self):
        assert self.f.classify("read_file", {}) == FilterResult.HIDDEN
        assert self.f.classify("write_file", {}) == FilterResult.HIDDEN
        assert self.f.classify("add_memory", {}) == FilterResult.HIDDEN
        assert self.f.classify("get_tool_info", {}) == FilterResult.HIDDEN

    def test_unknown_tool_hidden(self):
        assert self.f.classify("some_unknown_tool", {}) == FilterResult.HIDDEN


class TestUserMention:
    def setup_method(self):
        self.f = StepFilter()

    def test_mention_promotes_hidden_tool(self):
        self.f.set_user_messages(["帮我读取 config.yaml 文件"])
        assert self.f.classify("read_file", {}) == FilterResult.USER_MENTION

    def test_mention_run_shell(self):
        self.f.set_user_messages(["运行 npm install"])
        assert self.f.classify("run_shell", {}) == FilterResult.USER_MENTION

    def test_no_mention_stays_hidden(self):
        self.f.set_user_messages(["今天天气怎么样"])
        assert self.f.classify("read_file", {}) == FilterResult.HIDDEN

    def test_whitelist_not_affected_by_mention(self):
        self.f.set_user_messages(["搜索一下"])
        assert self.f.classify("web_search", {"query": "test"}) == FilterResult.WHITELIST


class TestCustomConfig:
    def test_custom_whitelist(self):
        config = StepFilterConfig(whitelist=["my_tool"])
        f = StepFilter(config=config)
        assert f.classify("my_tool", {}) == FilterResult.WHITELIST
        assert f.classify("web_search", {}) == FilterResult.HIDDEN
