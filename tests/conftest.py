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
        platform.system()  # populate cache
        platform._wmi_query = _orig_wmi  # restore

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
# 全局禁用桌面通知 - 防止 pytest 误弹 Windows Toast / macOS / Linux 系统通知
#
# 背景：tests/unit/test_scheduler_executor_status.py 等测试通过 ``trigger_now``
# 真实执行 ``TaskExecutor.execute``，链路会触达 ``_send_end_notification``，
# 后者会调用 ``notify_task_completed_async`` 真去 PowerShell 弹 Toast。
# 表象是每跑一次 scheduler 测试就在桌面右下角连续弹好几条
# "✅ OpenAkita 任务完成 / daily research"，体感极差且查不到源头。
#
# 这里在 autouse fixture 中把整个 desktop_notify 模块的发送函数替换成 no-op，
# 同时把 settings.desktop_notify_enabled 关掉作为双保险。
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _disable_desktop_notifications(monkeypatch):
    """禁止测试过程中弹出真实的桌面通知。

    Phase 2 commit 11 把 ``desktop_notify`` 模块从 ``openakita.core``
    搬到了 ``openakita.agent``，旧路径仅保留 re-export shim。要让
    no-op 保护无论调用方走哪条 import 路径都生效，需要同时
    monkeypatch 两个模块的 send/notify 函数名字。
    """
    # fail-soft：模块未导入时静默跳过（pytest collection 阶段可能还没 import）
    modules: list = []
    try:
        from openakita.core import desktop_notify as _dn_core

        modules.append(_dn_core)
    except Exception:
        pass
    try:
        from openakita.agent import desktop_notify as _dn_agent

        modules.append(_dn_agent)
    except Exception:
        pass

    if not modules:
        return

    async def _noop_async(*_args, **_kwargs):
        return False

    def _noop_sync(*_args, **_kwargs):
        return False

    for _dn in modules:
        monkeypatch.setattr(_dn, "send_desktop_notification", _noop_sync, raising=False)
        monkeypatch.setattr(_dn, "send_desktop_notification_async", _noop_async, raising=False)
        monkeypatch.setattr(_dn, "notify_task_completed", _noop_sync, raising=False)
        monkeypatch.setattr(_dn, "notify_task_completed_async", _noop_async, raising=False)

    # 双保险：把 settings.desktop_notify_enabled 也置为 False，覆盖任何动态导入
    try:
        from openakita.config import settings as _settings

        monkeypatch.setattr(_settings, "desktop_notify_enabled", False, raising=False)
    except Exception:
        pass


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
def _default_org_review_off(monkeypatch):
    """核心1/核心2: keep the new parent-executed review + rework loop OFF by
    default in tests.

    The review adds an extra LLM call per dispatched child and the rework loop
    can re-run a child, which would corrupt the deterministic canned-reply
    sequences (and call-count assertions) that the legacy orgs_v2 dispatch
    tests rely on. Production defaults the review ON (env unset). Tests that
    specifically exercise the review/rework behaviour opt in explicitly via
    ``monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "1")``.

    The knobs are read dynamically (see ``_runtime_agent_pipeline_executor``)
    so a plain ``setenv`` here is honoured without import-time freezing.
    """
    monkeypatch.setenv("OPENAKITA_ORG_REVIEW_ENABLED", "0")
    # Legacy XML-dispatch fixtures remain useful migration coverage. Production
    # defaults this off; new structured-delegation tests explicitly remove it.
    monkeypatch.setenv("OPENAKITA_ORG_LEGACY_TEXT_DISPATCH", "1")


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
