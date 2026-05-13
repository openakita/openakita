"""C1 smoke test: ensure policy_v2 skeleton imports cleanly and matrix invariants hold.

Acceptance criteria for C1:
- All public API imports work
- Enum cardinality matches design
- PolicyResult is alias to PolicyDecisionV2 (v1 compat)
- Matrix safety-by-default invariants hold
- PolicyContext.from_session works on legacy v1 session (no session_role/confirmation_mode)
- Legacy mode aliases (yolo→trust, smart→default, cautious→strict) work
- derive_child appends delegate_chain and preserves root_user_id
- ContextVar set/get/reset roundtrip
"""

from __future__ import annotations

from pathlib import Path

from openakita.core.policy_v2 import (
    ApprovalClass,
    ConfirmationMode,
    ConfirmationRequired,
    DecisionAction,
    DecisionSource,
    DeferredApprovalRequired,
    DeniedByPolicy,
    PolicyContext,
    PolicyDecisionV2,
    PolicyError,
    PolicyResult,
    SessionRole,
    ToolCallEvent,
    get_current_context,
    lookup_matrix,
    reset_current_context,
    set_current_context,
)


def test_enum_completeness() -> None:
    assert len(ApprovalClass) == 12, "11 业务维 + UNKNOWN 兜底"
    assert len(SessionRole) == 4
    assert len(ConfirmationMode) == 5
    assert len(DecisionAction) == 4
    assert len(DecisionSource) == 7


def test_decision_source_explicit_helper() -> None:
    assert DecisionSource.is_explicit(DecisionSource.EXPLICIT_REGISTER_PARAM)
    assert DecisionSource.is_explicit(DecisionSource.EXPLICIT_HANDLER_ATTR)
    assert DecisionSource.is_explicit(DecisionSource.SKILL_METADATA)
    assert not DecisionSource.is_explicit(DecisionSource.HEURISTIC_PREFIX)
    assert not DecisionSource.is_explicit(DecisionSource.FALLBACK_UNKNOWN)


def test_policy_result_alias() -> None:
    """PolicyResult 必须是 PolicyDecisionV2 别名（v1 orgs/runtime.py 兼容）。"""
    assert PolicyResult is PolicyDecisionV2


def test_exception_hierarchy() -> None:
    for exc_cls in (DeniedByPolicy, ConfirmationRequired, DeferredApprovalRequired):
        assert issubclass(exc_cls, PolicyError)
    err = DeniedByPolicy("msg", tool="write_file", reason="safety_immune")
    assert err.tool == "write_file"
    assert err.reason == "safety_immune"


# ---- matrix safety invariants ----


def test_unknown_never_silently_allows() -> None:
    """UNKNOWN 在任何 mode 下都不应静默 ALLOW（DONT_ASK 也仅 CONFIRM）。"""
    for mode in ConfirmationMode:
        for role in (SessionRole.AGENT, SessionRole.COORDINATOR):
            decision = lookup_matrix(role, mode, ApprovalClass.UNKNOWN)
            assert decision != DecisionAction.ALLOW, (
                f"UNKNOWN must not silently ALLOW under role={role.value} "
                f"mode={mode.value}, got {decision.value}"
            )


def test_destructive_in_trust_still_confirms() -> None:
    """DESTRUCTIVE 在 trust 模式仍需 CONFIRM（safety-by-default）。"""
    decision = lookup_matrix(
        SessionRole.AGENT, ConfirmationMode.TRUST, ApprovalClass.DESTRUCTIVE
    )
    assert decision == DecisionAction.CONFIRM


def test_destructive_in_strict_denies() -> None:
    decision = lookup_matrix(
        SessionRole.AGENT, ConfirmationMode.STRICT, ApprovalClass.DESTRUCTIVE
    )
    assert decision == DecisionAction.DENY


