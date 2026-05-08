from types import SimpleNamespace

import pytest

from openakita.tools.handlers.config import ConfigHandler


class _FakeBrain:
    def __init__(self) -> None:
        self.switch_calls = []
        self.restore_calls = []

    def switch_model(self, endpoint_name, hours=12, reason="", conversation_id=None):
        self.switch_calls.append(
            {
                "endpoint_name": endpoint_name,
                "hours": hours,
                "reason": reason,
                "conversation_id": conversation_id,
            }
        )
        if endpoint_name == "missing":
            return False, "端点 'missing' 不存在"
        return True, "已切换到模型: qwen3.5:2b"

    def restore_default_model(self, conversation_id=None):
        self.restore_calls.append({"conversation_id": conversation_id})
        return True, "已恢复默认模型: deepseek-reasoner"


class _FakeAgent:
    def __init__(self) -> None:
        self.brain = _FakeBrain()
        self._current_session = SimpleNamespace(chat_id="chat-123", id="session-uuid")
        self._current_session_id = "session-id"

    def _resolve_model_lookup_id(self, *, session=None, conversation_id=None, session_id=None):
        assert session is self._current_session
        assert conversation_id is None
        assert session_id == "session-id"
        return "chat-123"


@pytest.mark.asyncio
async def test_select_endpoint_switches_current_conversation_only():
    agent = _FakeAgent()
    handler = ConfigHandler(agent)

    result = await handler.handle(
        "system_config",
        {"action": "select_endpoint", "endpoint_name": "ollama-qwen3.5-2b"},
    )

    assert "已切换到端点" in result
    assert "当前会话" in result
    assert agent.brain.switch_calls == [
        {
            "endpoint_name": "ollama-qwen3.5-2b",
            "hours": 12,
            "reason": "system_config:select_endpoint",
            "conversation_id": "chat-123",
        }
    ]


@pytest.mark.asyncio
async def test_select_endpoint_can_restore_default_for_current_conversation():
    agent = _FakeAgent()
    handler = ConfigHandler(agent)

    result = await handler.handle(
        "system_config",
        {"action": "select_endpoint", "endpoint_name": "auto"},
    )

    assert "已恢复默认模型" in result
    assert agent.brain.restore_calls == [{"conversation_id": "chat-123"}]
    assert agent.brain.switch_calls == []


@pytest.mark.asyncio
async def test_select_endpoint_failure_is_friendly_and_non_mutating(monkeypatch):
    agent = _FakeAgent()
    handler = ConfigHandler(agent)
    monkeypatch.setattr(handler, "_available_main_endpoint_names", lambda: "primary, backup")

    result = await handler.handle(
        "system_config",
        {"action": "select_endpoint", "endpoint_name": "missing"},
    )

    assert "无法切换到端点" in result
    assert "primary, backup" in result
    assert agent.brain.switch_calls[0]["conversation_id"] == "chat-123"
