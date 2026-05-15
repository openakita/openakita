"""
Global pytest fixtures for OpenAkita test suite.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Workaround: On Windows, platform._wmi_query() can hang when the WMI
# service is slow/unresponsive (e.g. after a crash).  Faker triggers this
# during pytest plugin collection via platform.system().  Pre-populate the
# uname cache so the real WMI call is never needed.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    _orig_wmi = getattr(platform, "_wmi_query", None)
    if _orig_wmi is not None:
        platform._wmi_query = lambda *a, **k: ("10.0.26200", 1, "Multiprocessor Free", 0, 0)
        platform.system()          # populate cache
        platform._wmi_query = _orig_wmi   # restore

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tests.fixtures.mock_llm import MockBrain, MockLLMClient, MockResponse


@pytest.fixture
def mock_llm_client() -> MockLLMClient:
    """A fresh MockLLMClient with an empty response queue."""
    client = MockLLMClient()
    client.set_default_response("Default mock response")
    return client


@pytest.fixture
def mock_brain(mock_llm_client: MockLLMClient) -> MockBrain:
    """A MockBrain backed by the mock_llm_client fixture."""
    return MockBrain(mock_llm_client)


@pytest.fixture
def test_session():
    """A clean test Session with no messages."""
    from tests.fixtures.factories import create_test_session
    return create_test_session()


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """A temporary workspace directory with standard subdirs."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "memory").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "identity").mkdir()
    return tmp_path


@pytest.fixture
def test_settings(tmp_workspace: Path):
    """Test-specific Settings pointing to temp dirs, no external dependencies."""
    os.environ["OPENAKITA_PROJECT_ROOT"] = str(tmp_workspace)
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-placeholder")

    from openakita.config import Settings
    settings = Settings(
        project_root=tmp_workspace,
        database_path=str(tmp_workspace / "data" / "agent.db"),
        log_dir=str(tmp_workspace / "logs"),
        log_level="WARNING",
        max_iterations=10,
    )
    yield settings

    os.environ.pop("OPENAKITA_PROJECT_ROOT", None)


@pytest.fixture
def mock_response_factory():
    """Factory fixture for creating MockResponse instances."""
    def _create(
        content: str = "",
        tool_calls: list[dict] | None = None,
        reasoning_content: str | None = None,
    ) -> MockResponse:
        return MockResponse(
            content=content,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        )
    return _create


# ---------------------------------------------------------------------------
# Helpers for the (text, ConfigHint | None) tuple return type introduced by
# the web_search provider refactor (see src/openakita/tools/tool_hints.py).
#
# Existing tests that only care about the LLM-facing text can use these to
# stay readable instead of repeating ``text, _ = await executor.execute_tool(...)``
# everywhere. New tests that DO care about the hint (e.g. the
# WebSearchHandler tests) should unpack the tuple directly.
# ---------------------------------------------------------------------------


async def call_tool_text(executor, tool_name, tool_input, **kwargs) -> str:
    """Run ``executor.execute_tool`` and return only the LLM-facing text.

    Drops the ``ConfigHint`` part of the tuple. Use in tests that pre-date
    the hint side-channel and only assert on the result string.
    """
    text, _hint = await executor.execute_tool(tool_name, tool_input, **kwargs)
    return text


async def call_tool_with_policy_text(
    executor, tool_name, tool_input, policy_result, **kwargs
) -> str:
    """Run ``execute_tool_with_policy`` and return only the text portion."""
    text, _hint = await executor.execute_tool_with_policy(
        tool_name, tool_input, policy_result, **kwargs
    )
    return text


@pytest.fixture
def call_tool_text_helper():
    """Fixture wrapper around :func:`call_tool_text` for tests that prefer DI."""
    return call_tool_text


# ---------------------------------------------------------------------------
# C8b-1: 自动隔离 process-wide policy v2 singletons across tests.
#
# ``DeathSwitchTracker`` 累积 consecutive_denials；不重置时一个 test 的连续
# DENY 会让后续 test 误中 readonly_mode（StopIteration / 误 DENY）。
# ``SkillAllowlistManager`` 同理——一个 test 给 'foo' skill 加了 'bar' tool
# 之后下个 test 期待 'bar' is_allowed 仍 False。
#
# Bus（``UIConfirmBus``）已有 reset 路径但目前由各 test 自己管理；这里只统
# 一管理 C8b-1 新加的两个 singleton，避免污染其他 test 既有的 UIConfirmBus
# 显式 setup/teardown。
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_policy_v2_singletons():
    """每个 test 前后清空 DeathSwitch + SkillAllowlist singleton 状态。

    fail-soft：模块未导入时静默跳过（policy_v2 不在所有 test 范围内）。
    """
    try:
        from openakita.core.policy_v2.death_switch import (
            reset_death_switch_tracker,
        )
        from openakita.core.policy_v2.session_allowlist import (
            reset_session_allowlist_manager,
        )
        from openakita.core.policy_v2.skill_allowlist import (
            reset_skill_allowlist_manager,
        )
    except Exception:
        yield
        return

    # 用 reset_*_singleton() 而非 .reset() / .clear()，因为 .reset() 故意保留
    # ``total_denials`` 作为 lifetime 计数（与 v1 parity）；test 之间需要完全
    # 隔离否则一个测试的 10 个 deny 会被下个测试看到。
    # C8b-3: SessionAllowlistManager 同样是 process-wide ephemeral，必须按 test
    # 隔离——否则一个 test ``apply_resolution(allow_session)`` 的副作用会让
    # 下一个 test 期待"该工具仍需 confirm"的断言失败。
    reset_death_switch_tracker()
    reset_skill_allowlist_manager()
    reset_session_allowlist_manager()
    try:
        yield
    finally:
        reset_death_switch_tracker()
        reset_skill_allowlist_manager()
        reset_session_allowlist_manager()