def test_plan_mode_blocks_all_mutations_and_exec() -> None:
    """plan 模式下 mutation/exec/destructive 一律 DENY，不论 confirmation_mode。"""
    blocked_classes = (
        ApprovalClass.MUTATING_SCOPED,
        ApprovalClass.MUTATING_GLOBAL,
        ApprovalClass.DESTRUCTIVE,
        ApprovalClass.EXEC_LOW_RISK,
        ApprovalClass.EXEC_CAPABLE,
    )
    for klass in blocked_classes:
        for mode in ConfirmationMode:
            assert (
                lookup_matrix(SessionRole.PLAN, mode, klass) == DecisionAction.DENY
            ), f"plan mode must DENY {klass.value} regardless of mode={mode.value}"


def test_plan_mode_allows_readonly_and_interactive() -> None:
    for klass in (
        ApprovalClass.READONLY_SCOPED,
        ApprovalClass.READONLY_GLOBAL,
        ApprovalClass.READONLY_SEARCH,
        ApprovalClass.INTERACTIVE,
    ):
        assert (
            lookup_matrix(SessionRole.PLAN, ConfirmationMode.DEFAULT, klass)
            == DecisionAction.ALLOW
        )


def test_ask_mode_blocks_control_plane() -> None:
    """ask 模式禁 CONTROL_PLANE（不能 switch_mode 等）。"""
    assert (
        lookup_matrix(
            SessionRole.ASK, ConfirmationMode.TRUST, ApprovalClass.CONTROL_PLANE
        )
        == DecisionAction.DENY
    )


def test_coordinator_stricter_than_agent_in_trust() -> None:
    """coordinator 模式应比 agent 更严：trust 下 CONTROL_PLANE/MUTATING_GLOBAL/EXEC_CAPABLE 仍 CONFIRM。"""
    for klass in (
        ApprovalClass.MUTATING_GLOBAL,
        ApprovalClass.EXEC_CAPABLE,
        ApprovalClass.CONTROL_PLANE,
    ):
        agent_dec = lookup_matrix(SessionRole.AGENT, ConfirmationMode.TRUST, klass)
        coord_dec = lookup_matrix(
            SessionRole.COORDINATOR, ConfirmationMode.TRUST, klass
        )
        assert agent_dec == DecisionAction.ALLOW
        assert coord_dec == DecisionAction.CONFIRM, (
            f"coordinator should CONFIRM {klass.value} in trust, got {coord_dec.value}"
        )


def test_interactive_always_allow() -> None:
    """INTERACTIVE 在所有 role × mode 一律 ALLOW（IM 渠道屏蔽是 engine 层职责）。"""
    for role in SessionRole:
        for mode in ConfirmationMode:
            assert (
                lookup_matrix(role, mode, ApprovalClass.INTERACTIVE)
                == DecisionAction.ALLOW
            ), f"INTERACTIVE must ALLOW under role={role.value} mode={mode.value}"


# ---- PolicyContext ----


class _FakeLegacySession:
    """模拟 v1 Session（无 session_role / confirmation_mode 字段）。"""

    id = "legacy-session-1"
    workspace = "/tmp/work"
    metadata: dict = {}


def test_context_from_legacy_session_uses_defaults() -> None:
    ctx = PolicyContext.from_session(_FakeLegacySession())
    assert ctx.session_id == "legacy-session-1"
    assert ctx.workspace == Path("/tmp/work")
    assert ctx.session_role == SessionRole.AGENT
    assert ctx.confirmation_mode == ConfirmationMode.DEFAULT
    assert ctx.is_owner is True
    assert ctx.is_unattended is False
    assert ctx.channel == "desktop"


