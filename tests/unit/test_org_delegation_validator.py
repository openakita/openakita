"""Tests for OrgDelegationValidator (B3) and verify_incomplete_with_children
diagnosis card downgrade (B5).
"""

from openakita.core.validators import (
    OrgDelegationValidator,
    ValidationContext,
    ValidationResult,
    create_default_registry,
)
from openakita.orgs.failure_diagnoser import summarize


class TestOrgDelegationValidator:
    def test_skip_when_no_signal(self):
        v = OrgDelegationValidator()
        ctx = ValidationContext(user_request="any", assistant_response="ok")
        out = v.validate(ctx)
        assert out.result == ValidationResult.SKIP

    def test_pass_with_accepted_count(self):
        v = OrgDelegationValidator()
        ctx = ValidationContext(
            user_request="any",
            assistant_response="ok",
            accepted_child_count=2,
        )
        out = v.validate(ctx)
        assert out.result == ValidationResult.PASS
        assert "2" in out.reason

    def test_pass_with_recent_signal(self):
        v = OrgDelegationValidator()
        ctx = ValidationContext(
            user_request="any",
            assistant_response="ok",
            has_recent_accepted_signal=True,
        )
        out = v.validate(ctx)
        assert out.result == ValidationResult.PASS
        assert "weak signal" in out.reason or "deliverable_accepted" in out.reason

    def test_default_registry_includes_validator(self):
        # Must be wired into the default registry so verify_task_completion
        # can use its PASS verdict.
        registry = create_default_registry()
        names = [v.name for v in registry._validators]  # type: ignore[attr-defined]
        assert "OrgDelegationValidator" in names

    def test_backward_compat_existing_context(self):
        # ValidationContext built from older code paths (no new fields) still works.
        v = OrgDelegationValidator()
        ctx = ValidationContext(
            user_request="x",
            assistant_response="y",
            executed_tools=["read_file"],
        )
        out = v.validate(ctx)
        assert out.result == ValidationResult.SKIP


class TestDiagnosisCardDowngrade:
    def _make_trace_with_accept(self) -> list[dict]:
        return [
            {
                "iteration": 1,
                "tool_calls": [
                    {
                        "id": "tc1",
                        "name": "org_accept_deliverable",
                        "input": {"from_node": "writer_a"},
                    }
                ],
                "tool_results": [
                    {
                        "tool_use_id": "tc1",
                        "is_error": False,
                        "result_content": "验收通过",
                    }
                ],
            }
        ]

    def _make_trace_without_accept(self) -> list[dict]:
        return [
            {
                "iteration": 1,
                "tool_calls": [
                    {
                        "id": "tc1",
                        "name": "write_file",
                        "input": {"path": "/tmp/x.md"},
                    }
                ],
                "tool_results": [
                    {
                        "tool_use_id": "tc1",
                        "is_error": True,
                        "result_content": "❌ permission denied",
                    }
                ],
            }
        ]

    def test_verify_incomplete_with_accept_signal_downgrades(self):
        diag = summarize(self._make_trace_with_accept(), exit_reason="verify_incomplete")
        assert diag["root_cause"] == "verify_incomplete_with_children"
        assert "已通过下属交付" in diag["headline"]

    def test_verify_incomplete_without_accept_signal_keeps_strict(self):
        diag = summarize(self._make_trace_without_accept(), exit_reason="verify_incomplete")
        assert diag["root_cause"] == "verify_incomplete"
        assert "未完成" in diag["headline"]

    def test_unrelated_exit_reason_unchanged(self):
        # max_iterations / loop_terminated paths must be unaffected by the new branch.
        diag = summarize(self._make_trace_with_accept(), exit_reason="max_iterations")
        assert diag["root_cause"] == "max_iterations"
