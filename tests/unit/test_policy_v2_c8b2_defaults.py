"""C8b-2 — defaults / config-subsection migration regression tests.

Scope:
1. ``policy_v2/defaults.py`` 三个路径函数 + ``DEFAULT_BLOCKED_COMMANDS``
   常量与 v1 ``policy.py`` 中等价导出 byte-for-byte 一致（迁移没改语义）。
2. v1 ``policy.py`` 中的 ``_default_*_paths`` / ``_DEFAULT_BLOCKED_COMMANDS``
   仍然 importable 且 delegate 到 v2（旧 caller 不 break）。
3. ``audit_logger.get_audit_logger()`` / ``checkpoint.get_checkpoint_manager()``
   能在"v2 全局引擎已初始化但 v1 PolicyEngine 不存在"情境下正确初始化
   （证明已脱离 v1 依赖）。
4. ``reset_policy_v2_layer()`` 同时重置 v2 引擎 + audit logger（hot reload
   契约）。
5. ``shell_risk.DEFAULT_BLOCKED_COMMANDS`` 与 ``defaults.DEFAULT_BLOCKED_COMMANDS``
   保持单一 source of truth：内容完全一致。
"""

from __future__ import annotations

from openakita.core.policy_v2 import (
    default_blocked_commands,
    default_controlled_paths,
    default_forbidden_paths,
    default_protected_paths,
)
from openakita.core.policy_v2.defaults import DEFAULT_BLOCKED_COMMANDS
from openakita.core.policy_v2.shell_risk import (
    DEFAULT_BLOCKED_COMMANDS as SHELL_RISK_DEFAULT_BLOCKED_COMMANDS,
)


class TestDefaultsParityWithV1:
    """确保 v2 defaults.py 与 v1 policy.py 旧导出完全等价。"""

    def test_default_protected_paths_parity(self) -> None:
        from openakita.core.policy import _default_protected_paths

        assert default_protected_paths() == _default_protected_paths()

    def test_default_forbidden_paths_parity(self) -> None:
        from openakita.core.policy import _default_forbidden_paths

        assert default_forbidden_paths() == _default_forbidden_paths()

    def test_default_controlled_paths_parity(self) -> None:
        from openakita.core.policy import _default_controlled_paths

        assert default_controlled_paths() == _default_controlled_paths()

    def test_default_blocked_commands_parity_v1_constant(self) -> None:
        from openakita.core.policy import _DEFAULT_BLOCKED_COMMANDS

        assert default_blocked_commands() == _DEFAULT_BLOCKED_COMMANDS

    def test_default_blocked_commands_single_source_of_truth(self) -> None:
        """defaults.DEFAULT_BLOCKED_COMMANDS 直接从 shell_risk 重导出。"""
        assert tuple(SHELL_RISK_DEFAULT_BLOCKED_COMMANDS) == DEFAULT_BLOCKED_COMMANDS


class TestDefaultsListMutationSafety:
    """v1 旧 caller 直接 ``.append`` 到返回值的现象很常见；v2 必须每次
    返回新 list，否则共享 mutation 会污染下次返回值。"""

    def test_protected_paths_returns_fresh_list(self) -> None:
        a = default_protected_paths()
        a.append("/tampered")
        b = default_protected_paths()
        assert "/tampered" not in b

    def test_forbidden_paths_returns_fresh_list(self) -> None:
        a = default_forbidden_paths()
        a.append("/tampered")
        b = default_forbidden_paths()
        assert "/tampered" not in b

    def test_controlled_paths_returns_fresh_list(self) -> None:
        a = default_controlled_paths()
        a.append("/tampered")
        b = default_controlled_paths()
        assert "/tampered" not in b

    def test_blocked_commands_returns_fresh_list(self) -> None:
        a = default_blocked_commands()
        a.append("tamper-cmd")
        b = default_blocked_commands()
        assert "tamper-cmd" not in b


