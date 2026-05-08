"""``Agent.propagate_skill_change`` 的单元测试。

用轻量 FakeAgent + 绑定真实方法的方式，避免初始化整个 Agent（LLM / 工具 / 插件 / DB）。
验证：
- 每一步刷新都被按预期调用（且顺序无关紧要，但依赖关系正确）
- ``rescan=False`` 时跳过 ``loader.load_all``
- 任一中间步骤抛异常不影响后续步骤（try/except 隔离）
- ``action`` 参数为 ``SkillEvent`` / ``str`` / ``None`` / 其他类型时均正确广播
- ``_context.system`` 仅在原值非空时被重建
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# FakeAgent 构造
# ---------------------------------------------------------------------------


def _build_fake_agent(*, initialized: bool = True, ctx_system: str | None = "old-prompt"):
    """构造一个仅含 propagate_skill_change 所需字段的假 agent。

    绑定真实的 ``Agent.propagate_skill_change`` 方法（unbound 版本）以便验证真实逻辑。
    """
    from openakita.core.agent import Agent

    fake = SimpleNamespace()
    fake.skill_loader = MagicMock()
    fake.skill_loader.compute_effective_allowlist = MagicMock(
        return_value={"a", "b"}
    )
    fake.skill_loader.prune_external_by_allowlist = MagicMock()
    fake.skill_loader.load_all = MagicMock(return_value=2)

    fake.skill_registry = MagicMock()
    fake.skill_registry.list_enabled = MagicMock(return_value=[])

    fake.skill_catalog = MagicMock()
    fake.skill_catalog.invalidate_cache = MagicMock()
    fake.skill_catalog.generate_catalog = MagicMock(return_value="NEW-CATALOG")

    fake._skill_catalog_text = "OLD-CATALOG"
    fake._initialized = initialized
    fake._context = SimpleNamespace(system=ctx_system)
    fake._build_system_prompt = MagicMock(return_value="NEW-PROMPT")
    fake._invalidate_system_prompt_cache = MagicMock()
    fake._update_skill_tools = MagicMock()
    fake._sync_available_toolsets = MagicMock()
    fake._skill_activation = MagicMock()
    fake._skill_activation.clear = MagicMock()
    fake._skill_activation.register_conditional = MagicMock()

    # 绑定真实的 propagate_skill_change：MethodType 让 self=fake
    import types

    fake.propagate_skill_change = types.MethodType(
        Agent.propagate_skill_change, fake
    )

    return fake


# ---------------------------------------------------------------------------
# 通用 patch：隔离外部模块副作用
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_env(monkeypatch, tmp_path):
    """拦截 propagate_skill_change 内部会调用的模块级依赖。"""
    from openakita.config import settings as real_settings

    monkeypatch.setattr(real_settings, "project_root", tmp_path, raising=False)

    clear_caches_mock = MagicMock()
    read_allowlist_mock = MagicMock(return_value=(tmp_path / "data" / "skills.json", {"a"}))
    collect_skills_mock = MagicMock(return_value=set())
    notify_pools_mock = MagicMock()
    notify_changed_mock = MagicMock()

    monkeypatch.setattr(
        "openakita.skills.watcher.clear_all_skill_caches", clear_caches_mock
    )
    monkeypatch.setattr(
        "openakita.skills.allowlist_io.read_allowlist", read_allowlist_mock
    )
    monkeypatch.setattr(
        "openakita.skills.preset_utils.collect_preset_referenced_skills",
        collect_skills_mock,
    )
    monkeypatch.setattr(
        "openakita.core.agent.Agent.notify_pools_skills_changed",
        staticmethod(notify_pools_mock),
    )
    monkeypatch.setattr(
        "openakita.skills.events.notify_skills_changed", notify_changed_mock
    )

    return SimpleNamespace(
        clear_caches=clear_caches_mock,
        read_allowlist=read_allowlist_mock,
        collect_skills=collect_skills_mock,
        notify_pools=notify_pools_mock,
        notify_changed=notify_changed_mock,
    )


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_full_refresh_steps_called(self, patched_env):
        from openakita.skills.events import SkillEvent

        agent = _build_fake_agent()
        agent.propagate_skill_change(SkillEvent.INSTALL)

        patched_env.clear_caches.assert_called_once()
        agent.skill_loader.load_all.assert_called_once()
        patched_env.read_allowlist.assert_called_once()
        agent.skill_loader.compute_effective_allowlist.assert_called_once_with({"a"})
        agent.skill_loader.prune_external_by_allowlist.assert_called_once()
        agent.skill_catalog.invalidate_cache.assert_called_once()
        agent.skill_catalog.generate_catalog.assert_called_once()
        assert agent._skill_catalog_text == "NEW-CATALOG"
        agent._invalidate_system_prompt_cache.assert_called_once_with("skill change")
        agent._update_skill_tools.assert_called_once()
        agent._skill_activation.clear.assert_called_once()
        patched_env.notify_pools.assert_called_once()
        patched_env.notify_changed.assert_called_once_with("install")
        assert agent._context.system == "NEW-PROMPT"

    def test_uses_public_preset_utils_helper(self, patched_env):
        agent = _build_fake_agent()
        agent.propagate_skill_change("reload")

        patched_env.collect_skills.assert_called_once()
        assert not hasattr(agent, "_collect_preset_referenced_skills")

    def test_rescan_false_skips_load_all(self, patched_env):
        agent = _build_fake_agent()
        agent.propagate_skill_change("enable", rescan=False)

        agent.skill_loader.load_all.assert_not_called()
        # 其他步骤仍然执行
        patched_env.clear_caches.assert_called_once()
        agent.skill_catalog.generate_catalog.assert_called_once()
        agent._invalidate_system_prompt_cache.assert_called_once_with("skill change")
        patched_env.notify_pools.assert_called_once()
        patched_env.notify_changed.assert_called_once_with("enable")


# ---------------------------------------------------------------------------
# action 兼容
# ---------------------------------------------------------------------------


class TestActionArgument:
    def test_skill_event_enum(self, patched_env):
        from openakita.skills.events import SkillEvent

        agent = _build_fake_agent()
        agent.propagate_skill_change(SkillEvent.UNINSTALL)
        patched_env.notify_changed.assert_called_once_with("uninstall")

    def test_plain_string(self, patched_env):
        agent = _build_fake_agent()
        agent.propagate_skill_change("custom-action")
        patched_env.notify_changed.assert_called_once_with("custom-action")

    def test_none_falls_back_to_reload(self, patched_env):
        agent = _build_fake_agent()
        agent.propagate_skill_change(None)
        patched_env.notify_changed.assert_called_once_with("reload")

    def test_empty_string_falls_back_to_reload(self, patched_env):
        agent = _build_fake_agent()
        agent.propagate_skill_change("")
        patched_env.notify_changed.assert_called_once_with("reload")


# ---------------------------------------------------------------------------
# _context.system 重建条件
# ---------------------------------------------------------------------------


class TestSystemPromptRebuild:
    def test_rebuilds_when_ctx_system_truthy(self, patched_env):
        agent = _build_fake_agent(ctx_system="old")
        agent.propagate_skill_change("reload")
        agent._build_system_prompt.assert_called_once()
        assert agent._context.system == "NEW-PROMPT"

    def test_skips_when_ctx_system_empty(self, patched_env):
        agent = _build_fake_agent(ctx_system="")
        agent.propagate_skill_change("reload")
        agent._build_system_prompt.assert_not_called()
        assert agent._context.system == ""

    def test_skips_when_not_initialized(self, patched_env):
        agent = _build_fake_agent(initialized=False, ctx_system="old")
        agent.propagate_skill_change("reload")
        agent._build_system_prompt.assert_not_called()

    def test_skips_when_ctx_is_none(self, patched_env):
        agent = _build_fake_agent()
        agent._context = None
        agent.propagate_skill_change("reload")
        agent._build_system_prompt.assert_not_called()


# ---------------------------------------------------------------------------
# 异常隔离：任一步骤抛异常，不影响后续步骤
# ---------------------------------------------------------------------------


class TestExceptionIsolation:
    def test_load_all_failure_does_not_abort(self, patched_env):
        agent = _build_fake_agent()
        agent.skill_loader.load_all.side_effect = RuntimeError("boom")

        agent.propagate_skill_change("reload")

        # 后续步骤仍然执行
        agent.skill_catalog.generate_catalog.assert_called_once()
        patched_env.notify_pools.assert_called_once()
        patched_env.notify_changed.assert_called_once()

    def test_allowlist_apply_failure_does_not_abort(self, patched_env):
        agent = _build_fake_agent()
        patched_env.read_allowlist.side_effect = OSError("io error")

        agent.propagate_skill_change("reload")

        agent.skill_catalog.generate_catalog.assert_called_once()
        patched_env.notify_pools.assert_called_once()

    def test_catalog_rebuild_failure_does_not_abort(self, patched_env):
        agent = _build_fake_agent()
        agent.skill_catalog.generate_catalog.side_effect = ValueError("bad catalog")

        agent.propagate_skill_change("reload")

        agent._invalidate_system_prompt_cache.assert_called_once_with("skill change")
        agent._update_skill_tools.assert_called_once()
        patched_env.notify_pools.assert_called_once()
        patched_env.notify_changed.assert_called_once()

    def test_prompt_cache_invalidation_failure_does_not_abort(self, patched_env):
        agent = _build_fake_agent()
        agent._invalidate_system_prompt_cache.side_effect = RuntimeError("cache fail")

        agent.propagate_skill_change("reload")

        agent._update_skill_tools.assert_called_once()
        patched_env.notify_pools.assert_called_once()
        patched_env.notify_changed.assert_called_once()

    def test_update_skill_tools_failure_does_not_abort(self, patched_env):
        agent = _build_fake_agent()
        agent._update_skill_tools.side_effect = RuntimeError("tool sync failed")

        agent.propagate_skill_change("reload")

        patched_env.notify_pools.assert_called_once()
        patched_env.notify_changed.assert_called_once()

    def test_activation_refresh_failure_does_not_abort(self, patched_env):
        agent = _build_fake_agent()
        agent._skill_activation.clear.side_effect = RuntimeError("activation fail")

        agent.propagate_skill_change("reload")

        patched_env.notify_pools.assert_called_once()
        patched_env.notify_changed.assert_called_once()

    def test_system_prompt_failure_does_not_abort(self, patched_env):
        agent = _build_fake_agent()
        agent._build_system_prompt.side_effect = RuntimeError("prompt fail")

        agent.propagate_skill_change("reload")

        patched_env.notify_pools.assert_called_once()
        patched_env.notify_changed.assert_called_once()

    def test_pool_notify_failure_does_not_abort_event(self, patched_env):
        agent = _build_fake_agent()
        patched_env.notify_pools.side_effect = RuntimeError("pool fail")

        agent.propagate_skill_change("reload")

        patched_env.notify_changed.assert_called_once()

    def test_notify_changed_failure_is_swallowed(self, patched_env):
        """最后一步 notify_skills_changed 失败也不应抛出异常到调用方。"""
        agent = _build_fake_agent()
        patched_env.notify_changed.side_effect = RuntimeError("event bus down")

        # 不应抛
        agent.propagate_skill_change("reload")


# ---------------------------------------------------------------------------
# 条件激活注册表刷新
# ---------------------------------------------------------------------------


class TestActivationRefresh:
    def test_conditional_skills_registered(self, patched_env):
        agent = _build_fake_agent()

        # 构造两个有 paths 的 skill 与一个无 paths 的 skill
        with_paths = SimpleNamespace(paths=["src/**"], fallback_for_toolsets=[])
        with_fallback = SimpleNamespace(paths=[], fallback_for_toolsets=["web"])
        plain = SimpleNamespace(paths=[], fallback_for_toolsets=[])
        agent.skill_registry.list_enabled = MagicMock(
            return_value=[with_paths, with_fallback, plain]
        )

        agent.propagate_skill_change("reload")

        # 只有 with_paths 和 with_fallback 被注册为条件激活
        calls = agent._skill_activation.register_conditional.call_args_list
        assert len(calls) == 2
        agent._sync_available_toolsets.assert_called_once()

    def test_no_activation_manager_is_safe(self, patched_env):
        agent = _build_fake_agent()
        del agent._skill_activation

        # 不应抛
        agent.propagate_skill_change("reload")

        # 其他步骤依然执行
        patched_env.notify_pools.assert_called_once()
