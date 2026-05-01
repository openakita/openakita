"""L1 Unit Tests: LoopBudgetGuard token-anomaly + near-context behavior.

These tests target the long-task safety net that protects ReAct loops from
runaway token growth without prematurely killing well-behaved long tasks.

Specifically, they cover:

1. ``check_token_growth`` honors ``context_safe`` + ``near_context_ratio``:
   a high prompt is OK as long as we are not near the model context window.
2. ``near_context_ratio`` is configurable (the value used to be hardcoded
   to 0.85; it should now follow the dataclass default of 0.98 and be
   overridable per call).
3. The recovery counter does not eat a slot when compaction succeeds in
   bringing pressure back into the safe zone.
4. The diagnostic suffix is appended to the user-facing message so the
   reason for termination/compaction is observable.
"""

from __future__ import annotations

from openakita.core.loop_budget_guard import LoopBudgetDecision, LoopBudgetGuard


def _seed_with_tool_calls(guard: LoopBudgetGuard, n: int) -> None:
    """Drive ``total_tool_calls_seen`` past the half-budget gate without
    triggering the budget exceed branch."""
    guard.total_tool_calls_seen = n


class TestNearContextRatio:
    def test_default_ratio_is_relaxed(self):
        """Default near-context ratio should be >= 0.95 so high but
        non-window-saturating prompts don't get hard-killed."""
        guard = LoopBudgetGuard()
        assert guard.near_context_ratio >= 0.95

    def test_safe_context_skips_termination_below_ratio(self):
        """170k of 200k = 0.85 < default 0.98 → must not terminate when
        context is reported safe."""
        guard = LoopBudgetGuard(max_total_tool_calls=20, token_anomaly_threshold=80_000)
        _seed_with_tool_calls(guard, 15)
        decision = guard.check_token_growth(
            input_tokens=170_000,
            output_tokens=0,
            context_safe=True,
            max_context_tokens=200_000,
        )
        assert isinstance(decision, LoopBudgetDecision)
        assert decision.should_stop is False

    def test_per_call_override_can_tighten(self):
        """Even if the dataclass default is loose, callers can pass a
        stricter near_context_ratio to catch borderline cases.

        Recoveries are pre-exhausted so we observe the terminate branch
        directly (the recoverable branch is exercised in
        ``test_first_hit_is_recoverable``)."""
        guard = LoopBudgetGuard(max_total_tool_calls=20, token_anomaly_threshold=80_000)
        _seed_with_tool_calls(guard, 15)
        guard.token_anomaly_recoveries = 1
        decision = guard.check_token_growth(
            input_tokens=170_000,
            output_tokens=0,
            context_safe=True,
            max_context_tokens=200_000,
            near_context_ratio=0.80,
            max_recoveries=1,
        )
        assert decision.should_stop is True
        assert decision.exit_reason == "token_growth_terminated"
        assert "ctx_max=200000" in decision.message
        assert "hard_terminate_ratio=0.80" in decision.message

    def test_near_window_still_terminates(self):
        """At 99% of window, even with context_safe=True and a permissive
        ratio, we should hit the anomaly branch (recoveries exhausted)."""
        guard = LoopBudgetGuard(max_total_tool_calls=20, token_anomaly_threshold=80_000)
        _seed_with_tool_calls(guard, 15)
        guard.token_anomaly_recoveries = 1
        decision = guard.check_token_growth(
            input_tokens=198_000,
            output_tokens=0,
            context_safe=True,
            max_context_tokens=200_000,
            near_context_ratio=0.98,
            max_recoveries=1,
        )
        assert decision.should_stop is True

    def test_first_hit_is_recoverable_not_terminated(self):
        """The first time we trip the anomaly threshold the guard should
        ask for a compaction (``should_warn``), not terminate immediately."""
        guard = LoopBudgetGuard(max_total_tool_calls=20, token_anomaly_threshold=80_000)
        _seed_with_tool_calls(guard, 15)
        decision = guard.check_token_growth(
            input_tokens=170_000,
            output_tokens=0,
            context_safe=True,
            max_context_tokens=200_000,
            near_context_ratio=0.80,
            max_recoveries=1,
        )
        assert decision.should_stop is False
        assert decision.should_warn is True
        assert decision.exit_reason == "token_growth_recoverable"


class TestRecoveryAccounting:
    def test_recovered_call_increments_counter(self):
        guard = LoopBudgetGuard()
        assert guard.token_anomaly_recoveries == 0
        guard.check_token_growth(0, 0, recovered=True)
        assert guard.token_anomaly_recoveries == 1

    def test_safe_after_recover_does_not_consume_slot(self):
        """If, after a compaction, the next check arrives with
        ``context_safe=True`` and below the near-context line, the guard
        must short-circuit BEFORE bumping ``token_anomaly_recoveries``."""
        guard = LoopBudgetGuard()
        guard.check_token_growth(
            input_tokens=10_000,
            output_tokens=0,
            context_safe=True,
            max_context_tokens=200_000,
        )
        assert guard.token_anomaly_recoveries == 0


class TestRatioClamping:
    def test_out_of_range_ratio_is_clamped(self):
        """A misconfigured ratio (e.g. 5.0 from a typo) must be clamped
        so it cannot disable the safety net silently. With recoveries
        exhausted, 199k of 200k must terminate even with a clamped 0.99."""
        guard = LoopBudgetGuard(max_total_tool_calls=20, token_anomaly_threshold=80_000)
        _seed_with_tool_calls(guard, 15)
        guard.token_anomaly_recoveries = 1
        decision = guard.check_token_growth(
            input_tokens=199_000,
            output_tokens=0,
            context_safe=True,
            max_context_tokens=200_000,
            near_context_ratio=5.0,
            max_recoveries=1,
        )
        assert decision.should_stop is True
        assert "hard_terminate_ratio=0.99" in decision.message
