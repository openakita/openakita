"""``security_confirm`` SSE carries backend-owned decision-chain display data.

The frontend security-confirm modal must not understand policy enum labels,
step names, or raw reason/note strings. ``PolicyDecisionV2.to_ui_chain()``
therefore serializes both the raw audit fields and the structured ``display``
metadata that the UI renders directly.
"""

from __future__ import annotations

import json

from openakita.core.policy_v2.display import security_confirm_display
from openakita.core.policy_v2.enums import ApprovalClass, DecisionAction, DecisionSource
from openakita.core.policy_v2.models import DecisionStep, PolicyDecisionV2


def _make_decision(*, chain: list[DecisionStep]) -> PolicyDecisionV2:
    return PolicyDecisionV2(
        action=DecisionAction.CONFIRM,
        reason="test",
        approval_class=ApprovalClass.DESTRUCTIVE,
        chain=chain,
    )


class TestToUiChainShape:
    def test_returns_list_of_dicts(self) -> None:
        d = _make_decision(
            chain=[
                DecisionStep(
                    name="preflight",
                    action=DecisionAction.ALLOW,
                    note="tool=run_shell",
                    metadata={
                        "tool": "run_shell",
                        "tool_display": {"label": "PowerShell 命令"},
                    },
                ),
                DecisionStep(
                    name="classify",
                    action=DecisionAction.ALLOW,
                    note="approval_class=destructive",
                    metadata={
                        "approval_class": ApprovalClass.DESTRUCTIVE.value,
                        "source": DecisionSource.EXPLICIT_REGISTER_PARAM.value,
                    },
                ),
                DecisionStep(
                    name="confirmation_gate",
                    action=DecisionAction.CONFIRM,
                    note="destructive in strict",
                ),
            ]
        )
        ui_chain = d.to_ui_chain()
        assert isinstance(ui_chain, list)
        assert len(ui_chain) == 3
        for step in ui_chain:
            assert isinstance(step, dict)
            assert set(step.keys()) == {"name", "action", "note", "metadata", "display"}
            assert isinstance(step["metadata"], dict)
            assert set(step["display"].keys()) == {"label", "action", "note"}
            assert {"value", "label", "color"} <= set(step["display"]["action"].keys())

    def test_display_metadata_is_backend_owned(self) -> None:
        d = _make_decision(
            chain=[
                DecisionStep(
                    name="preflight",
                    action=DecisionAction.ALLOW,
                    note="tool=memory_delete_by_query",
                    metadata={
                        "tool": "memory_delete_by_query",
                        "tool_display": {
                            "label": "长期记忆删除",
                            "description": "删除匹配条件命中的长期记忆",
                        },
                    },
                ),
                DecisionStep(
                    name="classify",
                    action=DecisionAction.ALLOW,
                    note="class=destructive source=explicit_register_param",
                    metadata={
                        "approval_class": ApprovalClass.DESTRUCTIVE.value,
                        "source": DecisionSource.EXPLICIT_REGISTER_PARAM.value,
                    },
                ),
            ]
        )
        ui_chain = d.to_ui_chain()
        assert ui_chain[0]["display"]["label"] == "预检"
        assert ui_chain[0]["display"]["note"] == "工具：长期记忆删除"
        assert ui_chain[0]["display"]["action"]["label"] == "允许"
        assert ui_chain[1]["display"]["label"] == "分类"
        assert ui_chain[1]["display"]["note"] == "工具分类：破坏性操作；来源：显式注册参数"

    def test_preserves_step_order(self) -> None:
        d = _make_decision(
            chain=[
                DecisionStep(name="A", action=DecisionAction.ALLOW),
                DecisionStep(name="B", action=DecisionAction.ALLOW),
                DecisionStep(name="C", action=DecisionAction.CONFIRM),
            ]
        )
        ui_chain = d.to_ui_chain()
        assert [s["name"] for s in ui_chain] == ["A", "B", "C"]

    def test_empty_chain_returns_empty_list(self) -> None:
        d = _make_decision(chain=[])
        ui_chain = d.to_ui_chain()
        assert ui_chain == []
        # 重要：是 list 不是 None — 前端用 .length 判断渲染
        assert isinstance(ui_chain, list)

    def test_action_serialized_to_string_value(self) -> None:
        """DecisionAction StrEnum is still serialized as the raw audit value."""
        d = _make_decision(
            chain=[
                DecisionStep(name="x", action=DecisionAction.ALLOW),
                DecisionStep(name="y", action=DecisionAction.CONFIRM),
                DecisionStep(name="z", action=DecisionAction.DENY),
                DecisionStep(name="w", action=DecisionAction.DEFER),
            ]
        )
        actions = [s["action"] for s in d.to_ui_chain()]
        assert actions == ["allow", "confirm", "deny", "defer"]
        # 不能漏 enum.value，否则 JSON serialization 会带类型
        for a in actions:
            assert isinstance(a, str)

    def test_drops_duration_ms_field(self) -> None:
        """duration_ms is not part of the UI/audit wire payload."""
        d = _make_decision(
            chain=[DecisionStep(name="x", action=DecisionAction.ALLOW, note="n", duration_ms=42.5)]
        )
        step = d.to_ui_chain()[0]
        assert "duration_ms" not in step
        assert step["name"] == "x"
        assert step["action"] == "allow"
        assert step["note"] == "n"
        assert step["metadata"] == {}
        assert step["display"]["action"]["value"] == "allow"

    def test_empty_note_kept_as_empty_string(self) -> None:
        """note defaults to an empty string and display.note follows it."""
        d = _make_decision(chain=[DecisionStep(name="x", action=DecisionAction.ALLOW)])
        step = d.to_ui_chain()[0]
        assert step["note"] == ""
        assert step["display"]["note"] == ""


