"""
Failure Analysis Pipeline (Agent Harness: Failure Analysis Pipeline)

Leverages DecisionTrace data and task execution records to perform structured analysis
of failed tasks, identifying Harness-level defects and generating improvement suggestions.

Analysis Dimensions:
- Root Cause Classification: context_loss / tool_limitation / plan_deficiency / loop / budget_exhaustion / external_failure
- Harness Gap Identification: missing_tool / insufficient_docs / missing_guardrail / weak_verification / poor_context_engineering
- Quantitative Metrics: tokens_wasted / time_wasted / iterations_before_failure
- Improvement Suggestions: Automatically generated Harness improvement suggestions
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
    """Failure root cause classification"""

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
    """Harness gap type"""

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
    """Quantitative metrics for failed tasks"""

    total_tokens: int = 0
    total_iterations: int = 0
    total_tool_calls: int = 0
    elapsed_seconds: float = 0.0
    tokens_after_last_progress: int = 0  # tokens wasted after the last effective progress
    error_count: int = 0
    loop_count: int = 0


@dataclass
class FailureAnalysisResult:
    """Result of a single failure analysis"""

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
    Failure analyzer.

    Extracts failure signals from data sources such as react_trace,
    supervisor events, and budget status, then classifies and analyzes them.
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
        Analyze a failed task.

        Args:
            task_id: Task ID
            react_trace: ReAct loop trace data
            supervisor_events: RuntimeSupervisor event records
            budget_summary: Budget usage summary
            exit_reason: Exit reason
            task_description: Task description
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

    # ==================== Root Cause Classification ====================

    def _classify_root_cause(
        self,
        react_trace: list[dict],
        supervisor_events: list[dict],
        budget_summary: dict,
        exit_reason: str,
    ) -> RootCause:
        """Classify failure root cause based on multiple signals"""

        if exit_reason == "budget_exceeded":
            return RootCause.BUDGET_EXHAUSTION

        if exit_reason in ("loop_terminated", "loop_detected"):
            return RootCause.LOOP_DETECTED

        if exit_reason == "max_iterations":
            # Check if loop was the cause
            loop_events = [
                e
                for e in supervisor_events
                if e.get("pattern") in ("signature_repeat", "reasoning_loop")
            ]
            if loop_events:
                return RootCause.LOOP_DETECTED
            return RootCause.PLAN_DEFICIENCY

        # Check tool error patterns
        tool_errors = self._count_tool_errors(react_trace)
        if tool_errors > len(react_trace) * 0.5:
            return RootCause.TOOL_LIMITATION

        # Check for external failures
        external_patterns = ["API", "timeout", "connection", "HTTP", "502", "503"]
        for trace in react_trace[-5:]:
            for tr in trace.get("tool_results", []):
                content = str(tr.get("result_content", ""))
                if any(p in content for p in external_patterns):
                    return RootCause.EXTERNAL_FAILURE

        # Check for context loss (disorientation after compression)
        compression_count = sum(1 for t in react_trace if t.get("context_compressed"))
        if compression_count >= 2:
            late_errors = self._count_tool_errors(react_trace[len(react_trace) // 2 :])
            if late_errors > 3:
                return RootCause.CONTEXT_LOSS

        return RootCause.UNKNOWN

    # ==================== Harness Gap Identification ====================

    def _identify_harness_gap(
        self,
        root_cause: RootCause,
        react_trace: list[dict],
        supervisor_events: list[dict],
    ) -> HarnessGap:
        """Identify Harness gaps based on root cause and signals"""

        if root_cause == RootCause.TOOL_LIMITATION:
            return HarnessGap.MISSING_TOOL

        if root_cause == RootCause.LOOP_DETECTED:
            # Check if supervisor intervened in time
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

    # ==================== Evidence Collection ====================

    def _collect_evidence(
        self,
        react_trace: list[dict],
        supervisor_events: list[dict],
        exit_reason: str,
    ) -> list[str]:
        """Collect key evidence"""
        evidence = []

        if exit_reason:
            evidence.append(f"Exit reason: {exit_reason}")

        evidence.append(f"Total iterations: {len(react_trace)}")

        # Tool calls from the last few iterations
        for trace in react_trace[-3:]:
            tools = [tc.get("name", "?") for tc in trace.get("tool_calls", [])]
            if tools:
                evidence.append(f"Iter {trace.get('iteration', '?')}: tools={tools}")

        # Supervisor events
        for event in supervisor_events[-3:]:
            evidence.append(
                f"Supervisor: {event.get('pattern', '?')} level={event.get('level', '?')}"
            )

        return evidence

    # ==================== Improvement Suggestions ====================

    _SUGGESTION_MAP = {
        RootCause.CONTEXT_LOSS: (
            "Context loss caused failure. Suggestions:\n"
            "1. Check if ContextRewriter properly injects Plan status\n"
            "2. Increase key decision logging in the Scratchpad\n"
            "3. Consider lowering compression thresholds to retain more context"
        ),
        RootCause.TOOL_LIMITATION: (
            "Insufficient tool capability. Suggestions:\n"
            "1. Check if new tools or skills are needed\n"
            "2. Verify if current tool error handling is sufficient\n"
            "3. Improve tool parameter validation"
        ),
        RootCause.PLAN_DEFICIENCY: (
            "Insufficient planning led to timeout. Suggestions:\n"
            "1. Check if Plan steps are granular enough\n"
            "2. Look for unforeseen dependencies\n"
            "3. Verify if validators are correctly intercepting incomplete Plans"
        ),
        RootCause.LOOP_DETECTED: (
            "Reasoning stuck in a loop. Suggestions:\n"
            "1. Check if Supervisor loop detection thresholds are appropriate\n"
            "2. Ensure rollback strategies inject enough differentiation hints\n"
            "3. Consider earlier intervention"
        ),
        RootCause.BUDGET_EXHAUSTION: (
            "Budget exhausted. Suggestions:\n"
            "1. Evaluate if budget configurations are reasonable\n"
            "2. Check for token waste (e.g., redundant reading of large files)\n"
            "3. Consider cheaper model fallback strategies"
        ),
        RootCause.EXTERNAL_FAILURE: (
            "External dependency failure. Suggestions:\n"
            "1. Add retry and fallback strategies for external APIs\n"
            "2. Check if timeout configurations are reasonable\n"
            "3. Consider adding a caching mechanism"
        ),
    }

    def _generate_suggestion(
        self,
        root_cause: RootCause,
        harness_gap: HarnessGap,
        metrics: FailureMetrics,
    ) -> str:
        """Generate improvement suggestions"""
        suggestion = self._SUGGESTION_MAP.get(root_cause, "")

        if metrics.tokens_after_last_progress > 50000:
            suggestion += (
                f"\n\n⚠️ Wasted {metrics.tokens_after_last_progress} tokens after the last effective progress. "
                "Consider earlier termination or strategy switching."
            )

        return suggestion

    # ==================== Helper Methods ====================

    def _compute_metrics(
        self,
        react_trace: list[dict],
        budget_summary: dict,
    ) -> FailureMetrics:
        """Compute quantitative metrics"""
        metrics = FailureMetrics()
        metrics.total_iterations = len(react_trace)

        for trace in react_trace:
            tokens = trace.get("tokens", {})
            metrics.total_tokens += tokens.get("input", 0) + tokens.get("output", 0)
            metrics.total_tool_calls += len(trace.get("tool_calls", []))

            # Check for errors
            for tr in trace.get("tool_results", []):
                content = str(tr.get("result_content", ""))
                if any(m in content for m in ["❌", "⚠️ Tool execution error", "Error type:"]):
                    metrics.error_count += 1

        metrics.elapsed_seconds = budget_summary.get("elapsed_seconds", 0)

        return metrics

    def _count_tool_errors(self, traces: list[dict]) -> int:
        """Count tool errors"""
        count = 0
        for trace in traces:
            for tr in trace.get("tool_results", []):
                content = str(tr.get("result_content", ""))
                if any(m in content for m in ["❌", "⚠️ Tool execution error", "Error type:"]):
                    count += 1
        return count

    def _persist_result(self, result: FailureAnalysisResult) -> None:
        """Persist analysis results"""
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
        """Get recent analysis results"""
        return list(reversed(self._results[-limit:]))

    def get_stats(self) -> dict[str, Any]:
        """Get statistics summary"""
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
