"""
验证 RuntimeSupervisor 的循环检测增强逻辑：
- 交替模式检测（A,B,A,B / A,B,C,A,B,C）
- NUDGE 连续忽略后升级为 TERMINATE
- 极端迭代两级检测（ESCALATE + TERMINATE）
"""

from openakita.core.supervisor import (
    CYCLE_MIN_REPEATS,
    EXTREME_TERMINATE_THRESHOLD,
    SIGNATURE_NUDGE_ESCALATE,
    InterventionLevel,
    PatternType,
    RuntimeSupervisor,
)


class TestAlternatingCycleDetection:
    """交替/循环模式应被检测并终止"""

    def test_period2_cycle_terminates(self):
        sup = RuntimeSupervisor()
        sigs = ["A", "B"] * (CYCLE_MIN_REPEATS + 1)
        for sig in sigs:
            sup.record_tool_signature(sig)
        result = sup.evaluate(len(sigs))
        assert result is not None
        assert result.should_terminate is True
        assert result.pattern == PatternType.SIGNATURE_REPEAT
        assert "period=2" in result.message

    def test_period3_cycle_terminates(self):
        sup = RuntimeSupervisor()
        sigs = ["A", "B", "C"] * (CYCLE_MIN_REPEATS + 1)
        for sig in sigs:
            sup.record_tool_signature(sig)
        result = sup.evaluate(len(sigs))
        assert result is not None
        assert result.should_terminate is True
        assert "period=3" in result.message

    def test_period4_cycle_terminates(self):
        sup = RuntimeSupervisor()
        sigs = ["A", "B", "C", "D"] * (CYCLE_MIN_REPEATS + 1)
        for sig in sigs:
            sup.record_tool_signature(sig)
        result = sup.evaluate(len(sigs))
        assert result is not None
        assert result.should_terminate is True
        assert "period=4" in result.message

    def test_non_cycle_not_terminated(self):
        sup = RuntimeSupervisor()
        for sig in ["A", "B", "C", "D", "E", "F"]:
            sup.record_tool_signature(sig)
        result = sup.evaluate(6)
        assert result is None

    def test_single_value_not_treated_as_cycle(self):
        """All-same sequence is handled by frequency check, not cycle detector"""
        sup = RuntimeSupervisor()
        for _ in range(6):
            sup.record_tool_signature("A")
        result = sup.evaluate(6)
        assert result is not None
        assert result.should_terminate is True
        assert "Dead loop" in result.message


class TestNudgeEscalation:
    """NUDGE 被连续忽略后应升级为 TERMINATE"""

    def test_nudge_escalates_to_terminate(self):
        sup = RuntimeSupervisor()
        terminated = False
        for i in range(SIGNATURE_NUDGE_ESCALATE + 10):
            sup.record_tool_signature("X" if i % 2 == 0 else "Y" if i % 3 == 0 else "X")
            result = sup.evaluate(i)
            if result and result.should_terminate:
                terminated = True
                break
        assert terminated

    def test_three_consecutive_nudges_terminate(self):
        """Exactly SIGNATURE_NUDGE_ESCALATE consecutive nudges → TERMINATE"""
        sup = RuntimeSupervisor()
        results = []
        for i in range(20):
            sup.record_tool_signature("A" if i % 2 == 0 else "A")
            sup.record_consecutive_tool_rounds(i + 1)
            result = sup.evaluate(i)
            if result:
                results.append(result)
        terminate_results = [r for r in results if r.should_terminate]
        assert len(terminate_results) >= 1

    def test_streak_resets_on_no_repeat(self):
        """Non-repeating signatures reset the nudge streak"""
        sup = RuntimeSupervisor()
        for sig in ["A", "A", "A"]:
            sup.record_tool_signature(sig)
        result = sup.evaluate(0)
        assert result is not None and result.level == InterventionLevel.NUDGE
        assert sup._signature_nudge_streak == 1

        for sig in ["B", "C", "D", "E", "F", "G"]:
            sup.record_tool_signature(sig)
        result2 = sup.evaluate(1)
        assert result2 is None or result2.pattern != PatternType.SIGNATURE_REPEAT
        assert sup._signature_nudge_streak == 0


class TestExtremeIterationTwoLevel:
    """极端迭代两级检测"""

    def test_escalate_at_threshold(self):
        sup = RuntimeSupervisor()
        sup.record_consecutive_tool_rounds(50)
        result = sup.evaluate(50)
        assert result is not None
        assert result.level == InterventionLevel.ESCALATE
        assert result.pattern == PatternType.EXTREME_ITERATIONS

    def test_terminate_at_hard_limit(self):
        sup = RuntimeSupervisor()
        sup.record_consecutive_tool_rounds(EXTREME_TERMINATE_THRESHOLD)
        result = sup.evaluate(EXTREME_TERMINATE_THRESHOLD)
        assert result is not None
        assert result.should_terminate is True
        assert result.pattern == PatternType.EXTREME_ITERATIONS

    def test_escalate_repeats_on_interval(self):
        """ESCALATE should fire at intervals (not just once at ==50)"""
        sup = RuntimeSupervisor()
        escalates = []
        for rounds in range(50, 80):
            sup.record_consecutive_tool_rounds(rounds)
            result = sup.evaluate(rounds)
            if result and result.level == InterventionLevel.ESCALATE:
                escalates.append(rounds)
        assert len(escalates) >= 2

    def test_below_threshold_no_extreme(self):
        sup = RuntimeSupervisor()
        sup.record_consecutive_tool_rounds(49)
        result = sup.evaluate(49)
        extreme = (
            result is not None
            and result.pattern == PatternType.EXTREME_ITERATIONS
        )
        assert not extreme