def test_context_legacy_yolo_mode_alias() -> None:
    """v1 'yolo' / 'smart' / 'cautious' 应映射到 v2 'trust' / 'default' / 'strict'。"""

    class FakeSession:
        id = "s2"
        workspace = "/tmp/work"
        confirmation_mode = "yolo"
        metadata: dict = {}

    ctx = PolicyContext.from_session(FakeSession())
    assert ctx.confirmation_mode == ConfirmationMode.TRUST

    FakeSession.confirmation_mode = "smart"
    assert (
        PolicyContext.from_session(FakeSession()).confirmation_mode
        == ConfirmationMode.DEFAULT
    )

    FakeSession.confirmation_mode = "cautious"
    assert (
        PolicyContext.from_session(FakeSession()).confirmation_mode
        == ConfirmationMode.STRICT
    )


def test_context_im_channel_metadata_propagates() -> None:
    class FakeIMSession:
        id = "telegram:12345"
        workspace = "/tmp/work"
        metadata = {
            "channel": "im:telegram",
            "is_owner": False,
            "is_unattended": False,
            "delegate_chain": ["root"],
        }

    ctx = PolicyContext.from_session(FakeIMSession())
    assert ctx.channel == "im:telegram"
    assert ctx.is_owner is False
    assert ctx.delegate_chain == ["root"]


def test_context_overrides_param() -> None:
    """from_session(**overrides) 允许测试代码强制特定字段。"""
    ctx = PolicyContext.from_session(
        _FakeLegacySession(), is_unattended=True, unattended_strategy="defer_to_owner"
    )
    assert ctx.is_unattended is True
    assert ctx.unattended_strategy == "defer_to_owner"


def test_context_derive_child_appends_chain_and_promotes_root() -> None:
    parent = PolicyContext(
        session_id="root-session",
        workspace=Path("."),
        delegate_chain=["root"],
    )
    child = parent.derive_child("child-session-1", "specialist_a")
    assert child.delegate_chain == ["root", "specialist_a"]
    assert child.root_user_id == "root-session"
    assert child.session_id == "child-session-1"


def test_context_derive_child_preserves_existing_root() -> None:
    grandparent_root = PolicyContext(
        session_id="parent-session",
        workspace=Path("."),
        root_user_id="actual-root-uid",
        delegate_chain=["root", "parent"],
    )
    child = grandparent_root.derive_child("child-session-2", "specialist_b")
    assert child.root_user_id == "actual-root-uid"
    assert child.delegate_chain == ["root", "parent", "specialist_b"]


def test_context_derive_child_does_not_share_mutable_state() -> None:
    """子上下文不应与父共享 list 引用（避免一方 append 污染另一方）。"""
    parent = PolicyContext(
        session_id="p",
        workspace=Path("."),
        replay_authorizations=[{"a": 1}],
        trusted_path_overrides=[{"path": "/x"}],
    )
    child = parent.derive_child("c", "spec")
    child.replay_authorizations.append({"a": 2})
    child.trusted_path_overrides.append({"path": "/y"})
    assert len(parent.replay_authorizations) == 1
    assert len(parent.trusted_path_overrides) == 1


def test_context_var_roundtrip() -> None:
    assert get_current_context() is None
    ctx = PolicyContext(session_id="ctx-test", workspace=Path("."))
    token = set_current_context(ctx)
    try:
        assert get_current_context() is ctx
    finally:
        reset_current_context(token)
    assert get_current_context() is None


# ---- models ----


def test_decision_to_audit_dict_omits_chain() -> None:
    """to_audit_dict 默认不带 chain，仅返回 step_count（控制 SSE/审计数据量，详见 docs §13.5）。"""
    decision = PolicyDecisionV2(
        action=DecisionAction.ALLOW,
        reason="ok",
        approval_class=ApprovalClass.READONLY_GLOBAL,
    )
    audit = decision.to_audit_dict()
    assert "chain" not in audit
    assert audit["step_count"] == 0
    assert audit["action"] == "allow"
    assert audit["approval_class"] == "readonly_global"


def test_tool_call_event_defaults() -> None:
    event = ToolCallEvent(tool="write_file")
    assert event.params == {}
    assert event.classifier_source is None
    assert event.handler_name is None
