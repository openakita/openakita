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
    """C8b-4 删除的 v1 字段不应再出现。

    C8b-6b：v1 ``policy.py`` 整文件已删；最强断言就是模块不可导入。
    扫描"借尸还魂"则需要剥离 docstring/注释（v2 模块的设计 doc 中仍合法引用
    旧字段名作为历史记录），用 `_strip_comments_and_doc` 共享 helper。
    """

    @staticmethod
    def _strip_comments_and_doc(text: str) -> str:
        out: list[str] = []
        in_doc = False
        for raw in text.splitlines():
            triple_count = raw.count('"""') + raw.count("'''")
            if triple_count % 2 == 1:
                in_doc = not in_doc
                continue
            if triple_count >= 2 and not in_doc:
                continue
            if in_doc:
                continue
            if raw.lstrip().startswith("#"):
                continue
            out.append(raw)
        return "\n".join(out)

    @classmethod
    def _read_executable_v2_sources(cls) -> str:
        from pathlib import Path

        src_root = Path(__file__).resolve().parents[2] / "src" / "openakita"
        chunks: list[str] = []
        for py in sorted(src_root.rglob("*.py")):
            chunks.append(cls._strip_comments_and_doc(py.read_text(encoding="utf-8")))
        return "\n".join(chunks)

    def test_v1_module_fully_deleted(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            __import__("openakita.core.policy")

    def test_frontend_mode_field_assignment_gone(self) -> None:
        body = self._read_executable_v2_sources()
        assert "self._frontend_mode" not in body, (
            "self._frontend_mode 仍出现在可执行代码中"
        )

    def test_session_allow_count_field_gone(self) -> None:
        body = self._read_executable_v2_sources()
        assert "self._session_allow_count" not in body
        assert "_session_allow_count +=" not in body

    def test_smart_escalation_threshold_class_const_gone(self) -> None:
        body = self._read_executable_v2_sources()
        assert "_SMART_ESCALATION_THRESHOLD" not in body


class TestSmartEscalationDeleted:
    """v1 smart-mode MEDIUM 风险自动升信任路径完全删除。

    C8b-6b：原本通过实例化 PolicyEngine + 调 ``_on_allow`` 验证；v1 类已删，
    改为静态 + 整源码扫描（剥离 docstring/注释）确保 escalation 路径完全清除。
    """

    def test_no_smart_escalation_artifacts(self) -> None:
        body = TestPolicyEngineFieldsDeleted._read_executable_v2_sources()
        # smart_escalation 字符串作为 metadata key 不应出现
        assert "smart_escalation" not in body
        assert "_SMART_ESCALATION_THRESHOLD" not in body


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
