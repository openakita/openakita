"""C6 — policy_v2.adapter 翻译 / fail-closed / metadata 冗余写测试。"""
from __future__ import annotations

from typing import Any

import pytest

from openakita.core.policy import PolicyDecision, PolicyResult
from openakita.core.policy_v2 import (
    ApprovalClass,
    ConfirmationMode,
    DecisionAction,
    DecisionStep,
    PolicyContext,
    PolicyDecisionV2,
    SessionRole,
    decision_to_v1_result,
    evaluate_via_v2,
    evaluate_via_v2_to_v1_result,
    reset_current_context,
    reset_engine_v2,
    set_current_context,
    set_engine_v2,
)
from openakita.core.policy_v2.adapter import (
    _build_fallback_context,
    _build_metadata,
    _build_policy_name,
    _resolve_context,
    _shell_risk_to_v1_risk_level,
    _v2_action_to_v1_decision,
)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    reset_engine_v2()


class _StubEngine:
    """duck-type PolicyEngineV2 for adapter tests."""

    def __init__(self, decision: PolicyDecisionV2):
        self.decision = decision
        self.events: list[Any] = []
        self.contexts: list[Any] = []

    def evaluate_tool_call(self, event, ctx):
        self.events.append(event)
        self.contexts.append(ctx)
        return self.decision


# ---------------------------------------------------------------------------
# v2 → v1 decision mapping
# ---------------------------------------------------------------------------


class TestV2ToV1DecisionMapping:
    def test_allow(self):
        assert _v2_action_to_v1_decision(DecisionAction.ALLOW) == PolicyDecision.ALLOW

    def test_confirm(self):
        assert _v2_action_to_v1_decision(DecisionAction.CONFIRM) == PolicyDecision.CONFIRM

    def test_deny(self):
        assert _v2_action_to_v1_decision(DecisionAction.DENY) == PolicyDecision.DENY

    def test_defer_downgrades_to_confirm(self):
        # v1 不识别 DEFER；adapter 保守降为 CONFIRM
        assert _v2_action_to_v1_decision(DecisionAction.DEFER) == PolicyDecision.CONFIRM


# ---------------------------------------------------------------------------
# Metadata 冗余写
# ---------------------------------------------------------------------------


class TestMetadataFlatten:
    def test_canonical_fields_present(self):
        d = PolicyDecisionV2(
            action=DecisionAction.ALLOW,
            approval_class=ApprovalClass.MUTATING_SCOPED,
            needs_sandbox=True,
            needs_checkpoint=True,
            shell_risk_level="HIGH",
            safety_immune_match=None,
            is_owner_required=False,
            is_unattended_path=False,
            ttl_seconds=300.0,
        )
        meta = _build_metadata(d)
        assert meta["needs_sandbox"] is True
        assert meta["needs_checkpoint"] is True
        assert meta["shell_risk_level"] == "HIGH"
        assert meta["approval_class"] == "mutating_scoped"
        assert meta["risk_level"] == "high"
        assert meta["v2_origin"] is True

    def test_extras_dont_overwrite_canonical(self):
        # 上游写 metadata['needs_sandbox']=False；canonical 字段是 True；保留 canonical
        d = PolicyDecisionV2(
            action=DecisionAction.ALLOW,
            needs_sandbox=True,
            metadata={"needs_sandbox": False, "custom_field": "x"},
        )
        meta = _build_metadata(d)
        assert meta["needs_sandbox"] is True  # canonical 优先
        assert meta["custom_field"] == "x"

    def test_risk_level_from_shell_risk(self):
        for shell, expected in [
            ("BLOCKED", "critical"),
            ("CRITICAL", "critical"),
            ("HIGH", "high"),
            ("MEDIUM", "medium"),
            ("LOW", "low"),
        ]:
            assert _shell_risk_to_v1_risk_level(shell, "executable") == expected

    def test_risk_level_from_approval_class_when_no_shell(self):
        assert _shell_risk_to_v1_risk_level(None, "destructive") == "critical"
        assert _shell_risk_to_v1_risk_level(None, "control_plane") == "critical"
        assert _shell_risk_to_v1_risk_level(None, "mutating_global") == "high"
        assert _shell_risk_to_v1_risk_level(None, "interactive") == "medium"
        assert _shell_risk_to_v1_risk_level(None, "readonly_scoped") == "low"


# ---------------------------------------------------------------------------
# decision_to_v1_result
# ---------------------------------------------------------------------------


