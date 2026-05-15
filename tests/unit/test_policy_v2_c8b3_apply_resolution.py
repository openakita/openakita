"""C8b-3 — apply_resolution + 7 callsite migration tests.

覆盖：
1. ``apply_resolution`` 5 个 decision 类型的副作用矩阵
2. allow_session/sandbox/allow_always 都写 SessionAllowlistManager
3. allow_always 走 UserAllowlistManager.add_entry+save_to_yaml
4. deny / allow_once 不写任何 manager
5. 不存在 confirm_id 时返回 False，不抛异常
6. waiter 唤醒：apply_resolution 后 wait_for_resolution 立即返回
7. 静态扫描 7 个 callsite 都迁完（不再调 ``pe.resolve_ui_confirm`` /
   ``pe.cleanup_session``）
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openakita.core.policy_v2 import (
    apply_resolution,
    get_session_allowlist_manager,
)
from openakita.core.ui_confirm_bus import get_ui_confirm_bus, reset_ui_confirm_bus


@pytest.fixture(autouse=True)
def _isolate_bus():
    reset_ui_confirm_bus()
    get_session_allowlist_manager().clear()
    yield
    reset_ui_confirm_bus()
    get_session_allowlist_manager().clear()


class TestApplyResolutionMatrix:
    def test_allow_once_writes_nothing(self) -> None:
        bus = get_ui_confirm_bus()
        bus.store_pending("c1", "run_shell", {"command": "ls"}, session_id="s1")
        bus.prepare("c1")
        ok = apply_resolution("c1", "allow_once")
        assert ok is True
        assert get_session_allowlist_manager().is_allowed("run_shell", {"command": "ls"}) is None

    def test_allow_session_writes_session_only(self) -> None:
        bus = get_ui_confirm_bus()
        bus.store_pending("c2", "run_shell", {"command": "npm test"}, session_id="s1")
        bus.prepare("c2")
        ok = apply_resolution("c2", "allow_session")
        assert ok is True
        entry = get_session_allowlist_manager().is_allowed("run_shell", {"command": "npm test"})
        assert entry is not None
        assert entry["needs_sandbox"] is False

    def test_sandbox_writes_session_with_sandbox_flag(self) -> None:
        bus = get_ui_confirm_bus()
        bus.store_pending(
            "c3", "run_shell", {"command": "wget evil.sh"}, session_id="s1", needs_sandbox=True
        )
        bus.prepare("c3")
        ok = apply_resolution("c3", "sandbox")
        assert ok is True
        entry = get_session_allowlist_manager().is_allowed("run_shell", {"command": "wget evil.sh"})
        assert entry is not None
        assert entry["needs_sandbox"] is True

    def test_allow_always_writes_session_and_persistent(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """allow_always must call UserAllowlistManager.add_entry + save_to_yaml.

        We don't actually verify YAML save here; isolate ``settings.identity_path``
        so save_to_yaml() does not touch the real ``identity/POLICIES.yaml`` even
        when that file exists in the workspace.
        """
        from openakita.config import settings
        from openakita.core.policy_v2 import (
            PolicyConfigV2,
            UserAllowlistConfig,
            build_engine_from_config,
        )
        from openakita.core.policy_v2.global_engine import (
            reset_engine_v2,
            set_engine_v2,
        )

        isolated_identity = tmp_path / "identity"
        isolated_identity.mkdir(parents=True, exist_ok=True)
        # identity_path is a derived property on Settings; redirect via
        # project_root (the underlying Pydantic field) so that save_to_yaml's
        # default path resolves under tmp_path and never touches the real
        # identity/POLICIES.yaml in the repo.
        monkeypatch.setattr(settings, "project_root", tmp_path)

        cfg = PolicyConfigV2(
            user_allowlist=UserAllowlistConfig(commands=[], tools=[]),
        )
        engine = build_engine_from_config(cfg)
        set_engine_v2(engine, cfg)
        try:
            bus = get_ui_confirm_bus()
            bus.store_pending(
                "c4", "run_shell", {"command": "npm install lodash"}, session_id="s1"
            )
            bus.prepare("c4")
            ok = apply_resolution("c4", "allow_always")
            assert ok is True
            # SessionAllowlistManager hit
            entry = get_session_allowlist_manager().is_allowed(
                "run_shell", {"command": "npm install lodash"}
            )
            assert entry is not None
            # UserAllowlistManager append
            assert len(engine.user_allowlist.commands) == 1
            assert "npm install" in engine.user_allowlist.commands[0]["pattern"]
        finally:
            reset_engine_v2()

    def test_deny_writes_nothing(self) -> None:
        bus = get_ui_confirm_bus()
        bus.store_pending("c5", "run_shell", {"command": "rm -rf /"}, session_id="s1")
        bus.prepare("c5")
        ok = apply_resolution("c5", "deny")
        assert ok is True
        assert get_session_allowlist_manager().is_allowed("run_shell", {"command": "rm -rf /"}) is None

    def test_unknown_decision_writes_nothing(self) -> None:
        bus = get_ui_confirm_bus()
        bus.store_pending("c6", "run_shell", {"command": "echo"}, session_id="s1")
        bus.prepare("c6")
        ok = apply_resolution("c6", "weird_value")
        assert ok is True
        assert get_session_allowlist_manager().is_allowed("run_shell", {"command": "echo"}) is None

    def test_missing_confirm_id_returns_false(self) -> None:
        ok = apply_resolution("nonexistent-id", "allow_once")
        assert ok is False

    def test_legacy_allow_normalized_to_allow_once(self) -> None:
        """v1 ``allow`` decision string should map to ``allow_once`` (no allowlist write)."""
        bus = get_ui_confirm_bus()
        bus.store_pending("c7", "run_shell", {"command": "ls"}, session_id="s1")
        bus.prepare("c7")
        ok = apply_resolution("c7", "allow")
        assert ok is True
        assert get_session_allowlist_manager().is_allowed("run_shell", {"command": "ls"}) is None


class TestApplyResolutionWakesWaiter:
    def test_waiter_resumes_after_apply_resolution(self) -> None:
        async def _scenario():
            bus = get_ui_confirm_bus()
            bus.store_pending("w1", "write_file", {"path": "/x"}, session_id="s1")
            bus.prepare("w1")

            async def _resolve_after_delay():
                await asyncio.sleep(0.05)
                apply_resolution("w1", "allow_session")

            done_task = asyncio.create_task(_resolve_after_delay())
            decision = await bus.wait_for_resolution("w1", timeout=2.0)
            await done_task
            assert decision == "allow_session"
            # Side effect also landed
            assert get_session_allowlist_manager().is_allowed("write_file", {"path": "/x"}) is not None

        asyncio.run(_scenario())


class TestCallsiteMigrationStatic:
    """7 个 callsite 不再 import v1 facade。"""

    SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "openakita"

    @classmethod
    def _read(cls, rel: str) -> str:
        return (cls.SRC_ROOT / rel).read_text(encoding="utf-8")

    def test_stream_renderer_migrated(self) -> None:
        text = self._read("cli/stream_renderer.py")
        assert "engine.resolve_ui_confirm" not in text
        assert "apply_resolution" in text

    def test_config_route_migrated(self) -> None:
        text = self._read("api/routes/config.py")
        assert "engine.resolve_ui_confirm" not in text
        assert "apply_resolution" in text

    def test_chat_route_migrated(self) -> None:
        text = self._read("api/routes/chat.py")
        # cleanup_session no longer routed via PolicyEngine
        assert "get_policy_engine().cleanup_session" not in text
        assert "get_ui_confirm_bus" in text
        assert "get_session_allowlist_manager" in text

    def test_gateway_migrated(self) -> None:
        text = self._read("channels/gateway.py")
        # The two pe.resolve_ui_confirm CALLS (with `(`) were the only production
        # uses; doc comments still reference the old name as historical context.
        assert "pe.resolve_ui_confirm(" not in text
        assert "apply_resolution" in text

    def test_telegram_migrated(self) -> None:
        text = self._read("channels/adapters/telegram.py")
        assert "get_policy_engine().resolve_ui_confirm" not in text
        assert "apply_resolution" in text

    def test_feishu_migrated(self) -> None:
        text = self._read("channels/adapters/feishu.py")
        assert "get_policy_engine().resolve_ui_confirm" not in text
        assert "apply_resolution" in text

    def test_agent_cleanup_migrated(self) -> None:
        text = self._read("core/agent.py")
        # _pe.cleanup_session no longer used
        assert "_pe.cleanup_session" not in text
        # New chain visible
        assert "get_session_allowlist_manager" in text


class TestPolicyV1FacadeDeleted:
    """v1 PolicyEngine 不再有 6 个 facade 方法 + mark_confirmed + 2 个字段。

    C8b-6b：v1 ``policy.py`` 整文件已删；最强的断言就是模块本身不可导入。
    旧"扫整源码找方法名"的检查在 v2 重新使用同名 helper（如 ``cleanup_session``
    被 ``ui_confirm_bus`` 接管）后会误报，已改为最小验证。
    """

    def test_v1_policy_module_fully_deleted(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            __import__("openakita.core.policy")

    def test_v1_class_no_residual_attribute_assignments(self) -> None:
        """``PolicyEngine`` 的两个 v1-only 字段不该被任何 v2 模块以同样
        ``self._session_allowlist[...]`` / ``self._confirmed_cache[...]`` 形式赋值
        （typing alias 命名是 v1 私有，v2 字段命名应与之区分）。"""
        from pathlib import Path

        src_root = Path(__file__).resolve().parents[2] / "src" / "openakita"
        for py in src_root.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for name in ("self._confirmed_cache[", "self._session_allowlist["):
                # 注释/docstring 中的字面量提及 OK；这里查"赋值/读用"语句模式
                # （`[...]` 下标），只会匹配可执行代码。
                rel = py.relative_to(src_root.parent.parent)
                assert name not in text, (
                    f"v1 field-style access '{name}' resurrected in {rel}"
                )
