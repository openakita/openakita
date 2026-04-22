"""Tests for supervisor known-error early termination (A4).

Validates that ``_check_tool_thrashing`` upgrades to STRATEGY_SWITCH (>=2)
and TERMINATE (>=3) when failures match KNOWN_ERROR_PATTERNS, while keeping
the original 8-window/3-threshold STRATEGY_SWITCH path for normal failures.
"""

from openakita.core.supervisor import (
    InterventionLevel,
    PatternType,
    RuntimeSupervisor,
    KNOWN_ERROR_STRATEGY_SWITCH_THRESHOLD,
    KNOWN_ERROR_TERMINATE_THRESHOLD,
)


def _record_known_error(sup: RuntimeSupervisor, tool: str, n: int) -> None:
    for i in range(n):
        sup.record_tool_call(
            tool_name=tool,
            params={"x": i},
            success=False,
            iteration=i,
            result_text="❌ Tool not found: write_file",
        )


def _record_normal_failure(sup: RuntimeSupervisor, tool: str, n: int) -> None:
    for i in range(n):
        sup.record_tool_call(
            tool_name=tool,
            params={"x": i},
            success=False,
            iteration=i,
            result_text="HTTP 500 timeout",
        )


class TestKnownErrorEarlyTerminate:
    def test_one_known_error_no_intervention(self):
        sup = RuntimeSupervisor()
        _record_known_error(sup, "get_tool_info", 1)
        assert sup._check_tool_thrashing(0) is None

    def test_two_known_errors_triggers_strategy_switch(self):
        sup = RuntimeSupervisor()
        _record_known_error(sup, "get_tool_info", KNOWN_ERROR_STRATEGY_SWITCH_THRESHOLD)
        intervention = sup._check_tool_thrashing(0)
        assert intervention is not None
        assert intervention.level == InterventionLevel.STRATEGY_SWITCH
        assert intervention.pattern == PatternType.TOOL_THRASHING
        assert "get_tool_info" in intervention.message

    def test_three_known_errors_triggers_terminate(self):
        sup = RuntimeSupervisor()
        _record_known_error(sup, "get_tool_info", KNOWN_ERROR_TERMINATE_THRESHOLD)
        intervention = sup._check_tool_thrashing(0)
        assert intervention is not None
        assert intervention.level == InterventionLevel.TERMINATE
        assert intervention.should_inject_prompt is True

    def test_run_shell_missing_command_pattern_recognized(self):
        sup = RuntimeSupervisor()
        for i in range(3):
            sup.record_tool_call(
                tool_name="run_shell",
                params={},
                success=False,
                iteration=i,
                result_text="❌ run_shell 缺少必要参数 'command'。",
            )
        intervention = sup._check_tool_thrashing(0)
        assert intervention is not None
        assert intervention.level == InterventionLevel.TERMINATE


class TestNormalFailureUnchanged:
    """Non-KNOWN_ERROR failures must keep the original 8-window/3-threshold path."""

    def test_three_normal_failures_strategy_switch_only(self):
        sup = RuntimeSupervisor()
        _record_normal_failure(sup, "web_search", 3)
        intervention = sup._check_tool_thrashing(0)
        assert intervention is not None
        assert intervention.level == InterventionLevel.STRATEGY_SWITCH

    def test_two_normal_failures_no_intervention(self):
        """Original threshold is 3; 2 normal failures should NOT trigger."""
        sup = RuntimeSupervisor()
        _record_normal_failure(sup, "web_search", 2)
        assert sup._check_tool_thrashing(0) is None

    def test_known_error_takes_precedence_over_normal(self):
        """When both kinds of failures coexist, known-error path wins."""
        sup = RuntimeSupervisor()
        _record_known_error(sup, "get_tool_info", 2)
        _record_normal_failure(sup, "web_search", 1)
        intervention = sup._check_tool_thrashing(0)
        assert intervention is not None
        assert "get_tool_info" in intervention.message


class TestRecordToolCallBackwardCompat:
    def test_old_calling_convention_no_result_text(self):
        sup = RuntimeSupervisor()
        # Old call site that does not pass result_text — must not raise.
        sup.record_tool_call(tool_name="x", success=False)
        assert len(sup._tool_call_history) == 1
        # And known-error path obviously does not match (no text).
        sup.record_tool_call(tool_name="x", success=False)
        sup.record_tool_call(tool_name="x", success=False)
        intervention = sup._check_tool_thrashing(0)
        # Falls through to the original normal-failure path.
        assert intervention is not None
        assert intervention.level == InterventionLevel.STRATEGY_SWITCH