class TestDecisionToV1Result:
    def test_basic_allow(self):
        d = PolicyDecisionV2(
            action=DecisionAction.ALLOW,
            reason="ok",
            approval_class=ApprovalClass.READONLY_SCOPED,
        )
        r = decision_to_v1_result(d)
        assert isinstance(r, PolicyResult)
        assert r.decision == PolicyDecision.ALLOW
        assert r.reason == "ok"
        assert r.policy_name.startswith("policy_v2")

    def test_policy_name_uses_last_chain_step(self):
        d = PolicyDecisionV2(
            action=DecisionAction.DENY,
            chain=[
                DecisionStep(name="classifier", action=DecisionAction.ALLOW),
                DecisionStep(name="safety_immune", action=DecisionAction.DENY),
            ],
        )
        assert _build_policy_name(d) == "policy_v2:safety_immune"

    def test_policy_name_empty_chain(self):
        d = PolicyDecisionV2(action=DecisionAction.ALLOW)
        assert _build_policy_name(d) == "policy_v2"


# ---------------------------------------------------------------------------
# evaluate_via_v2 + ContextVar
# ---------------------------------------------------------------------------


class TestEvaluateViaV2:
    def test_uses_context_var_when_set(self):
        decision = PolicyDecisionV2(action=DecisionAction.ALLOW)
        engine = _StubEngine(decision)
        set_engine_v2(engine)  # type: ignore[arg-type]

        ctx = PolicyContext(
            session_id="test-session",
            workspace=__import__("pathlib").Path("/tmp"),
            confirmation_mode=ConfirmationMode.TRUST,
        )
        token = set_current_context(ctx)
        try:
            result = evaluate_via_v2("read_file", {"path": "x"})
            assert result is decision
            assert engine.contexts[0] is ctx
        finally:
            reset_current_context(token)

    def test_falls_back_when_no_context_var(self):
        decision = PolicyDecisionV2(action=DecisionAction.ALLOW)
        engine = _StubEngine(decision)
        set_engine_v2(engine)  # type: ignore[arg-type]

        result = evaluate_via_v2("read_file", {"path": "x"})
        assert result is decision
        # fallback ctx 至少有 workspace + session_role
        used_ctx = engine.contexts[0]
        assert used_ctx.session_id == "policy_v2_adapter_fallback"
        assert used_ctx.session_role == SessionRole.AGENT

    def test_extra_ctx_overrides_context_var(self):
        decision = PolicyDecisionV2(action=DecisionAction.ALLOW)
        engine = _StubEngine(decision)
        set_engine_v2(engine)  # type: ignore[arg-type]

        ctx_a = PolicyContext(
            session_id="ctx_a", workspace=__import__("pathlib").Path("/tmp")
        )
        ctx_b = PolicyContext(
            session_id="ctx_b", workspace=__import__("pathlib").Path("/tmp")
        )
        token = set_current_context(ctx_a)
        try:
            evaluate_via_v2("read_file", {}, extra_ctx=ctx_b)
            assert engine.contexts[0] is ctx_b
        finally:
            reset_current_context(token)

    def test_user_message_filled_when_ctx_lacks(self):
        decision = PolicyDecisionV2(action=DecisionAction.ALLOW)
        engine = _StubEngine(decision)
        set_engine_v2(engine)  # type: ignore[arg-type]

        ctx = PolicyContext(
            session_id="s", workspace=__import__("pathlib").Path("/tmp"), user_message=""
        )
        token = set_current_context(ctx)
        try:
            evaluate_via_v2("write_file", {"path": "/tmp/x"}, user_message="hello")
            used = engine.contexts[0]
            assert used.user_message == "hello"
            # 不修改原 ctx（防止跨调用污染）
            assert ctx.user_message == ""
        finally:
            reset_current_context(token)

    def test_user_message_not_overwritten_when_ctx_already_has_one(self):
        decision = PolicyDecisionV2(action=DecisionAction.ALLOW)
        engine = _StubEngine(decision)
        set_engine_v2(engine)  # type: ignore[arg-type]

        ctx = PolicyContext(
            session_id="s",
            workspace=__import__("pathlib").Path("/tmp"),
            user_message="original",
        )
        token = set_current_context(ctx)
        try:
            evaluate_via_v2("write_file", {}, user_message="new")
            assert engine.contexts[0].user_message == "original"
        finally:
            reset_current_context(token)


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_risky_tool_engine_crash_returns_deny(self, monkeypatch):
        def _boom():
            raise RuntimeError("singleton dead")

        monkeypatch.setattr("openakita.core.policy_v2.adapter._get_engine", _boom)

        decision = evaluate_via_v2("write_file", {"path": "x"})
        assert decision.action == DecisionAction.DENY
        assert "adapter_fail_closed" in decision.chain[0].name

    def test_safe_tool_engine_crash_returns_allow(self, monkeypatch):
        def _boom():
            raise RuntimeError("singleton dead")

        monkeypatch.setattr("openakita.core.policy_v2.adapter._get_engine", _boom)

        decision = evaluate_via_v2("read_file", {"path": "x"})
        assert decision.action == DecisionAction.ALLOW
        assert "adapter_fail_open_safe" in decision.chain[0].name

    def test_run_shell_fail_closed(self, monkeypatch):
        monkeypatch.setattr(
            "openakita.core.policy_v2.adapter._get_engine",
            lambda: (_ for _ in ()).throw(RuntimeError("x")),
        )
        decision = evaluate_via_v2("run_shell", {"command": "echo hi"})
        assert decision.action == DecisionAction.DENY


