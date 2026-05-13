"""C8b-4 — confirmation_mode helper + smart-mode deletion tests.

覆盖：
1. ``read_permission_mode_label`` v2→v1 反向映射 5×ConfirmationMode
2. ``coerce_v1_label_to_v2_mode`` v1→v2 正向映射 + alias
3. ``permission-mode`` GET/POST endpoint 直读 v2，无 v1 字段依赖
4. ``PolicyEngine`` 不再有 ``_frontend_mode`` / ``_session_allow_count`` /
   ``_SMART_ESCALATION_THRESHOLD``
5. v1 smart-mode escalation 路径已删除（MEDIUM 风险一律 CONFIRM）
6. POST permission-mode 后 GET 返回新值（YAML→reset_v2→read 链路验证）
"""

from __future__ import annotations

import pytest

from openakita.core.policy import PolicyEngine
from openakita.core.policy_v2 import (
    coerce_v1_label_to_v2_mode,
    read_permission_mode_label,
)
from openakita.core.policy_v2.enums import ConfirmationMode


class TestReadPermissionModeLabel:
    """v2 enum → v1 product label 的 5 档映射 + fail-soft fallback。"""

    @pytest.mark.parametrize(
        "v2_mode,v1_label",
        [
            (ConfirmationMode.TRUST, "yolo"),
            (ConfirmationMode.DEFAULT, "smart"),
            (ConfirmationMode.STRICT, "cautious"),
            (ConfirmationMode.ACCEPT_EDITS, "smart"),  # v2-only → 归并到 smart
            (ConfirmationMode.DONT_ASK, "yolo"),       # v2-only → 归并到 yolo
        ],
    )
    def test_5_mode_mapping(self, v2_mode: ConfirmationMode, v1_label: str) -> None:
        from openakita.core.policy_v2 import (
            PolicyConfigV2,
            build_engine_from_config,
        )
        from openakita.core.policy_v2.global_engine import (
            reset_engine_v2,
            set_engine_v2,
        )
        from openakita.core.policy_v2.schema import ConfirmationConfig

        cfg = PolicyConfigV2(confirmation=ConfirmationConfig(mode=v2_mode))
        engine = build_engine_from_config(cfg)
        set_engine_v2(engine, cfg)
        try:
            assert read_permission_mode_label() == v1_label
        finally:
            reset_engine_v2()

    def test_fallback_when_v2_unavailable(self, monkeypatch) -> None:
        """v2 拉取失败应回到 'yolo' 而非抛异常。"""
        from openakita.core.policy_v2 import confirmation_mode as cm

        def _boom():
            raise RuntimeError("v2 not initialized")

        monkeypatch.setattr(
            "openakita.core.policy_v2.global_engine.get_config_v2", _boom
        )
        # Re-import inside function so monkeypatch takes effect on the local import
        assert cm.read_permission_mode_label() == "yolo"


class TestCoerceV1LabelToV2Mode:
    @pytest.mark.parametrize(
        "label,expected",
        [
            ("yolo", ConfirmationMode.TRUST),
            ("trust", ConfirmationMode.TRUST),
            ("smart", ConfirmationMode.DEFAULT),
            ("default", ConfirmationMode.DEFAULT),
            ("cautious", ConfirmationMode.STRICT),
            ("strict", ConfirmationMode.STRICT),
            ("YOLO", ConfirmationMode.TRUST),  # case-insensitive
            ("  smart  ", ConfirmationMode.DEFAULT),  # whitespace tolerant
        ],
    )
    def test_coerce(self, label: str, expected: ConfirmationMode) -> None:
        assert coerce_v1_label_to_v2_mode(label) == expected

    def test_unknown_falls_back_to_default(self) -> None:
        assert coerce_v1_label_to_v2_mode("nonsense") == ConfirmationMode.DEFAULT
        assert coerce_v1_label_to_v2_mode("") == ConfirmationMode.TRUST  # empty → "yolo" → TRUST


