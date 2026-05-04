"""问题 2 回归测试：duration 维度的"有进展则不强杀"豁免。

旧行为：duration 命中 100% 直接 PAUSE，哪怕任务正在持续调用工具。
新行为：duration 命中 100% 时检查近 60s 是否有 tool_call / token 进展，
若有进展则降级为 WARNING（自动续期，不强杀）；无进展才 PAUSE。

同时验证：
- 其它维度（tokens / iterations / tool_calls / cost）不被豁免
- record_iteration 不算"进展"（避免每轮都调而让豁免永远生效）
- 阈值去抖：should_emit_threshold 同一阈值仅触发一次
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from openakita.core.resource_budget import (
    BudgetAction,
    BudgetConfig,
    ResourceBudget,
)


def _make_budget(
    *,
    duration: int = 600,
    tokens: int = 0,
    iterations: int = 0,
    tool_calls: int = 0,
    cost: float = 0.0,
) -> ResourceBudget:
    config = BudgetConfig(
        max_tokens=tokens,
        max_cost_usd=cost,
        max_duration_seconds=duration,
        max_iterations=iterations,
        max_tool_calls=tool_calls,
    )
    budget = ResourceBudget(config)
    budget.start()
    return budget


def _shift_start(budget: ResourceBudget, seconds_ago: float) -> None:
    """将 budget 开始时间往前挪 seconds_ago 秒（伪造任务已运行了那么久）。"""
    budget._start_time = time.time() - seconds_ago


# ---------------------------------------------------------------------------
# had_recent_progress
# ---------------------------------------------------------------------------


def test_had_recent_progress_false_initially() -> None:
    budget = _make_budget(duration=600)
    assert budget.had_recent_progress() is False


def test_had_recent_progress_true_after_tool_call() -> None:
    budget = _make_budget(duration=600)
    budget.record_tool_calls(1)
    assert budget.had_recent_progress() is True


def test_had_recent_progress_true_after_token_record() -> None:
    budget = _make_budget(duration=600)
    budget.record_tokens(input_tokens=100, output_tokens=50)
    assert budget.had_recent_progress() is True


def test_had_recent_progress_false_outside_window() -> None:
    budget = _make_budget(duration=600)
    budget.record_tool_calls(1)
    # 把上次 tool_call 时间挪到 90 秒前，超出默认 60s 窗口
    budget._last_tool_call_at = time.time() - 90.0
    assert budget.had_recent_progress() is False
    assert budget.had_recent_progress(window_seconds=120.0) is True


def test_record_iteration_does_not_count_as_progress() -> None:
    """关键不变量：record_iteration 不算进展，否则每轮都调会让豁免永远生效。"""
    budget = _make_budget(duration=600)
    for _ in range(10):
        budget.record_iteration()
    assert budget.had_recent_progress() is False


# ---------------------------------------------------------------------------
# duration 维度：有进展 → WARNING；无进展 → PAUSE
# ---------------------------------------------------------------------------


def test_duration_100pct_with_recent_progress_returns_warning() -> None:
    """任务持续 700s 但近 60s 有 tool_call → 降级为 WARNING，不 PAUSE。"""
    budget = _make_budget(duration=600)
    _shift_start(budget, seconds_ago=700.0)
    # 模拟 LLM 刚刚调用过工具
    budget.record_tool_calls(1)

    status = budget.check()
    assert status.dimension == "duration"
    assert status.action == BudgetAction.WARNING, (
        f"应降级为 WARNING（有进展），但 action={status.action}"
    )
    assert status.usage_ratio > 1.0
    assert "renewed" in status.details and status.details["renewed"] is True
    assert budget.duration_renewals == 1


def test_duration_100pct_without_recent_progress_returns_pause() -> None:
    """任务持续 700s 且超过 60s 没有 tool_call/token 产出 → PAUSE。"""
    budget = _make_budget(duration=600)
    _shift_start(budget, seconds_ago=700.0)
    # 模拟最后一次 tool_call 是 100 秒前（在 600s 预算前 200s）
    budget.record_tool_calls(1)
    budget._last_tool_call_at = time.time() - 100.0

    status = budget.check()
    assert status.dimension == "duration"
    assert status.action == BudgetAction.PAUSE, (
        f"无进展应 PAUSE，但 action={status.action}"
    )
    assert budget.duration_renewals == 0


def test_duration_renewals_accumulate_across_checks() -> None:
    """每次有进展的命中都 +1 renewals，便于运维诊断。"""
    budget = _make_budget(duration=600)
    _shift_start(budget, seconds_ago=700.0)
    budget.record_tool_calls(1)
    budget.check()
    assert budget.duration_renewals == 1
    # 模拟下一轮又调了一次工具
    budget.record_tool_calls(1)
    budget.check()
    assert budget.duration_renewals == 2


# ---------------------------------------------------------------------------
# 反例：其它维度命中 100% 不享有豁免
# ---------------------------------------------------------------------------


def test_tokens_100pct_with_recent_progress_still_pause() -> None:
    """tokens 是累计计数，命中真的就是用尽，不能因"刚有进展"而续期。"""
    budget = _make_budget(tokens=1000, duration=0)
    budget.record_tokens(input_tokens=600, output_tokens=500)
    status = budget.check()
    assert status.dimension == "tokens"
    assert status.action == BudgetAction.PAUSE


def test_tool_calls_100pct_still_pause() -> None:
    budget = _make_budget(tool_calls=5, duration=0)
    budget.record_tool_calls(6)
    status = budget.check()
    assert status.dimension == "tool_calls"
    assert status.action == BudgetAction.PAUSE


def test_iterations_100pct_still_pause() -> None:
    budget = _make_budget(iterations=3, duration=0)
    for _ in range(4):
        budget.record_iteration()
    status = budget.check()
    assert status.dimension == "iterations"
    assert status.action == BudgetAction.PAUSE


# ---------------------------------------------------------------------------
# 阈值去抖：should_emit_threshold
# ---------------------------------------------------------------------------


def test_should_emit_threshold_only_once_per_dimension_and_level() -> None:
    budget = _make_budget(duration=600)
    assert budget.should_emit_threshold("duration", "warning") is True
    assert budget.should_emit_threshold("duration", "warning") is False
    assert budget.should_emit_threshold("duration", "downgrade") is True
    assert budget.should_emit_threshold("duration", "downgrade") is False
    # 不同维度互不影响
    assert budget.should_emit_threshold("tokens", "warning") is True


def test_start_resets_threshold_emissions() -> None:
    """新任务开始时（start 重置）应允许重新 emit。"""
    budget = _make_budget(duration=600)
    budget.should_emit_threshold("duration", "warning")
    budget.start()
    assert budget.should_emit_threshold("duration", "warning") is True


# ---------------------------------------------------------------------------
# 80% / 90% 阈值仍按预期触发 WARNING / DOWNGRADE
# ---------------------------------------------------------------------------


def test_duration_80pct_returns_warning() -> None:
    budget = _make_budget(duration=600)
    _shift_start(budget, seconds_ago=480.0)  # 80%
    status = budget.check()
    assert status.dimension == "duration"
    assert status.action == BudgetAction.WARNING


def test_duration_90pct_returns_downgrade() -> None:
    budget = _make_budget(duration=600)
    _shift_start(budget, seconds_ago=540.0)  # 90%
    status = budget.check()
    assert status.dimension == "duration"
    assert status.action == BudgetAction.DOWNGRADE


# ---------------------------------------------------------------------------
# 整合场景：模拟真实长任务（700s，每 30s 一个 tool_call）→ 不 PAUSE
# ---------------------------------------------------------------------------


def test_long_running_task_never_pauses_while_making_progress() -> None:
    """模拟用户日志里的 25 轮工具调用真实工作流：duration 超 100% 但持续推进。"""
    budget = _make_budget(duration=600)

    # 模拟从 t=0 到 t=700 每 30s 调一次工具，每次 check 应当不 PAUSE
    for elapsed in range(30, 750, 30):
        _shift_start(budget, seconds_ago=float(elapsed))
        budget.record_tool_calls(1)
        budget._last_tool_call_at = time.time()  # 刚刚调用
        status = budget.check()
        assert status.action != BudgetAction.PAUSE, (
            f"在 elapsed={elapsed}s 时不应 PAUSE，但收到 {status.action}（"
            f"ratio={status.usage_ratio:.2f}）"
        )

    # 最后停止调用工具，再过窗口才能触发 PAUSE
    budget._last_tool_call_at = time.time() - 90.0  # 90s 前最后一次调用
    _shift_start(budget, seconds_ago=800.0)
    final_status = budget.check()
    assert final_status.action == BudgetAction.PAUSE
    assert final_status.dimension == "duration"
