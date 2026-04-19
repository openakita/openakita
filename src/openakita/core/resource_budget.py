"""
Task-level Resource Budget Management (Agent Harness: Resource Budget)

Allocate and enforce budgets for each task, similar to how an OS
manages process resources. Graduated actions are taken automatically
as the budget approaches exhaustion.

Budget dimensions:
- max_tokens: max token consumption per task
- max_cost_usd: max cost per task
- max_duration_seconds: max wall-clock duration per task
- max_iterations: max iteration count
- max_tool_calls: max tool invocation count

Budget policy:
- Warning (80%): inject budget warning
- Downgrade (90%): switch to a cheaper model
- Pause (100%): pause execution and notify the user
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class BudgetAction(Enum):
    """Budget action (value indicates severity; higher is more severe)."""

    OK = 0
    WARNING = 1
    DOWNGRADE = 2
    PAUSE = 3


class BudgetExceeded(Exception):
    """Budget exceeded exception."""

    def __init__(self, dimension: str, used: float, limit: float):
        self.dimension = dimension
        self.used = used
        self.limit = limit
        super().__init__(f"Budget exceeded: {dimension} ({used:.1f}/{limit:.1f})")


@dataclass
class BudgetConfig:
    """Budget configuration."""

    max_tokens: int = 0  # 0 = no limit
    max_cost_usd: float = 0.0  # 0 = no limit
    max_duration_seconds: int = 0  # 0 = no limit
    max_iterations: int = 0  # 0 = no limit
    max_tool_calls: int = 0  # 0 = no limit

    warning_threshold: float = 0.80
    downgrade_threshold: float = 0.90
    pause_threshold: float = 1.0

    # Default policy when budget is exceeded: "warning", "downgrade", "pause"
    exceed_policy: str = "pause"

    @property
    def has_any_limit(self) -> bool:
        return any(
            [
                self.max_tokens > 0,
                self.max_cost_usd > 0,
                self.max_duration_seconds > 0,
                self.max_iterations > 0,
                self.max_tool_calls > 0,
            ]
        )


@dataclass
class BudgetStatus:
    """Budget status snapshot."""

    action: BudgetAction
    dimension: str = ""
    usage_ratio: float = 0.0
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class ResourceBudget:
    """
    Task-level resource budget manager.

    Created at task start; consumption accumulates as the task executes.
    ReasoningEngine calls check() each iteration to inspect the budget.
    """

    def __init__(
        self, config: BudgetConfig | None = None, parent: ResourceBudget | None = None
    ) -> None:
        self._config = config or BudgetConfig()
        self._parent: ResourceBudget | None = parent
        self._start_time: float = 0.0

        # Cumulative consumption
        self._tokens_used: int = 0
        self._cost_used: float = 0.0
        self._iterations_used: int = 0
        self._tool_calls_used: int = 0

        # Budget warning fired flags (avoid duplicate alerts)
        self._warning_fired: set[str] = set()
        self._downgrade_fired: bool = False

    @property
    def config(self) -> BudgetConfig:
        return self._config

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def cost_used(self) -> float:
        return self._cost_used

    @property
    def elapsed_seconds(self) -> float:
        if self._start_time <= 0:
            return 0.0
        return time.time() - self._start_time

    def start(self) -> None:
        """Called when the task starts."""
        self._start_time = time.time()
        self._tokens_used = 0
        self._cost_used = 0.0
        self._iterations_used = 0
        self._tool_calls_used = 0
        self._warning_fired.clear()
        self._downgrade_fired = False

    def record_tokens(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Record token consumption."""
        self._tokens_used += input_tokens + output_tokens
        if self._parent is not None:
            self._parent.record_tokens(input_tokens, output_tokens)

    def record_cost(self, cost_usd: float) -> None:
        """Record cost."""
        self._cost_used += cost_usd
        if self._parent is not None:
            self._parent.record_cost(cost_usd)

    def record_iteration(self) -> None:
        """Record an iteration."""
        self._iterations_used += 1
        if self._parent is not None:
            self._parent.record_iteration()

    def record_tool_calls(self, count: int = 1) -> None:
        """Record tool calls."""
        self._tool_calls_used += count
        if self._parent is not None:
            self._parent.record_tool_calls(count)

    def allocate_sub_budget(self, ratio: float = 0.5) -> ResourceBudget:
        """Allocate a sub-budget for a subtask/delegation (scaled by ratio)."""
        ratio = max(0.1, min(1.0, ratio))
        sub_config = BudgetConfig(
            max_tokens=int(self._config.max_tokens * ratio) if self._config.max_tokens else 0,
            max_cost_usd=self._config.max_cost_usd * ratio if self._config.max_cost_usd else 0.0,
            max_duration_seconds=int(self._config.max_duration_seconds * ratio)
            if self._config.max_duration_seconds
            else 0,
            max_iterations=int(self._config.max_iterations * ratio)
            if self._config.max_iterations
            else 0,
            max_tool_calls=int(self._config.max_tool_calls * ratio)
            if self._config.max_tool_calls
            else 0,
            warning_threshold=self._config.warning_threshold,
            downgrade_threshold=self._config.downgrade_threshold,
            pause_threshold=self._config.pause_threshold,
            exceed_policy=self._config.exceed_policy,
        )
        sub = ResourceBudget(sub_config, parent=self)
        sub.start()
        return sub

    def check(self) -> BudgetStatus:
        """
        Check budget status and return the most severe status.

        Should be called at the start of each iteration.
        """
        if not self._config.has_any_limit:
            return BudgetStatus(action=BudgetAction.OK)

        worst = BudgetStatus(action=BudgetAction.OK)

        checks = self._check_all_dimensions()
        for status in checks:
            if status.action.value > worst.action.value:
                worst = status

        if worst.action != BudgetAction.OK:
            logger.info(
                f"[Budget] {worst.action.name}: {worst.dimension} "
                f"({worst.usage_ratio:.0%}) — {worst.message}"
            )

            # Decision Trace
            try:
                from ..tracing.tracer import get_tracer

                tracer = get_tracer()
                tracer.record_decision(
                    decision_type="budget_check",
                    reasoning=worst.message,
                    outcome=worst.action.name,
                    dimension=worst.dimension,
                    usage_ratio=worst.usage_ratio,
                )
            except Exception:
                pass

        return worst

    def get_budget_prompt_warning(self) -> str:
        """Deprecated: no longer injects warnings into conversation."""
        return ""

    def get_summary(self) -> dict[str, Any]:
        """Return a budget summary."""
        return {
            "tokens_used": self._tokens_used,
            "cost_used": round(self._cost_used, 6),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "iterations_used": self._iterations_used,
            "tool_calls_used": self._tool_calls_used,
            "limits": {
                "max_tokens": self._config.max_tokens,
                "max_cost_usd": self._config.max_cost_usd,
                "max_duration_seconds": self._config.max_duration_seconds,
                "max_iterations": self._config.max_iterations,
                "max_tool_calls": self._config.max_tool_calls,
            },
        }

    # ==================== Internal methods ====================

    def _check_all_dimensions(self) -> list[BudgetStatus]:
        """Check all budget dimensions."""
        results: list[BudgetStatus] = []

        if self._config.max_tokens > 0:
            results.append(
                self._check_dimension(
                    "tokens",
                    self._tokens_used,
                    self._config.max_tokens,
                )
            )

        if self._config.max_cost_usd > 0:
            results.append(
                self._check_dimension(
                    "cost_usd",
                    self._cost_used,
                    self._config.max_cost_usd,
                )
            )

        if self._config.max_duration_seconds > 0:
            results.append(
                self._check_dimension(
                    "duration",
                    self.elapsed_seconds,
                    self._config.max_duration_seconds,
                )
            )

        if self._config.max_iterations > 0:
            results.append(
                self._check_dimension(
                    "iterations",
                    self._iterations_used,
                    self._config.max_iterations,
                )
            )

        if self._config.max_tool_calls > 0:
            results.append(
                self._check_dimension(
                    "tool_calls",
                    self._tool_calls_used,
                    self._config.max_tool_calls,
                )
            )

        return results

    def _check_dimension(
        self,
        dimension: str,
        used: float,
        limit: float,
    ) -> BudgetStatus:
        """Check a single dimension."""
        if limit <= 0:
            return BudgetStatus(action=BudgetAction.OK, dimension=dimension)

        ratio = used / limit

        if ratio >= self._config.pause_threshold:
            return BudgetStatus(
                action=BudgetAction.PAUSE,
                dimension=dimension,
                usage_ratio=ratio,
                message=f"{dimension} budget exhausted ({used:.1f}/{limit:.1f})",
            )

        if ratio >= self._config.downgrade_threshold:
            return BudgetStatus(
                action=BudgetAction.DOWNGRADE,
                dimension=dimension,
                usage_ratio=ratio,
                message=f"{dimension} approaching limit ({used:.1f}/{limit:.1f})",
            )

        if ratio >= self._config.warning_threshold:
            return BudgetStatus(
                action=BudgetAction.WARNING,
                dimension=dimension,
                usage_ratio=ratio,
                message=f"{dimension} at {ratio:.0%} of budget ({used:.1f}/{limit:.1f})",
            )

        return BudgetStatus(
            action=BudgetAction.OK,
            dimension=dimension,
            usage_ratio=ratio,
        )


def create_budget_from_settings() -> ResourceBudget:
    """Create a budget manager from settings."""
    try:
        from ..config import settings

        config = BudgetConfig(
            max_tokens=getattr(settings, "task_budget_tokens", 0),
            max_cost_usd=getattr(settings, "task_budget_cost", 0.0),
            max_duration_seconds=getattr(settings, "task_budget_duration", 0),
            max_iterations=getattr(settings, "task_budget_iterations", 0),
            max_tool_calls=getattr(settings, "task_budget_tool_calls", 0),
        )
        return ResourceBudget(config)
    except Exception:
        return ResourceBudget()
