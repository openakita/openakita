"""
失败分析管线 (Agent Harness: Failure Analysis Pipeline)

利用 DecisionTrace 数据和任务执行记录，对失败任务进行结构化分析，
识别 Harness 层面的缺陷并生成改进建议。

分析维度:
- 根因分类: context_loss / tool_limitation / plan_deficiency / loop / budget_exhaustion / external_failure
- Harness 缺口识别: missing_tool / insufficient_docs / missing_guardrail / weak_verification / poor_context_engineering
- 量化指标: tokens_wasted / time_wasted / iterations_before_failure
- 改进建议: 自动生成 Harness 改进建议
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class RootCause(StrEnum):
    """失败根因分类"""

    CONTEXT_LOSS = "context_loss"
    TOOL_LIMITATION = "tool_limitation"
    PLAN_DEFICIENCY = "plan_deficiency"
    LOOP_DETECTED = "loop_detected"
    BUDGET_EXHAUSTION = "budget_exhaustion"
    EXTERNAL_FAILURE = "external_failure"
    MODEL_LIMITATION = "model_limitation"
    USER_AMBIGUITY = "user_ambiguity"
    UNKNOWN = "unknown"


class HarnessGap(StrEnum):
    """Harness 缺口类型"""

    MISSING_TOOL = "missing_tool"
    INSUFFICIENT_DOCS = "insufficient_docs"
    MISSING_GUARDRAIL = "missing_guardrail"
    WEAK_VERIFICATION = "weak_verification"
    POOR_CONTEXT_ENGINEERING = "poor_context_engineering"
    SUPERVISION_GAP = "supervision_gap"
    BUDGET_MISCONFIGURED = "budget_misconfigured"
    NONE = "none"


@dataclass
class FailureMetrics:
    """失败任务量化指标"""

    total_tokens: int = 0
    total_iterations: int = 0
    total_tool_calls: int = 0
    elapsed_seconds: float = 0.0
    tokens_after_last_progress: int = 0  # 最后一次有效进展后浪费的 token
    error_count: int = 0
    loop_count: int = 0


@dataclass
class FailureAnalysisResult:
    """单次失败分析结果"""

    task_id: str
    timestamp: str
    root_cause: RootCause
    harness_gap: HarnessGap
    metrics: FailureMetrics
    evidence: list[str] = field(default_factory=list)
    suggestion: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)


class FailureAnalyzer:
    """
    失败分析器。

    从 react_trace、supervisor events、budget status 等数据源
    提取失败信号并进行分类分析。
    """

    def __init__(self, output_dir: str | Path = "data/failure_analysis") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._results: list[FailureAnalysisResult] = []

    def analyze_task(
        self,
        *,
        task_id: str = "",
        react_trace: list[dict] | None = None,
        supervisor_events: list[dict] | None = None,
        budget_summary: dict | None = None,
        exit_reason: str = "",
        task_description: str = "",
    ) -> FailureAnalysisResult:
        """
        分析一个失败任务。

        Args:
            task_id: 任务 ID
            react_trace: ReAct 循环追踪数据
            supervisor_events: RuntimeSupervisor 事件记录
            budget_summary: 预算使用摘要
            exit_reason: 退出原因
            task_description: 任务描述
        """
        react_trace = react_trace or []
        supervisor_events = supervisor_events or []
        budget_summary = budget_summary or {}

        metrics = self._compute_metrics(react_trace, budget_summary)
        root_cause = self._classify_root_cause(
            react_trace,
            supervisor_events,
            budget_summary,
            exit_reason,
        )
        harness_gap = self._identify_harness_gap(
            root_cause,
            react_trace,
            supervisor_events,
        )
        evidence = self._collect_evidence(
            react_trace,
            supervisor_events,
            exit_reason,
        )
        suggestion = self._generate_suggestion(root_cause, harness_gap, metrics)

        result = FailureAnalysisResult(
            task_id=task_id,
            timestamp=datetime.now().isoformat(),
            root_cause=root_cause,
            harness_gap=harness_gap,
            metrics=metrics,
            evidence=evidence,
            suggestion=suggestion,
            raw_data={
                "exit_reason": exit_reason,
                "task_description": task_description[:200] if task_description else "",
                "iterations": len(react_trace),
                "supervisor_event_count": len(supervisor_events),
            },
        )

        self._results.append(result)
        self._persist_result(result)

        logger.info(
            f"[FailureAnalysis] task={task_id[:8]} root_cause={root_cause.value} "
            f"harness_gap={harness_gap.value} iterations={metrics.total_iterations}"
        )

        # Decision Trace
        try:
            from ..tracing.tracer import get_tracer

            tracer = get_tracer()
            tracer.record_decision(
                decision_type="failure_analysis",
                reasoning=f"root_cause={root_cause.value}, gap={harness_gap.value}",
                outcome=suggestion[:200] if suggestion else "no_suggestion",
                task_id=task_id,
            )
        except Exception:
            pass

        return result

    # ==================== 根因分类 ====================

    def _classify_root_cause(
        self,
        react_trace: list[dict],
        supervisor_events: list[dict],
        budget_summary: dict,
        exit_reason: str,
    ) -> RootCause:
        """基于多信号分类失败根因"""

        if exit_reason == "budget_exceeded":
            return RootCause.BUDGET_EXHAUSTION

        if exit_reason in ("loop_terminated", "loop_detected"):
            return RootCause.LOOP_DETECTED

        if exit_reason == "max_iterations":
            # 检查是否是循环导致
            loop_events = [
                e
                for e in supervisor_events
                if e.get("pattern") in ("signature_repeat", "reasoning_loop")
            ]
            if loop_events:
                return RootCause.LOOP_DETECTED
            return RootCause.PLAN_DEFICIENCY

        # 检查工具错误模式
        tool_errors = self._count_tool_errors(react_trace)
        if tool_errors > len(react_trace) * 0.5:
            return RootCause.TOOL_LIMITATION

        # 检查外部失败
        external_patterns = ["API", "timeout", "connection", "HTTP", "502", "503"]
        for trace in react_trace[-5:]:
            for tr in trace.get("tool_results", []):
                content = str(tr.get("result_content", ""))
                if any(p in content for p in external_patterns):
                    return RootCause.EXTERNAL_FAILURE

        # 检查上下文丢失（压缩后迷失方向）
        compression_count = sum(1 for t in react_trace if t.get("context_compressed"))
        if compression_count >= 2:
            late_errors = self._count_tool_errors(react_trace[len(react_trace) // 2 :])
            if late_errors > 3:
                return RootCause.CONTEXT_LOSS

        return RootCause.UNKNOWN

    # ==================== Harness 缺口识别 ====================

    def _identify_harness_gap(
        self,
        root_cause: RootCause,
        react_trace: list[dict],
        supervisor_events: list[dict],
    ) -> HarnessGap:
        """基于根因和迹象识别 Harness 缺口"""

        if root_cause == RootCause.TOOL_LIMITATION:
            return HarnessGap.MISSING_TOOL

        if root_cause == RootCause.LOOP_DETECTED:
            # 检查 supervisor 是否及时介入
            loop_events = [
                e
                for e in supervisor_events
                if e.get("pattern") in ("signature_repeat", "reasoning_loop")
            ]
            if not loop_events:
                return HarnessGap.SUPERVISION_GAP
            return HarnessGap.POOR_CONTEXT_ENGINEERING

        if root_cause == RootCause.CONTEXT_LOSS:
            return HarnessGap.POOR_CONTEXT_ENGINEERING

        if root_cause == RootCause.BUDGET_EXHAUSTION:
            return HarnessGap.BUDGET_MISCONFIGURED

        if root_cause == RootCause.PLAN_DEFICIENCY:
            return HarnessGap.WEAK_VERIFICATION

        return HarnessGap.NONE

    # ==================== 证据收集 ====================

    def _collect_evidence(
        self,
        react_trace: list[dict],
        supervisor_events: list[dict],
        exit_reason: str,
    ) -> list[str]:
        """收集关键证据"""
        evidence = []

        if exit_reason:
            evidence.append(f"Exit reason: {exit_reason}")

        evidence.append(f"Total iterations: {len(react_trace)}")

        # 最后几轮的工具调用
        for trace in react_trace[-3:]:
            tools = [tc.get("name", "?") for tc in trace.get("tool_calls", [])]
            if tools:
                evidence.append(f"Iter {trace.get('iteration', '?')}: tools={tools}")

        # Supervisor 事件
        for event in supervisor_events[-3:]:
            evidence.append(
                f"Supervisor: {event.get('pattern', '?')} level={event.get('level', '?')}"
            )

        return evidence

    # ==================== 改进建议 ====================

    _SUGGESTION_MAP = {
        RootCause.CONTEXT_LOSS: (
            "上下文丢失导致失败。建议：\n"
            "1. 检查 ContextRewriter 是否正确注入 Plan 状态\n"
            "2. 增加 Scratchpad 中的关键决策记录\n"
            "3. 考虑降低压缩阈值保留更多上下文"
        ),
        RootCause.TOOL_LIMITATION: (
            "工具能力不足。建议：\n"
            "1. 检查是否需要新增工具或技能\n"
            "2. 现有工具的错误处理是否充分\n"
            "3. 工具参数验证是否完善"
        ),
        RootCause.PLAN_DEFICIENCY: (
            "计划不充分导致超时。建议：\n"
            "1. 检查 Plan 步骤是否足够细粒度\n"
            "2. 是否有未预见的依赖关系\n"
            "3. 验证器是否在 Plan 未完成时正确拦截"
        ),
        RootCause.LOOP_DETECTED: (
            "推理陷入循环。建议：\n"
            "1. 检查 Supervisor 的循环检测阈值是否合适\n"
            "2. 回滚策略是否注入了足够的差异化提示\n"
            "3. 是否需要更早期的干预"
        ),
        RootCause.BUDGET_EXHAUSTION: (
            "预算耗尽。建议：\n"
            "1. 评估预算配置是否合理\n"
            "2. 检查是否有 token 浪费（重复读取大文件等）\n"
            "3. 考虑是否需要更便宜的模型降级策略"
        ),
        RootCause.EXTERNAL_FAILURE: (
            "外部依赖失败。建议：\n"
            "1. 添加外部 API 的重试和降级策略\n"
            "2. 检查超时配置是否合理\n"
            "3. 考虑添加缓存机制"
        ),
    }

    def _generate_suggestion(
        self,
        root_cause: RootCause,
        harness_gap: HarnessGap,
        metrics: FailureMetrics,
    ) -> str:
        """生成改进建议"""
        suggestion = self._SUGGESTION_MAP.get(root_cause, "")

        if metrics.tokens_after_last_progress > 50000:
            suggestion += (
                f"\n\n⚠️ 最后一次有效进展后浪费了 {metrics.tokens_after_last_progress} tokens。"
                "考虑更早期的终止或策略切换。"
            )

        return suggestion

    # ==================== 辅助方法 ====================

    def _compute_metrics(
        self,
        react_trace: list[dict],
        budget_summary: dict,
    ) -> FailureMetrics:
        """计算量化指标"""
        metrics = FailureMetrics()
        metrics.total_iterations = len(react_trace)

        for trace in react_trace:
            tokens = trace.get("tokens", {})
            metrics.total_tokens += tokens.get("input", 0) + tokens.get("output", 0)
            metrics.total_tool_calls += len(trace.get("tool_calls", []))

            # 检查错误
            for tr in trace.get("tool_results", []):
                content = str(tr.get("result_content", ""))
                if any(m in content for m in ["❌", "⚠️ 工具执行错误", "错误类型:"]):
                    metrics.error_count += 1

        metrics.elapsed_seconds = budget_summary.get("elapsed_seconds", 0)

        return metrics

    def _count_tool_errors(self, traces: list[dict]) -> int:
        """计算工具错误数"""
        count = 0
        for trace in traces:
            for tr in trace.get("tool_results", []):
                content = str(tr.get("result_content", ""))
                if any(m in content for m in ["❌", "⚠️ 工具执行错误", "错误类型:"]):
                    count += 1
        return count

    def _persist_result(self, result: FailureAnalysisResult) -> None:
        """持久化分析结果"""
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            day_dir = self._output_dir / date_str
            day_dir.mkdir(parents=True, exist_ok=True)

            filename = f"{result.task_id[:12]}_{result.root_cause.value}.json"
            filepath = day_dir / filename

            data = {
                "task_id": result.task_id,
                "timestamp": result.timestamp,
                "root_cause": result.root_cause.value,
                "harness_gap": result.harness_gap.value,
                "metrics": {
                    "total_tokens": result.metrics.total_tokens,
                    "total_iterations": result.metrics.total_iterations,
                    "total_tool_calls": result.metrics.total_tool_calls,
                    "elapsed_seconds": result.metrics.elapsed_seconds,
                    "error_count": result.metrics.error_count,
                },
                "evidence": result.evidence,
                "suggestion": result.suggestion,
                "raw_data": result.raw_data,
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.debug(f"[FailureAnalysis] Persisted result to {filepath}")

        except Exception as e:
            logger.warning(f"[FailureAnalysis] Failed to persist result: {e}")

    def get_recent_results(self, limit: int = 20) -> list[FailureAnalysisResult]:
        """获取最近的分析结果"""
        return list(reversed(self._results[-limit:]))

    def get_stats(self) -> dict[str, Any]:
        """获取统计摘要"""
        if not self._results:
            return {"total": 0}

        cause_counts: dict[str, int] = {}
        gap_counts: dict[str, int] = {}
        total_wasted_tokens = 0

        for r in self._results:
            cause_counts[r.root_cause.value] = cause_counts.get(r.root_cause.value, 0) + 1
            gap_counts[r.harness_gap.value] = gap_counts.get(r.harness_gap.value, 0) + 1
            total_wasted_tokens += r.metrics.total_tokens

        return {
            "total": len(self._results),
            "root_cause_distribution": cause_counts,
            "harness_gap_distribution": gap_counts,
            "total_wasted_tokens": total_wasted_tokens,
        }