class TestSubsystemsReadV2Config:
    """audit_logger / checkpoint 在 C8b-2 后只依赖 v2 全局引擎。"""

    @staticmethod
    def _install_v2_config(cfg) -> None:
        """Helper：把自定义 PolicyConfigV2 注入全局单例，绕开 YAML 加载。"""
        from openakita.core.policy_v2.engine import build_engine_from_config
        from openakita.core.policy_v2.global_engine import set_engine_v2

        engine = build_engine_from_config(cfg)
        set_engine_v2(engine, cfg)

    def test_audit_logger_reads_v2_audit_config(self, tmp_path) -> None:
        """构造一个 PolicyConfigV2，audit.enabled=True + log_path 指向
        临时目录，验证 ``get_audit_logger()`` 返回的对象 path/enabled 正确。"""
        from openakita.core.audit_logger import reset_audit_logger
        from openakita.core.policy_v2.global_engine import reset_engine_v2
        from openakita.core.policy_v2.schema import AuditConfig, PolicyConfigV2

        custom_path = str(tmp_path / "audit.jsonl")
        cfg = PolicyConfigV2(
            audit=AuditConfig(enabled=True, log_path=custom_path),
        )
        try:
            self._install_v2_config(cfg)
            reset_audit_logger()
            from openakita.core.audit_logger import get_audit_logger

            log = get_audit_logger()
            assert str(log._path) == custom_path
            assert log._enabled is True
        finally:
            reset_audit_logger()
            reset_engine_v2()

    def test_audit_logger_disabled_when_v2_audit_disabled(self, tmp_path) -> None:
        from openakita.core.audit_logger import reset_audit_logger
        from openakita.core.policy_v2.global_engine import reset_engine_v2
        from openakita.core.policy_v2.schema import AuditConfig, PolicyConfigV2

        cfg = PolicyConfigV2(
            audit=AuditConfig(enabled=False, log_path=str(tmp_path / "x.jsonl")),
        )
        try:
            self._install_v2_config(cfg)
            reset_audit_logger()
            from openakita.core.audit_logger import get_audit_logger

            log = get_audit_logger()
            assert log._enabled is False
        finally:
            reset_audit_logger()
            reset_engine_v2()

    def test_checkpoint_manager_reads_v2_checkpoint_config(self, tmp_path) -> None:
        from openakita.core.policy_v2.global_engine import reset_engine_v2
        from openakita.core.policy_v2.schema import CheckpointConfig, PolicyConfigV2

        custom_dir = str(tmp_path / "snapshots")
        cfg = PolicyConfigV2(
            checkpoint=CheckpointConfig(
                enabled=True, snapshot_dir=custom_dir, max_snapshots=42
            ),
        )

        import openakita.core.checkpoint as ck_mod

        try:
            self._install_v2_config(cfg)
            ck_mod._global_checkpoint_mgr = None
            mgr = ck_mod.get_checkpoint_manager()
            assert str(mgr._base_dir) == custom_dir
            assert mgr._max_snapshots == 42
        finally:
            ck_mod._global_checkpoint_mgr = None
            reset_engine_v2()


class TestResetPolicyV2Layer:
    """``reset_policy_v2_layer()`` 必须重置 v2 engine + audit_logger。"""

    def test_reset_clears_v2_engine_singleton(self) -> None:
        from openakita.core.policy_v2.global_engine import (
            get_engine_v2,
            is_initialized,
            reset_policy_v2_layer,
        )

        # ensure engine is built
        get_engine_v2()
        assert is_initialized() is True

        reset_policy_v2_layer()
        assert is_initialized() is False

    def test_reset_clears_audit_logger_singleton(self) -> None:
        from openakita.core.audit_logger import get_audit_logger
        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        # warm up audit
        log_a = get_audit_logger()
        # call reset
        reset_policy_v2_layer()
        # next get returns a fresh instance (different identity)
        log_b = get_audit_logger()
        assert log_a is not log_b


class TestConfigPyDoesNotImportV1Internals:
    """config.py 不应再 import v1 ``policy.py`` 私有符号
    （``_default_*_paths`` / ``_DEFAULT_BLOCKED_COMMANDS`` /
    ``reset_policy_engine``）。``get_policy_engine`` 仍允许（公开 API，
    ``_frontend_mode`` shim 等到 C8b-5 折叠）。"""

    def test_no_default_paths_import(self) -> None:
        from pathlib import Path

        path = Path(__file__).parent.parent.parent / "src" / "openakita" / "api" / "routes" / "config.py"
        text = path.read_text(encoding="utf-8")
        assert "_default_protected_paths" not in text, (
            "config.py 仍 import _default_protected_paths（v1 私有），"
            "应使用 policy_v2.defaults.default_protected_paths"
        )
        assert "_default_forbidden_paths" not in text
        assert "_default_controlled_paths" not in text
        assert "_DEFAULT_BLOCKED_COMMANDS" not in text

    def test_no_reset_policy_engine_import(self) -> None:
        from pathlib import Path

        path = Path(__file__).parent.parent.parent / "src" / "openakita" / "api" / "routes" / "config.py"
        text = path.read_text(encoding="utf-8")
        assert "reset_policy_engine" not in text, (
            "config.py 仍 import reset_policy_engine（v1）；"
            "应使用 policy_v2.global_engine.reset_policy_v2_layer"
        )
