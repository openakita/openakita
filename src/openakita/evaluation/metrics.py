"""
Evaluation Metrics Definition

Defines various metrics and aggregation logic for Agent performance evaluation.
Extracts quantitative metrics from Trace data in the Tracing system.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TraceMetrics:
    """
    Metrics extracted from a single Trace.

    Quantitative data extracted from a single Trace (one complete user request).
    """

    trace_id: str
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)

    # Basic Metrics
    total_iterations: int = 0
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_duration_ms: float = 0.0

    # Quality Metrics
    task_completed: bool = False  # Whether the task is completed (by state machine)
    tool_errors: int = 0  # Number of tool call failures
    loop_detected: bool = False  # Whether loop detection was triggered
    rollback_count: int = 0  # Number of rollbacks
    context_compressions: int = 0  # Number of context compressions

    # Tool usage
    tools_used: list[str] = field(default_factory=list)
    unique_tools: int = 0

    @classmethod
    def from_trace(cls, trace: Any) -> "TraceMetrics":
        """Extract metrics from a Trace object."""
        from ..tracing.tracer import SpanStatus, SpanType

        metrics = cls(
            trace_id=trace.trace_id,
            session_id=trace.session_id,
            total_duration_ms=trace.duration_ms or 0.0,
        )

        for span in trace.spans:
            if span.span_type == SpanType.LLM:
                metrics.total_llm_calls += 1
                metrics.total_input_tokens += span.attributes.get("input_tokens", 0)
                metrics.total_output_tokens += span.attributes.get("output_tokens", 0)

            elif span.span_type == SpanType.TOOL:
                metrics.total_tool_calls += 1
                tool_name = span.attributes.get("tool_name", "")
                if tool_name:
                    metrics.tools_used.append(tool_name)
                if span.status == SpanStatus.ERROR:
                    metrics.tool_errors += 1

            elif span.span_type == SpanType.CONTEXT:
                metrics.context_compressions += 1

            elif span.span_type == SpanType.REASONING:
                metrics.total_iterations += 1

        metrics.unique_tools = len(set(metrics.tools_used))

        # Extract completion info from trace metadata
        metadata = trace.metadata or {}
        result = metadata.get("result", "")
        metrics.task_completed = result in ("completed", "completed_end_turn")
        metrics.loop_detected = result == "loop_terminated"
        metrics.rollback_count = metadata.get("rollback_count", 0)

        return metrics


@dataclass
class EvalResult:
    """
    Result of a single evaluation.

    Contains quantitative metrics + qualitative evaluation from LLM Judge.
    """

    trace_id: str
    metrics: TraceMetrics
    judge_score: float = 0.0  # 0-1, scored by Judge
    judge_reasoning: str = ""
    judge_suggestions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # Tags: "failed", "slow", "loop", etc.

    def is_good(self) -> bool:
        """Whether the evaluation passed"""
        return self.metrics.task_completed and self.judge_score >= 0.7


@dataclass
class EvalMetrics:
    """
    Aggregated evaluation metrics.

    Overall performance metrics aggregated from multiple EvalResults.
    """

    # Count
    total_traces: int = 0
    period_start: float = 0.0
    period_end: float = 0.0

    # Completion Rate
    task_completion_rate: float = 0.0  # Task completion rate

    # Tool Related
    tool_selection_accuracy: float = 0.0  # Tool accuracy (trace without errors / total trace)
    avg_tool_calls_per_task: float = 0.0
    most_errored_tools: list[tuple[str, int]] = field(default_factory=list)

    # Efficiency Metrics
    avg_iterations: float = 0.0
    avg_token_usage: int = 0  # Average total tokens
    avg_latency_ms: float = 0.0

    # Anomaly Detection
    loop_detection_rate: float = 0.0  # Proportion that triggered loop detection
    error_recovery_rate: float = 0.0  # Proportion with errors that ultimately completed
    rollback_rate: float = 0.0  # Proportion that triggered rollback

    # Judge Score
    avg_judge_score: float = 0.0

    @classmethod
    def aggregate(cls, results: list[EvalResult]) -> "EvalMetrics":
        """Aggregate metrics from a list of evaluation results."""
        if not results:
            return cls()

        total = len(results)
        now = time.time()

        # Completion rate
        completed = sum(1 for r in results if r.metrics.task_completed)

        # Tool accuracy: proportion of traces without tool errors
        no_tool_errors = sum(1 for r in results if r.metrics.tool_errors == 0)

        # Loop detection rate
        loops = sum(1 for r in results if r.metrics.loop_detected)

        # Error recovery rate
        had_errors = [r for r in results if r.metrics.tool_errors > 0]
        recovered = sum(1 for r in had_errors if r.metrics.task_completed)

        # Rollback rate
        rollbacks = sum(1 for r in results if r.metrics.rollback_count > 0)

        metrics = cls(
            total_traces=total,
            period_start=min(r.metrics.timestamp for r in results),
            period_end=now,
            task_completion_rate=completed / total,
            tool_selection_accuracy=no_tool_errors / total,
            avg_tool_calls_per_task=(sum(r.metrics.total_tool_calls for r in results) / total),
            avg_iterations=sum(r.metrics.total_iterations for r in results) / total,
            avg_token_usage=int(
                sum(r.metrics.total_input_tokens + r.metrics.total_output_tokens for r in results)
                / total
            ),
            avg_latency_ms=sum(r.metrics.total_duration_ms for r in results) / total,
            loop_detection_rate=loops / total,
            error_recovery_rate=(recovered / len(had_errors)) if had_errors else 1.0,
            rollback_rate=rollbacks / total,
            avg_judge_score=(sum(r.judge_score for r in results) / total),
        )

        return metrics

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary"""
        return {
            "total_traces": self.total_traces,
            "task_completion_rate": round(self.task_completion_rate, 3),
            "tool_selection_accuracy": round(self.tool_selection_accuracy, 3),
            "avg_tool_calls_per_task": round(self.avg_tool_calls_per_task, 1),
            "avg_iterations": round(self.avg_iterations, 1),
            "avg_token_usage": self.avg_token_usage,
            "avg_latency_ms": round(self.avg_latency_ms, 0),
            "loop_detection_rate": round(self.loop_detection_rate, 3),
            "error_recovery_rate": round(self.error_recovery_rate, 3),
            "rollback_rate": round(self.rollback_rate, 3),
            "avg_judge_score": round(self.avg_judge_score, 3),
        }

    def format_report(self) -> str:
        """Format as a readable report"""
        lines = [
            "=" * 50,
            "OpenAkita Agent Evaluation Report",
            "=" * 50,
            f"Traces Evaluated: {self.total_traces}",
            "",
            "📊 Core Metrics:",
            f"  Completion Rate:     {self.task_completion_rate:.1%}",
            f"  Tool Accuracy Rate:   {self.tool_selection_accuracy:.1%}",
            f"  Avg Judge Score:      {self.avg_judge_score:.2f}/1.0",
            "",
            "⚡ Efficiency Metrics:",
            f"  Avg Iterations:      {self.avg_iterations:.1f}",
            f"  Avg Token Usage:     {self.avg_token_usage:,}",
            f"  Avg Latency:         {self.avg_latency_ms:.0f}ms",
            f"  Avg Tool Calls:      {self.avg_tool_calls_per_task:.1f}",
            "",
            "🔍 Anomaly Metrics:",
            f"  Loop Detection Rate: {self.loop_detection_rate:.1%}",
            f"  Error Recovery Rate: {self.error_recovery_rate:.1%}",
            f"  Rollback Rate:       {self.rollback_rate:.1%}",
            "=" * 50,
        ]
        return "\n".join(lines)
