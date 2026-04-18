"""
Evaluation report generator.

Formats evaluation results into multiple output formats:
- JSON: machine-readable full report
- Text: human-readable summary report
- Markdown: used by the selfcheck command for display
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .metrics import EvalMetrics, EvalResult

logger = logging.getLogger(__name__)


class Reporter:
    """Evaluation report generator."""

    def __init__(self, output_dir: str = "data/evaluation") -> None:
        self._output_dir = output_dir

    async def save(
        self,
        metrics: EvalMetrics,
        results: list[EvalResult],
        *,
        report_name: str | None = None,
    ) -> str:
        """
        Save the evaluation report.

        Args:
            metrics: aggregated metrics
            results: per-trace evaluation results
            report_name: custom report name

        Returns:
            path to the report file
        """
        os.makedirs(self._output_dir, exist_ok=True)

        if report_name is None:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            report_name = f"eval_{timestamp}"

        # Save JSON report
        json_path = os.path.join(self._output_dir, f"{report_name}.json")
        report_data = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "metrics": metrics.to_dict(),
            "results": [
                {
                    "trace_id": r.trace_id,
                    "judge_score": r.judge_score,
                    "judge_reasoning": r.judge_reasoning,
                    "judge_suggestions": r.judge_suggestions,
                    "tags": r.tags,
                    "task_completed": r.metrics.task_completed,
                    "iterations": r.metrics.total_iterations,
                    "tool_calls": r.metrics.total_tool_calls,
                    "tool_errors": r.metrics.tool_errors,
                    "duration_ms": r.metrics.total_duration_ms,
                    "total_tokens": r.metrics.total_input_tokens + r.metrics.total_output_tokens,
                }
                for r in results
            ],
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        # Save Markdown report
        md_path = os.path.join(self._output_dir, f"{report_name}.md")
        md_content = self._generate_markdown(metrics, results)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        logger.info(f"[Reporter] Saved evaluation report: {json_path}")
        return json_path

    def _generate_markdown(
        self,
        metrics: EvalMetrics,
        results: list[EvalResult],
    ) -> str:
        """Generate a Markdown-formatted report."""
        lines = [
            "# Agent Evaluation Report",
            "",
            f"**Generated at**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Evaluated Traces**: {metrics.total_traces}",
            "",
            "## Key Metrics",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Task Completion Rate | {metrics.task_completion_rate:.1%} |",
            f"| Tool Error-Free Rate | {metrics.tool_selection_accuracy:.1%} |",
            f"| Average Judge Score | {metrics.avg_judge_score:.2f}/1.0 |",
            f"| Average Iterations | {metrics.avg_iterations:.1f} |",
            f"| Average Tokens | {metrics.avg_token_usage:,} |",
            f"| Average Latency | {metrics.avg_latency_ms:.0f}ms |",
            "",
            "## Anomaly Metrics",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Loop Detection Rate | {metrics.loop_detection_rate:.1%} |",
            f"| Error Recovery Rate | {metrics.error_recovery_rate:.1%} |",
            f"| Rollback Trigger Rate | {metrics.rollback_rate:.1%} |",
            "",
        ]

        # Failed case analysis
        failed = [r for r in results if not r.is_good()]
        if failed:
            lines.append(f"## Cases Requiring Attention ({len(failed)})")
            lines.append("")
            for r in failed[:10]:
                tags_str = ", ".join(r.tags) if r.tags else "No tags"
                lines.append(f"### Trace: {r.trace_id}")
                lines.append(f"- **Tags**: {tags_str}")
                lines.append(f"- **Judge Score**: {r.judge_score:.2f}")
                lines.append(f"- **Reason**: {r.judge_reasoning[:200]}")
                if r.judge_suggestions:
                    lines.append("- **Suggestions**:")
                    for s in r.judge_suggestions[:3]:
                        lines.append(f"  - {s}")
                lines.append("")

        # Improvement suggestions summary
        all_suggestions: list[str] = []
        for r in results:
            all_suggestions.extend(r.judge_suggestions)

        if all_suggestions:
            # Simple deduplication
            unique = list(dict.fromkeys(all_suggestions))
            lines.append("## Improvement Suggestions")
            lines.append("")
            for i, s in enumerate(unique[:10], 1):
                lines.append(f"{i}. {s}")
            lines.append("")

        return "\n".join(lines)

    async def load_latest(self) -> dict[str, Any] | None:
        """Load the latest evaluation report."""
        output_path = Path(self._output_dir)
        if not output_path.exists():
            return None

        json_files = sorted(output_path.glob("eval_*.json"), reverse=True)
        if not json_files:
            return None

        try:
            with open(json_files[0], encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[Reporter] Failed to load report: {e}")
            return None
