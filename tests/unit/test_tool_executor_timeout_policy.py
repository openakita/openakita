from openakita.config import settings
from openakita.core.tool_executor import ToolExecutor
from openakita.tools.handlers import SystemHandlerRegistry


def test_tool_executor_hard_timeout_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "tool_hard_timeout_seconds", 0)
    monkeypatch.setattr(settings, "long_running_tool_timeout_seconds", 0)
    executor = ToolExecutor(SystemHandlerRegistry())

    assert executor._hard_timeout_for_tool("read_file") == 0
    assert executor._hard_timeout_for_tool("run_shell") == 0


def test_tool_executor_hard_timeout_uses_user_configuration(monkeypatch):
    monkeypatch.setattr(settings, "tool_hard_timeout_seconds", 120)
    monkeypatch.setattr(settings, "long_running_tool_timeout_seconds", 1800)
    executor = ToolExecutor(SystemHandlerRegistry())

    assert executor._hard_timeout_for_tool("read_file") == 120
    assert executor._hard_timeout_for_tool("run_shell") == 1800
