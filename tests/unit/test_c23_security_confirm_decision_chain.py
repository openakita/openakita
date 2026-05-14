"""C23 P2-2: ``security_confirm`` SSE 携带 ``decision_chain`` 字段。

Background
==========

Plan C9 要求 ``SecurityConfirmModal`` 渲染 ``decision_chain``——让用户
看到引擎逐步判定的链路（"step 1 preflight allow → step 2 classify
→ destructive → step 5 confirmation gate confirm"）而不只是一句
``reason``。但从 C9a 到 C20 一直没接通：``security_confirm`` SSE
payload 缺这个字段，前端 modal 也没渲染相关 UI。

C23 P2-2 在 ``PolicyDecisionV2`` 加 ``to_ui_chain()`` helper，把 chain
压缩为 ``[{name, action, note}, ...]`` 列表，注入到 reasoning_engine
的两个 yield 点（普通 confirm 路径 + dedup follower 路径以外的 leader
路径）。前端 ``SecurityConfirmModal`` 增加折叠"决策依据"区，缺失时
不渲染（向后兼容）。

本测试覆盖
==========

1. ``PolicyDecisionV2.to_ui_chain`` 输出形状
2. 空 chain 时返回空列表（不抛、不漏字段）
3. 序列化结果 JSON-safe（前端要走 SSE 序列化）
4. DecisionAction enum value 字符串化正确（前端 ACTION_LABELS 对齐）
"""

from __future__ import annotations

import json

from openakita.core.policy_v2.enums import ApprovalClass, DecisionAction
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
                DecisionStep(name="preflight", action=DecisionAction.ALLOW, note="tool=run_shell"),
                DecisionStep(name="classify", action=DecisionAction.ALLOW, note="approval_class=destructive"),
                DecisionStep(name="confirmation_gate", action=DecisionAction.CONFIRM, note="destructive in strict"),
            ]
        )
        ui_chain = d.to_ui_chain()
        assert isinstance(ui_chain, list)
        assert len(ui_chain) == 3
        for step in ui_chain:
            assert isinstance(step, dict)
            assert set(step.keys()) == {"name", "action", "note"}

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
        """DecisionAction StrEnum → 字符串值。前端 ACTION_LABELS map 用的
        是 'allow'/'confirm'/'deny'/'defer' 这些字符串，不是 enum 实例。"""
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
        """duration_ms 在 SSE 不需要——大多为 0，且用户无 actionable
        信息。前端 ACTION_LABELS 也没渲染它。"""
        d = _make_decision(
            chain=[DecisionStep(name="x", action=DecisionAction.ALLOW, note="n", duration_ms=42.5)]
        )
        step = d.to_ui_chain()[0]
        assert "duration_ms" not in step
        assert step == {"name": "x", "action": "allow", "note": "n"}

    def test_empty_note_kept_as_empty_string(self) -> None:
        """note 缺省是 ""——前端 ``if (step.note)`` 判断渲染。保持 str
        类型让 schema 一致，不要回退到 None。"""
        d = _make_decision(
            chain=[DecisionStep(name="x", action=DecisionAction.ALLOW)]
        )
        step = d.to_ui_chain()[0]
        assert step["note"] == ""


class TestJsonSafety:
    """SSE 走 JSON 序列化 → to_ui_chain 输出必须可直接 json.dumps。"""

    def test_full_chain_json_dumpable(self) -> None:
        d = _make_decision(
            chain=[
                DecisionStep(name="preflight", action=DecisionAction.ALLOW, note="tool=write_file"),
                DecisionStep(name="zones", action=DecisionAction.ALLOW, note="path inside workspace"),
                DecisionStep(name="confirmation_gate", action=DecisionAction.CONFIRM, note="destructive in default mode"),
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
    """Integration: 验证 reasoning_engine 的 security_confirm yield 点已
    经把 decision_chain 字段塞进 payload。这是 grep-style 静态守卫，让
    未来若有人误删 ``"decision_chain": _pr.to_ui_chain()`` 被立即抓住。"""

    def test_yield_points_include_decision_chain(self) -> None:
        from pathlib import Path

        src = Path("src/openakita/core/reasoning_engine.py").read_text(encoding="utf-8")
        # 应该在两处 security_confirm yield 都有 decision_chain
        confirm_yields = src.count('"type": "security_confirm"')
        chain_emits = src.count('"decision_chain": _pr.to_ui_chain()')
        assert confirm_yields >= 2, (
            f"Expected ≥2 security_confirm yield points, got {confirm_yields}. "
            "If you split / refactored the yield sites, update this guard."
        )
        assert chain_emits == confirm_yields, (
            f"Found {confirm_yields} security_confirm yields but only "
            f"{chain_emits} include decision_chain. Every security_confirm "
            "yield must carry decision_chain (C23 P2-2)."
        )

    def test_to_ui_chain_used_at_module_level(self) -> None:
        """grep guard: ``to_ui_chain`` 至少出现一次（防 git revert
        误删整个 helper 但留下 reasoning_engine 引用）。"""
        from pathlib import Path

        models_src = Path("src/openakita/core/policy_v2/models.py").read_text(encoding="utf-8")
        assert "def to_ui_chain" in models_src