# ---------------------------------------------------------------------------
# evaluate_via_v2_to_v1_result（一步式）
# ---------------------------------------------------------------------------


class TestEvaluateViaV2ToV1Result:
    def test_returns_v1_shaped_result(self):
        engine = _StubEngine(
            PolicyDecisionV2(
                action=DecisionAction.ALLOW,
                reason="all good",
                needs_sandbox=True,
                approval_class=ApprovalClass.MUTATING_SCOPED,
            )
        )
        set_engine_v2(engine)  # type: ignore[arg-type]

        r = evaluate_via_v2_to_v1_result("write_file", {"path": "/tmp/x"})
        assert isinstance(r, PolicyResult)
        assert r.decision == PolicyDecision.ALLOW
        assert r.reason == "all good"
        # downstream execute_tool_with_policy uses metadata.needs_sandbox
        assert r.metadata["needs_sandbox"] is True
        assert r.metadata["v2_origin"] is True


# ---------------------------------------------------------------------------
# fallback context construction
# ---------------------------------------------------------------------------


class TestFallbackContext:
    def test_default_role_and_mode(self):
        ctx = _build_fallback_context()
        assert ctx.session_role == SessionRole.AGENT
        # mode 取自 config（默认 DEFAULT）；若 config 不可用 fallback DEFAULT
        assert isinstance(ctx.confirmation_mode, ConfirmationMode)

    def test_user_message_propagated(self):
        ctx = _build_fallback_context(user_message="please write file")
        assert ctx.user_message == "please write file"

    def test_resolve_context_priority_extra_ctx(self):
        a = PolicyContext(session_id="a", workspace=__import__("pathlib").Path("/tmp"))
        b = PolicyContext(session_id="b", workspace=__import__("pathlib").Path("/tmp"))
        token = set_current_context(a)
        try:
            picked = _resolve_context(extra_ctx=b, user_message="")
            assert picked is b
        finally:
            reset_current_context(token)

    def test_resolve_context_user_message_copy_preserves_all_fields(self):
        """C6 二轮 audit 加固：_resolve_context 要补 user_message 时复制 ctx，
        必须保留所有字段；否则未来给 PolicyContext 加新字段会静默丢失。"""
        from openakita.core.policy_v2 import (
            ReplayAuthorization,
            TrustedPathOverride,
        )

        original = PolicyContext(
            session_id="orig",
            workspace=__import__("pathlib").Path("/ws"),
            channel="telegram",
            is_owner=True,
            root_user_id="user-42",
            session_role=SessionRole.AGENT,
            confirmation_mode=ConfirmationMode.TRUST,
            is_unattended=True,
            unattended_strategy="safe_only",
            delegate_chain=["root", "child"],
            replay_authorizations=[
                ReplayAuthorization(
                    expires_at=9999999999.0,
                    original_message="please write file",
                    confirmation_id="conf-1",
                    operation="write",
                )
            ],
            trusted_path_overrides=[
                TrustedPathOverride(
                    operation="write",
                    path_pattern="/tmp/trusted/*",
                )
            ],
            safety_immune_paths=("/etc",),
            metadata={"custom_key": "custom_val"},
            user_message="",
        )
        token = set_current_context(original)
        try:
            copied = _resolve_context(extra_ctx=None, user_message="new_msg")

            assert copied is not original  # 真的复制了
            assert copied.user_message == "new_msg"
            assert original.user_message == ""  # 不污染原 ctx

            for field in (
                "session_id",
                "workspace",
                "channel",
                "is_owner",
                "root_user_id",
                "session_role",
                "confirmation_mode",
                "is_unattended",
                "unattended_strategy",
                "safety_immune_paths",
            ):
                assert getattr(copied, field) == getattr(original, field), (
                    f"_resolve_context lost field {field!r} when copying ctx"
                )

            assert copied.delegate_chain == original.delegate_chain
            assert copied.replay_authorizations == original.replay_authorizations
            assert copied.trusted_path_overrides == original.trusted_path_overrides
            assert copied.metadata == original.metadata
        finally:
            reset_current_context(token)