class TestSecurityConfirmDisplay:
    def test_confirmation_display_uses_tool_policy_metadata(self) -> None:
        display = security_confirm_display(
            source="risk_gate",
            tool_name="memory_delete_by_query",
            args={"query": "OPENAKITA_RISKGATE_689_REPRO_TEST"},
            reason="tool commit requires confirmed RiskGate tool authorization",
            risk_level="high",
            approval_class=ApprovalClass.DESTRUCTIVE.value,
            channel="desktop",
            policy_metadata={
                "tool_display": {
                    "label": "长期记忆删除",
                    "description": "删除匹配条件命中的长期记忆",
                }
            },
        )
        assert display["title"] == "RiskGate 安全确认"
        assert display["tool"]["label"] == "长期记忆删除"
        assert display["tool"]["description"] == "删除匹配条件命中的长期记忆"
        assert display["risk"]["label"] == "高风险"
        assert display["approval_class"]["label"] == "破坏性操作"
        assert display["arguments"]["format"] == "json"


class TestJsonSafety:
    """SSE 走 JSON 序列化 → to_ui_chain 输出必须可直接 json.dumps。"""

    def test_full_chain_json_dumpable(self) -> None:
        d = _make_decision(
            chain=[
                DecisionStep(name="preflight", action=DecisionAction.ALLOW, note="tool=write_file"),
                DecisionStep(
                    name="zones", action=DecisionAction.ALLOW, note="path inside workspace"
                ),
                DecisionStep(
                    name="confirmation_gate",
                    action=DecisionAction.CONFIRM,
                    note="destructive in default mode",
                ),
            ]
        )
        payload = {"decision_chain": d.to_ui_chain()}
        # 不要 default=repr 兜底 — 必须开箱即用
        encoded = json.dumps(payload)
        assert "preflight" in encoded
        assert "confirm" in encoded
        # 反序列化等价
        round_trip = json.loads(encoded)
        assert round_trip["decision_chain"] == d.to_ui_chain()

    def test_unicode_note_preserved(self) -> None:
        """note 经常含中文（"destructive in strict 模式"等）—— ensure_ascii
        默认 True 时也得 round-trip 一致。"""
        d = _make_decision(
            chain=[
                DecisionStep(
                    name="confirmation_gate",
                    action=DecisionAction.CONFIRM,
                    note="destructive 类工具在 strict 模式下需确认",
                ),
            ]
        )
        encoded = json.dumps({"chain": d.to_ui_chain()})
        decoded = json.loads(encoded)
        assert decoded["chain"][0]["note"] == "destructive 类工具在 strict 模式下需确认"


class TestPayloadIntegration:
    """Integration: 验证 security_confirm 统一构造点输出 backend display metadata。"""

    def test_yield_points_include_decision_chain(self) -> None:
        from pathlib import Path

        engine_src = Path("src/openakita/core/reasoning_engine.py").read_text(encoding="utf-8")
        channel_src = Path("src/openakita/core/security_confirm_channel.py").read_text(
            encoding="utf-8"
        )

        assert "security_confirm_display" not in engine_src
        assert "register_policy_confirm(" in engine_src
        assert "prepare_riskgate_tool_prompt(" in engine_src
        assert '"type": "security_confirm"' in channel_src
        assert '"decision_chain":' in channel_src
        assert '"display": security_confirm_display(' in channel_src

    def test_to_ui_chain_used_at_module_level(self) -> None:
        """grep guard: ``to_ui_chain`` 至少出现一次（防 git revert
        误删整个 helper 但留下 reasoning_engine 引用）。"""
        from pathlib import Path

        models_src = Path("src/openakita/core/policy_v2/models.py").read_text(encoding="utf-8")
        assert "def to_ui_chain" in models_src

    def test_frontend_renders_backend_display_metadata(self) -> None:
        """The modal renders the structured display contract produced by the backend."""
        from pathlib import Path

        modal_src = Path(
            "apps/setup-center/src/views/chat/components/SecurityConfirmModal.tsx"
        ).read_text(encoding="utf-8")
        assert "data.display.title" in modal_src
        assert "data.display.reason.text" in modal_src
        assert "data.display.risk.label" in modal_src
        assert "data.display.tool.label" in modal_src
        assert "data.display.arguments.text" in modal_src
        assert "step.display.label" in modal_src
        assert "step.display.action" in modal_src
