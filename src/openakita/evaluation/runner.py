"""
Evaluation Runner

Loads trace data from the tracing system and runs the evaluation pipeline:
Trace -> TraceMetrics -> Judge -> EvalResult -> EvalMetrics
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

from .judge import Judge
from .metrics import EvalMetrics, EvalResult, TraceMetrics

logger = logging.getLogger(__name__)


class EvalRunner:
    """
    Evaluation runner.

    Loads traces from files exported by the tracing system
    and generates evaluation results using a Judge.
    """

    def __init__(
        self,
        traces_dir: str = "data/traces",
        judge: Judge | None = None,
    ) -> None:
        self._traces_dir = traces_dir
        self._judge = judge

    def set_judge(self, judge: Judge) -> None:
        """Set the Judge (lazy injection)."""
        self._judge = judge

    async def run_evaluation(
        self,
        *,
        since: float | None = None,
        max_traces: int = 50,
    ) -> tuple[EvalMetrics, list[EvalResult]]:
        """
        Run the full evaluation pipeline.

        Args:
            since: Only evaluate traces after this timestamp (default: past 24 hours)
            max_traces: Maximum number of traces to evaluate

        Returns:
            (aggregated metrics, list of per-trace evaluation results)
        """
        if since is None:
            since = time.time() - 86400  # past 24 hours

        # Step 1: Load traces
        traces = self._load_traces(since=since, max_traces=max_traces)
        if not traces:
            logger.info("[Eval] No traces found for evaluation")
            return EvalMetrics(), []

        logger.info(f"[Eval] Loaded {len(traces)} traces for evaluation")

        # Step 2: Extract metrics
        results: list[EvalResult] = []
        for trace in traces:
            metrics = TraceMetrics.from_trace(trace)

            # Step 3: Judge evaluation (if configured)
            judge_score = 0.0
            judge_reasoning = ""
            judge_suggestions: list[str] = []
            tags: list[str] = []

            if self._judge:
                judge_result = await self._judge.evaluate(trace)
                judge_score = judge_result.overall_score
                judge_reasoning = judge_result.reasoning
                judge_suggestions = judge_result.suggestions

                # Auto-tag based on metrics
                if not metrics.task_completed:
                    tags.append("failed")
                if metrics.loop_detected:
                    tags.append("loop")
                if metrics.total_duration_ms > 60000:
                    tags.append("slow")
                if metrics.tool_errors > 3:
                    tags.append("tool_errors")
                if metrics.rollback_count > 0:
                    tags.append("rollback")
                if judge_score < 0.5:
                    tags.append("low_quality")
            else:
                # Without a Judge, score simply based on metrics
                judge_score = 1.0 if metrics.task_completed else 0.0
                if not metrics.task_completed:
                    tags.append("failed")
                if metrics.loop_detected:
                    tags.append("loop")

            result = EvalResult(
                trace_id=metrics.trace_id,
                metrics=metrics,
                judge_score=judge_score,
                judge_reasoning=judge_reasoning,
                judge_suggestions=judge_suggestions,
                tags=tags,
            )
            results.append(result)

        # Step 4: Aggregate metrics
        aggregated = EvalMetrics.aggregate(results)

        logger.info(
            f"[Eval] Evaluation complete: {aggregated.total_traces} traces, "
            f"completion={aggregated.task_completion_rate:.1%}, "
            f"judge_avg={aggregated.avg_judge_score:.2f}"
        )

        return aggregated, results

    def _load_traces(
        self,
        *,
        since: float = 0.0,
        max_traces: int = 50,
    ) -> list[Any]:
        """Load trace objects from files."""
        from ..tracing.tracer import Span, SpanStatus, SpanType, Trace

        traces_path = Path(self._traces_dir)
        if not traces_path.exists():
            return []

        loaded = []
        for file_path in sorted(traces_path.glob("*.json"), reverse=True):
            if len(loaded) >= max_traces:
                break

            try:
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)

                # Support both single-trace and multi-trace files
                trace_dicts = data if isinstance(data, list) else [data]

                for td in trace_dicts:
                    start_time = td.get("start_time", 0)
                    if start_time < since:
                        continue

                    # Reconstruct Trace object
                    trace = Trace(
                        trace_id=td.get("trace_id", ""),
                        session_id=td.get("session_id", ""),
                        start_time=start_time,
                        end_time=td.get("end_time"),
                        metadata=td.get("metadata", {}),
                    )

                    # Reconstruct Span object
                    for sd in td.get("spans", []):
                        span_type_str = sd.get("type", "tool")
                        try:
                            span_type = SpanType(span_type_str)
                        except ValueError:
                            span_type = SpanType.TOOL

                        status_str = sd.get("status", "ok")
                        try:
                            status = SpanStatus(status_str)
                        except ValueError:
                            status = SpanStatus.OK

                        span = Span(
                            span_id=sd.get("span_id", ""),
                            name=sd.get("name", ""),
                            span_type=span_type,
                            start_time=sd.get("start_time", 0),
                            parent_id=sd.get("parent_id"),
                            end_time=sd.get("end_time"),
                            status=status,
                            attributes=sd.get("attributes", {}),
                            error_message=sd.get("error"),
                        )
                        trace.add_span(span)

                    loaded.append(trace)

            except Exception as e:
                logger.warning(f"[Eval] Failed to load trace from {file_path}: {e}")

        return loaded[:max_traces]
