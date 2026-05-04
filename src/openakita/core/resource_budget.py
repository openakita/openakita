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

    max_tokens: int = 0  # 0 = 不限制
    max_cost_usd: float = 0.0  # 0 = 不限制
    max_duration_seconds: int = 0  # 0 = 不限制
    max_iterations: int = 0  # 0 = 不限制
    max_tool_calls: int = 0  # 0 = 不限制

    warning_threshold: float = 0.80
    downgrade_threshold: float = 0.90
    pause_threshold: float = 1.0

    # 超出预算时的默认策略: "warning", "downgrade", "pause"
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

    # 默认"近期进展"判定窗口：60 秒。任务在 duration 命中 100% 时，
    # 若窗口内仍有 tool_call / token 产出，则视为正常推进，降级为 WARNING
    # 而非 PAUSE，避免误杀长任务。可通过 had_recent_progress(window) 自定义。
    DEFAULT_PROGRESS_WINDOW_SECONDS: float = 60.0

    def __init__(
        self, config: BudgetConfig | None = None, parent: ResourceBudget | None = None
    ) -> None:
        self._config = config or BudgetConfig()
        self._parent: ResourceBudget | None = parent
        self._start_time: float = 0.0

        # 累计消耗
        self._tokens_used: int = 0
        self._cost_used: float = 0.0
        self._iterations_used: int = 0
        self._tool_calls_used: int = 0

        # 真实进展时间戳：用于 duration 维度的"有进展则不强杀"判定。
        # iteration 不算进展（每轮固定调用一次，会让"有进展"永远为真）。
        self._last_tool_call_at: float = 0.0
        self._last_token_record_at: float = 0.0

        # 预算警告已触发标记（避免重复告警）
        self._warning_fired: set[str] = set()
        self._downgrade_fired: bool = False

        # 阈值去抖：每个 (dimension, threshold_name) 仅触发一次 emit，
        # 避免 80% / 90% 反复刷屏污染日志和前端 banner。
        # threshold_name ∈ {"warning", "downgrade", "pause"}。
        self._emitted_thresholds: set[tuple[str, str]] = set()

        # 已被「有进展自动续期」豁免过的 PAUSE 次数（仅供运维统计）。
        self._duration_renewals: int = 0

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
        self._last_tool_call_at = 0.0
        self._last_token_record_at = 0.0
        self._warning_fired.clear()
        self._downgrade_fired = False
        self._emitted_thresholds.clear()
        self._duration_renewals = 0

    def record_tokens(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """记录 token 消耗"""
        if input_tokens or output_tokens:
            self._tokens_used += input_tokens + output_tokens
            self._last_token_record_at = time.time()
        if self._parent is not None:
            self._parent.record_tokens(input_tokens, output_tokens)

    def record_cost(self, cost_usd: float) -> None:
        """记录成本"""
        self._cost_used += cost_usd
        if self._parent is not None:
            self._parent.record_cost(cost_usd)

    def record_iteration(self) -> None:
        """记录迭代"""
        self._iterations_used += 1
        if self._parent is not None:
            self._parent.record_iteration()

    def record_tool_calls(self, count: int = 1) -> None:
        """记录工具调用"""
        if count > 0:
            self._tool_calls_used += count
            self._last_tool_call_at = time.time()
        if self._parent is not None:
            self._parent.record_tool_calls(count)

    def had_recent_progress(self, window_seconds: float | None = None) -> bool:
        """近窗口内是否有真实进展（tool_call 或 token 产出）。

        ``record_iteration`` 不算进展——它每轮固定调用一次，会让本判定永远为真。
        仅 ``record_tool_calls`` / ``record_tokens`` 视为推进证据。
        """
        if window_seconds is None:
            window_seconds = self.DEFAULT_PROGRESS_WINDOW_SECONDS
        latest = max(self._last_tool_call_at, self._last_token_record_at)
        if latest <= 0:
            return False
        return (time.time() - latest) <= window_seconds

    def should_emit_threshold(self, dimension: str, threshold_name: str) -> bool:
        """阈值去抖：每个 (dimension, threshold_name) 仅返回 True 一次。

        触发后内部会自动登记，下次同维度同阈值返回 False。供 reasoning_engine
        判断是否需要向前端 yield budget_warning 事件。
        """
        key = (dimension, threshold_name)
        if key in self._emitted_thresholds:
            return False
        self._emitted_thresholds.add(key)
        return True

    @property
    def duration_renewals(self) -> int:
        return self._duration_renewals

    def allocate_sub_budget(self, ratio: float = 0.5) -> ResourceBudget:
        """为子任务/委派分配预算（按比例缩减）"""
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
        """检查单个维度"""
        if limit <= 0:
            return BudgetStatus(action=BudgetAction.OK, dimension=dimension)

        ratio = used / limit

        if ratio >= self._config.pause_threshold:
            # duration 维度的"有进展则不强杀"豁免：当且仅当
            #   1) 维度是 duration（其它维度——tokens/cost/iterations/
            #      tool_calls——本身就是累计计数，命中 100% 是真的"用尽"），
            #   2) 最近 60s 内有 tool_call 或 token 产出（说明任务在真正推进，
            #      不是死循环），
            # 时降级为 WARNING，让 ReAct 主循环继续，不强制 PAUSE。
            # 这是为了对齐 LoopBudgetGuard 的"病态才打断、正常进展放行"哲学。
            if dimension == "duration" and self.had_recent_progress():
                self._duration_renewals += 1
                return BudgetStatus(
                    action=BudgetAction.WARNING,
                    dimension=dimension,
                    usage_ratio=ratio,
                    message=(
                        f"{dimension} over budget but task is making progress "
                        f"({used:.1f}/{limit:.1f}, renewals={self._duration_renewals})"
                    ),
                    details={"renewed": True, "renewals": self._duration_renewals},
                )
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
