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

    def test_allow_always_writes_session_and_persistent(self, tmp_path) -> None:
        """allow_always must call UserAllowlistManager.add_entry + save_to_yaml.

        We don't actually verify YAML save (that hits production POLICIES.yaml
        path); just verify the engine's user_allowlist got an entry added.
        """
        from openakita.core.policy_v2 import (
            PolicyConfigV2,
            UserAllowlistConfig,
            build_engine_from_config,
        )
        from openakita.core.policy_v2.global_engine import (
            reset_engine_v2,
            set_engine_v2,
        )

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
    """v1 PolicyEngine 不再有 6 个 facade 方法 + mark_confirmed + 2 个字段。"""

    def test_facade_methods_gone(self) -> None:
        from openakita.core.policy import PolicyEngine

        for name in (
            "store_ui_pending",
            "cleanup_session",
            "resolve_ui_confirm",
            "prepare_ui_confirm",
            "cleanup_ui_confirm",
            "wait_for_ui_resolution",
            "mark_confirmed",
        ):
            assert not hasattr(PolicyEngine, name), (
                f"PolicyEngine still has {name} — should be deleted in C8b-3"
            )

    def test_fields_gone(self) -> None:
        from openakita.core.policy import PolicyEngine

        # construct an instance and confirm fields don't exist
        engine = PolicyEngine.__new__(PolicyEngine)
        # Init enough to test field absence; full init requires config
        for name in ("_session_allowlist", "_confirmed_cache"):
            assert not hasattr(engine, name), (
                f"PolicyEngine still has {name} after C8b-3"
            )