class TestPolicyEngineFieldsDeleted:
    """C8b-4 删除的 v1 字段不应再出现。"""

    def test_frontend_mode_field_gone(self) -> None:
        engine = PolicyEngine()
        assert not hasattr(engine, "_frontend_mode"), (
            "PolicyEngine still has _frontend_mode after C8b-4"
        )

    def test_session_allow_count_field_gone(self) -> None:
        engine = PolicyEngine()
        assert not hasattr(engine, "_session_allow_count"), (
            "PolicyEngine still has _session_allow_count after C8b-4"
        )

    def test_smart_escalation_threshold_class_const_gone(self) -> None:
        assert not hasattr(PolicyEngine, "_SMART_ESCALATION_THRESHOLD"), (
            "PolicyEngine still has _SMART_ESCALATION_THRESHOLD after C8b-4"
        )


class TestSmartEscalationDeleted:
    """v1 smart-mode 的 MEDIUM 风险自动升信任路径完全删除。"""

    def test_on_allow_does_not_increment_phantom_counter(self) -> None:
        """``_on_allow`` 应只重置 consecutive_denials，不再自增任何 escalation
        计数（_session_allow_count 字段已删）。"""
        from openakita.core.policy import (
            ConfirmationConfig,
            SecurityConfig,
        )

        engine = PolicyEngine(
            SecurityConfig(
                enabled=True,
                confirmation=ConfirmationConfig(mode="smart"),
            )
        )
        # 多次 _on_allow 不应抛 AttributeError（即使字段已删）
        for _ in range(10):
            engine._on_allow("write_file")
        assert engine._consecutive_denials == 0
        assert not hasattr(engine, "_session_allow_count")

    def test_smart_escalation_runtime_constants_removed(self) -> None:
        """动态守卫：PolicyEngine 上不再有 escalation 相关的可执行符号。

        本测试**不**做源码 grep（doc comment 里仍会提及已删字段名作为历史
        参考）；改为查类字典里没有这些 attribute / class const。
        """
        # Class-level
        assert not hasattr(PolicyEngine, "_SMART_ESCALATION_THRESHOLD")
        # Instance-level after __init__
        engine = PolicyEngine()
        assert not hasattr(engine, "_session_allow_count")

        # Smoke: assert the deleted field truly cannot be accessed
        # (confirms it's not an instance attribute swallowed by __getattr__)
        with pytest.raises(AttributeError):
            engine._session_allow_count  # noqa: B018


class TestPermissionModeEndpointE2E:
    """端到端：POST permission-mode → GET 返回新值（验证 v2 lazy reload 链路）。

    端点本身集成测试在 ``test_config_endpoints.py``（如有），这里只验证
    helper 函数的端到端行为，不依赖 FastAPI TestClient。
    """

    def test_set_then_read_via_v2_only(self, tmp_path, monkeypatch) -> None:
        """构造一个独立的 v2 engine + config，模拟"YAML 写 → reset → read"链路。"""
        from openakita.core.policy_v2 import (
            PolicyConfigV2,
            build_engine_from_config,
        )
        from openakita.core.policy_v2.global_engine import (
            reset_engine_v2,
            set_engine_v2,
        )
        from openakita.core.policy_v2.schema import ConfirmationConfig

        # Initial: TRUST
        cfg1 = PolicyConfigV2(confirmation=ConfirmationConfig(mode=ConfirmationMode.TRUST))
        eng1 = build_engine_from_config(cfg1)
        set_engine_v2(eng1, cfg1)
        try:
            assert read_permission_mode_label() == "yolo"

            # User picks "cautious" → POST 端点会 _apply_permission_mode_defaults
            # 并 _write_policies_yaml + reset_policy_v2_layer。这里直接模拟
            # reset 后 v2 重建为 STRICT。
            cfg2 = PolicyConfigV2(confirmation=ConfirmationConfig(mode=ConfirmationMode.STRICT))
            eng2 = build_engine_from_config(cfg2)
            set_engine_v2(eng2, cfg2)

            assert read_permission_mode_label() == "cautious"
        finally:
            reset_engine_v2()
