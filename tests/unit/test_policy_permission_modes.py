from openakita.core.policy_v2 import (
    ApprovalClass,
    ConfirmationMode,
    DecisionAction,
    SessionRole,
    lookup_matrix,
)


def test_plan_permission_mode_denies_write():
    assert (
        lookup_matrix(
            SessionRole.PLAN,
            ConfirmationMode.DEFAULT,
            ApprovalClass.MUTATING_SCOPED,
        )
        == DecisionAction.DENY
    )


def test_accept_edits_still_requires_confirmation_for_capable_shell():
    assert (
        lookup_matrix(
            SessionRole.AGENT,
            ConfirmationMode.ACCEPT_EDITS,
            ApprovalClass.EXEC_CAPABLE,
        )
        == DecisionAction.CONFIRM
    )


def test_accept_edits_allows_scoped_edits_but_not_global_writes():
    assert (
        lookup_matrix(
            SessionRole.AGENT,
            ConfirmationMode.ACCEPT_EDITS,
            ApprovalClass.MUTATING_SCOPED,
        )
        == DecisionAction.ALLOW
    )
    assert (
        lookup_matrix(
            SessionRole.AGENT,
            ConfirmationMode.ACCEPT_EDITS,
            ApprovalClass.MUTATING_GLOBAL,
        )
        == DecisionAction.CONFIRM
    )


def test_trust_mode_still_requires_confirmation_for_destructive_actions():
    assert (
        lookup_matrix(
            SessionRole.AGENT,
            ConfirmationMode.TRUST,
            ApprovalClass.DESTRUCTIVE,
        )
        == DecisionAction.CONFIRM
    )
