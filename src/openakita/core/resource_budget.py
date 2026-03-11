"""
任务级资源预算管理 (Agent Harness: Resource Budget)

像操作系统管理进程资源一样，为每个任务分配和强制执行预算。
当预算接近耗尽时自动采取分级措施。

预算维度:
- max_tokens: 单次任务最大 token 消耗
- max_cost_usd: 单次任务最大成本
- max_duration_seconds: 单次任务最大时长
- max_iterations: 最大迭代次数
- max_tool_calls: 最大工具调用次数

预算策略:
- Warning (80%): 注入预算警告
- Downgrade (90%): 切换到更便宜的模型
- Pause (100%): 暂停执行，通知用户
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class BudgetAction(Enum):
    """预算动作（值为严重程度，越大越严重）"""
    OK = 0
    WARNING = 1
    DOWNGRADE = 2
    PAUSE = 3


class BudgetExceeded(Exception):
    """预算耗尽异常"""
    def __init__(self, dimension: str, used: float, limit: float):
        self.dimension = dimension
        self.used = used
        self.limit = limit
        super().__init__(f"Budget exceeded: {dimension} ({used:.1f}/{limit:.1f})")


@dataclass
class BudgetConfig:
    """预算配置"""
    max_tokens: int = 0          # 0 = 不限制
    max_cost_usd: float = 0.0   # 0 = 不限制
    max_duration_seconds: int = 0  # 0 = 不限制
    max_iterations: int = 0      # 0 = 不限制
    max_tool_calls: int = 0      # 0 = 不限制

    warning_threshold: float = 0.80
    downgrade_threshold: float = 0.90
    pause_threshold: float = 1.0

    # 超出预算时的默认策略: "warning", "downgrade", "pause"
    exceed_policy: str = "pause"

    @property
    def has_any_limit(self) -> bool:
        return any([
            self.max_tokens > 0,
            self.max_cost_usd > 0,
            self.max_duration_seconds > 0,
            self.max_iterations > 0,
            self.max_tool_calls > 0,
        ])


@dataclass
class BudgetStatus:
    """预算状态快照"""
    action: BudgetAction
    dimension: str = ""
    usage_ratio: float = 0.0
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class ResourceBudget:
    """
    任务级资源预算管理器。

    每个任务开始时创建，随任务执行累加消耗。
    ReasoningEngine 每轮迭代调用 check() 检查预算。
    """

    def __init__(self, config: BudgetConfig | None = None) -> None:
        self._config = config or BudgetConfig()
        self._start_time: float = 0.0

        # 累计消耗
        self._tokens_used: int = 0
        self._cost_used: float = 0.0
        self._iterations_used: int = 0
        self._tool_calls_used: int = 0

        # 预算警告已触发标记（避免重复告警）
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
        """任务开始时调用"""
        self._start_time = time.time()
        self._tokens_used = 0
        self._cost_used = 0.0
        self._iterations_used = 0
        self._tool_calls_used = 0
        self._warning_fired.clear()
        self._downgrade_fired = False

    def record_tokens(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """记录 token 消耗"""
        self._tokens_used += input_tokens + output_tokens

    def record_cost(self, cost_usd: float) -> None:
        """记录成本"""
        self._cost_used += cost_usd

    def record_iteration(self) -> None:
        """记录迭代"""
        self._iterations_used += 1

    def record_tool_calls(self, count: int = 1) -> None:
        """记录工具调用"""
        self._tool_calls_used += count

    def allocate_sub_budget(self, ratio: float = 0.5) -> "ResourceBudget":
        """为子任务/委派分配预算（按比例缩减）"""
        ratio = max(0.1, min(1.0, ratio))
        sub_config = BudgetConfig(
            max_tokens=int(self._config.max_tokens * ratio) if self._config.max_tokens else 0,
            max_cost_usd=self._config.max_cost_usd * ratio if self._config.max_cost_usd else 0.0,
            max_duration_seconds=int(self._config.max_duration_seconds * ratio) if self._config.max_duration_seconds else 0,
            max_iterations=int(self._config.max_iterations * ratio) if self._config.max_iterations else 0,
            max_tool_calls=int(self._config.max_tool_calls * ratio) if self._config.max_tool_calls else 0,
            warning_threshold=self._config.warning_threshold,
            downgrade_threshold=self._config.downgrade_threshold,
            pause_threshold=self._config.pause_threshold,
            exceed_policy=self._config.exceed_policy,
        )
        sub = ResourceBudget(sub_config)
        sub.start()
        return sub

    def check(self) -> BudgetStatus:
        """
        检查预算状态，返回最严重的预算状态。

        应在每轮迭代开始时调用。
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
        """获取预算摘要"""
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

    # ==================== 内部方法 ====================

    def _check_all_dimensions(self) -> list[BudgetStatus]:
        """检查所有预算维度"""
        results: list[BudgetStatus] = []

        if self._config.max_tokens > 0:
            results.append(self._check_dimension(
                "tokens", self._tokens_used, self._config.max_tokens,
            ))

        if self._config.max_cost_usd > 0:
            results.append(self._check_dimension(
                "cost_usd", self._cost_used, self._config.max_cost_usd,
            ))

        if self._config.max_duration_seconds > 0:
            results.append(self._check_dimension(
                "duration", self.elapsed_seconds, self._config.max_duration_seconds,
            ))

        if self._config.max_iterations > 0:
            results.append(self._check_dimension(
                "iterations", self._iterations_used, self._config.max_iterations,
            ))

        if self._config.max_tool_calls > 0:
            results.append(self._check_dimension(
                "tool_calls", self._tool_calls_used, self._config.max_tool_calls,
            ))

        return results

    def _check_dimension(
        self, dimension: str, used: float, limit: float,
    ) -> BudgetStatus:
        """检查单个维度"""
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
    """从 settings 创建预算管理器"""
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
