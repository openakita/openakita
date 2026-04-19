"""
Evaluation Feedback Optimizer

Automatically drive system optimization based on evaluation results:
1. Memory feedback: Write failure patterns and success experiences to memory system
2. Skill feedback: Trigger new skill generation or improve existing skills
3. Prompt feedback: Adjust Agent guidance principles
4. Tool feedback: Update warning information in tool descriptions

Achieve continuous self-improvement of Agent through closed-loop feedback.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .metrics import EvalMetrics, EvalResult

logger = logging.getLogger(__name__)


@dataclass
class OptimizationAction:
    """Optimization action record"""

    action_type: str  # "memory", "skill", "prompt", "tool"
    description: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    applied: bool = False


class FeedbackAnalyzer:
    """
    Feedback analyzer.

    Analyze evaluation results, identify improvement opportunities,
    and generate concrete optimization recommendations.
    """

    # Threshold configuration
    COMPLETION_THRESHOLD = 0.8  # Trigger analysis if completion rate below this value
    TOOL_ACCURACY_THRESHOLD = 0.7  # Trigger analysis if tool accuracy below this value
    JUDGE_SCORE_THRESHOLD = 0.6  # Focus if Judge score below this value
    LOOP_RATE_THRESHOLD = 0.1  # Alert if loop rate above this value

    def analyze(
        self,
        metrics: EvalMetrics,
        results: list[EvalResult],
    ) -> list[OptimizationAction]:
        """
        Analyze evaluation results and generate optimization actions.

        Returns:
            List of optimization actions
        """
        actions: list[OptimizationAction] = []

        # 1. Task completion rate analysis
        if metrics.task_completion_rate < self.COMPLETION_THRESHOLD:
            failure_analysis = self._analyze_failures(results)
            actions.append(
                OptimizationAction(
                    action_type="memory",
                    description=(
                        f"Task completion rate ({metrics.task_completion_rate:.1%}) below threshold "
                        f"({self.COMPLETION_THRESHOLD:.0%}), need to record failure patterns"
                    ),
                    details={
                        "completion_rate": metrics.task_completion_rate,
                        "failure_patterns": failure_analysis,
                    },
                )
            )

        # 2. Tool accuracy analysis
        if metrics.tool_selection_accuracy < self.TOOL_ACCURACY_THRESHOLD:
            tool_analysis = self._analyze_tool_errors(results)
            actions.append(
                OptimizationAction(
                    action_type="tool",
                    description=(
                        f"Tool accuracy ({metrics.tool_selection_accuracy:.1%}) below threshold, "
                        f"need to update tool descriptions"
                    ),
                    details={
                        "accuracy": metrics.tool_selection_accuracy,
                        "error_tools": tool_analysis,
                    },
                )
            )

        # 3. Loop detection rate analysis
        if metrics.loop_detection_rate > self.LOOP_RATE_THRESHOLD:
            actions.append(
                OptimizationAction(
                    action_type="prompt",
                    description=(
                        f"Loop detection rate ({metrics.loop_detection_rate:.1%}) too high, need to adjust reasoning guidance"
                    ),
                    details={
                        "loop_rate": metrics.loop_detection_rate,
                        "loop_traces": [r.trace_id for r in results if r.metrics.loop_detected],
                    },
                )
            )

        # 4. Efficiency analysis
        if metrics.avg_iterations > 15:
            actions.append(
                OptimizationAction(
                    action_type="prompt",
                    description=(
                        f"Average iterations ({metrics.avg_iterations:.1f}) too high, need to optimize reasoning efficiency"
                    ),
                    details={"avg_iterations": metrics.avg_iterations},
                )
            )

        # 5. Judge suggestions summary
        all_suggestions: list[str] = []
        for r in results:
            all_suggestions.extend(r.judge_suggestions)

        if all_suggestions:
            # Deduplicate and get frequently suggested suggestions
            suggestion_count: dict[str, int] = {}
            for s in all_suggestions:
                suggestion_count[s] = suggestion_count.get(s, 0) + 1

            frequent = [
                s
                for s, c in sorted(suggestion_count.items(), key=lambda x: x[1], reverse=True)
                if c >= 2
            ][:5]

            if frequent:
                actions.append(
                    OptimizationAction(
                        action_type="skill",
                        description="Frequently suggested ability improvements by Judge",
                        details={"suggestions": frequent},
                    )
                )

        return actions

    def _analyze_failures(self, results: list[EvalResult]) -> list[dict]:
        """Analyze failure patterns."""
        failed = [r for r in results if not r.metrics.task_completed]
        patterns: list[dict] = []

        # Group and count by tags
        tag_counts: dict[str, int] = {}
        for r in failed:
            for tag in r.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        for tag, count in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True):
            patterns.append(
                {
                    "pattern": tag,
                    "count": count,
                    "percentage": count / max(len(failed), 1),
                }
            )

        return patterns

    def _analyze_tool_errors(self, results: list[EvalResult]) -> list[dict]:
        """Analyze tool error distribution."""
        tool_error_count: dict[str, int] = {}
        tool_total_count: dict[str, int] = {}

        for r in results:
            for tool in r.metrics.tools_used:
                tool_total_count[tool] = tool_total_count.get(tool, 0) + 1

        # Simplified: count tools with errors at trace level
        for r in results:
            if r.metrics.tool_errors > 0:
                for tool in set(r.metrics.tools_used):
                    tool_error_count[tool] = tool_error_count.get(tool, 0) + 1

        error_tools = []
        for tool, errors in sorted(tool_error_count.items(), key=lambda x: x[1], reverse=True):
            total = tool_total_count.get(tool, errors)
            error_tools.append(
                {
                    "tool": tool,
                    "error_traces": errors,
                    "total_traces": total,
                }
            )

        return error_tools[:10]


class FeedbackOptimizer:
    """
    Feedback optimizer.

    Execute optimization actions generated by FeedbackAnalyzer:
    - Write experiences to memory system (MEMORY.md / memory storage)
    - Update tool descriptions
    - Generate improvement recommendation report
    """

    def __init__(
        self,
        memory_file: str = "data/identity/MEMORY.md",
        output_dir: str = "data/evaluation",
    ) -> None:
        self._memory_file = memory_file
        self._output_dir = output_dir
        self._applied_actions: list[OptimizationAction] = []

    async def apply_actions(
        self,
        actions: list[OptimizationAction],
        *,
        dry_run: bool = False,
    ) -> list[OptimizationAction]:
        """
        Execute optimization actions.

        Args:
            actions: List of optimization actions
            dry_run: If True, only log without actual execution

        Returns:
            List of executed actions
        """
        applied = []

        for action in actions:
            try:
                if action.action_type == "memory":
                    await self._apply_memory_feedback(action, dry_run=dry_run)
                elif action.action_type == "tool":
                    await self._apply_tool_feedback(action, dry_run=dry_run)
                elif action.action_type == "prompt":
                    await self._apply_prompt_feedback(action, dry_run=dry_run)
                elif action.action_type == "skill":
                    await self._apply_skill_feedback(action, dry_run=dry_run)

                action.applied = not dry_run
                applied.append(action)

            except Exception as e:
                logger.error(f"[Optimizer] Failed to apply action '{action.action_type}': {e}")

        # Save action log
        await self._save_action_log(applied)
        self._applied_actions.extend(applied)

        return applied

    async def _apply_memory_feedback(
        self,
        action: OptimizationAction,
        dry_run: bool = False,
    ) -> None:
        """Write failure experiences to memory file."""
        patterns = action.details.get("failure_patterns", [])
        if not patterns:
            return

        memory_entry = [
            "",
            f"## Evaluation Feedback ({time.strftime('%Y-%m-%d')})",
            "",
            f"Task completion rate: {action.details.get('completion_rate', 0):.1%}",
            "",
            "### Failure Pattern Analysis",
            "",
        ]
        for p in patterns:
            memory_entry.append(
                f"- **{p['pattern']}**: occurred {p['count']} times (proportion {p['percentage']:.0%})"
            )

        memory_entry.extend(
            [
                "",
                "### Improvement Direction",
                "",
                "- Optimize reasoning strategy for high-frequency failure patterns",
                "- Strengthen recovery ability after tool errors",
                "",
            ]
        )

        content = "\n".join(memory_entry)

        if dry_run:
            logger.info(f"[Optimizer][DryRun] Would append to {self._memory_file}:\n{content}")
            return

        # Append to MEMORY.md
        os.makedirs(os.path.dirname(self._memory_file), exist_ok=True)
        with open(self._memory_file, "a", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"[Optimizer] Appended memory feedback to {self._memory_file}")

    async def _apply_tool_feedback(
        self,
        action: OptimizationAction,
        dry_run: bool = False,
    ) -> None:
        """Record tool improvement suggestions."""
        error_tools = action.details.get("error_tools", [])
        if not error_tools:
            return

        # Save tool feedback report
        report = {
            "type": "tool_feedback",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "accuracy": action.details.get("accuracy", 0),
            "error_tools": error_tools,
            "recommendations": [f"Check error handling logic of tool '{t['tool']}'" for t in error_tools[:5]],
        }

        if dry_run:
            logger.info(
                f"[Optimizer][DryRun] Tool feedback: {json.dumps(report, ensure_ascii=False)}"
            )
            return

        os.makedirs(self._output_dir, exist_ok=True)
        feedback_path = os.path.join(
            self._output_dir,
            f"tool_feedback_{time.strftime('%Y%m%d')}.json",
        )
        with open(feedback_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"[Optimizer] Saved tool feedback to {feedback_path}")

    async def _apply_prompt_feedback(
        self,
        action: OptimizationAction,
        dry_run: bool = False,
    ) -> None:
        """Record Prompt improvement suggestions."""
        report = {
            "type": "prompt_feedback",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "description": action.description,
            "details": action.details,
        }

        if dry_run:
            logger.info(f"[Optimizer][DryRun] Prompt feedback: {action.description}")
            return

        os.makedirs(self._output_dir, exist_ok=True)
        feedback_path = os.path.join(
            self._output_dir,
            f"prompt_feedback_{time.strftime('%Y%m%d')}.json",
        )

        # Append mode (multiple feedbacks may occur in one day)
        existing = []
        if os.path.exists(feedback_path):
            try:
                with open(feedback_path, encoding="utf-8") as f:
                    existing = json.load(f)
                    if not isinstance(existing, list):
                        existing = [existing]
            except Exception:
                pass

        existing.append(report)
        with open(feedback_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        logger.info(f"[Optimizer] Saved prompt feedback to {feedback_path}")

    async def _apply_skill_feedback(
        self,
        action: OptimizationAction,
        dry_run: bool = False,
    ) -> None:
        """Record skill improvement suggestions."""
        suggestions = action.details.get("suggestions", [])
        if not suggestions:
            return

        report = {
            "type": "skill_feedback",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "suggestions": suggestions,
        }

        if dry_run:
            logger.info(f"[Optimizer][DryRun] Skill feedback: {suggestions}")
            return

        os.makedirs(self._output_dir, exist_ok=True)
        feedback_path = os.path.join(
            self._output_dir,
            f"skill_feedback_{time.strftime('%Y%m%d')}.json",
        )
        with open(feedback_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"[Optimizer] Saved skill feedback to {feedback_path}")

    async def _save_action_log(self, actions: list[OptimizationAction]) -> None:
        """Save optimization action log."""
        if not actions:
            return

        os.makedirs(self._output_dir, exist_ok=True)
        log_path = os.path.join(
            self._output_dir,
            f"optimization_log_{time.strftime('%Y%m%d')}.json",
        )

        # Append mode
        existing: list[dict] = []
        if os.path.exists(log_path):
            try:
                with open(log_path, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass

        for action in actions:
            existing.append(
                {
                    "action_type": action.action_type,
                    "description": action.description,
                    "applied": action.applied,
                    "timestamp": action.timestamp,
                }
            )

        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)


class DailyEvaluator:
    """
    Daily auto-evaluator.

    Integrate with self-check system (selfcheck), automatically run evaluation pipeline and execute feedback loop.

    Usage:
        evaluator = DailyEvaluator(brain=brain)
        await evaluator.run_daily_eval()
    """

    def __init__(
        self,
        brain: Any = None,
        traces_dir: str = "data/traces",
        output_dir: str = "data/evaluation",
        memory_file: str = "data/identity/MEMORY.md",
    ) -> None:
        from .judge import Judge
        from .reporter import Reporter
        from .runner import EvalRunner

        self._judge = Judge(brain=brain)
        self._runner = EvalRunner(traces_dir=traces_dir, judge=self._judge)
        self._reporter = Reporter(output_dir=output_dir)
        self._analyzer = FeedbackAnalyzer()
        self._optimizer = FeedbackOptimizer(
            memory_file=memory_file,
            output_dir=output_dir,
        )

    def set_brain(self, brain: Any) -> None:
        """Set LLM client"""
        self._judge.set_brain(brain)

    async def run_daily_eval(self, dry_run: bool = False) -> dict[str, Any]:
        """
        Run daily evaluation.

        Returns:
            Evaluation summary dict
        """
        logger.info("[DailyEval] Starting daily evaluation...")

        # 1. Run evaluation
        metrics, results = await self._runner.run_evaluation()

        if not results:
            logger.info("[DailyEval] No traces to evaluate")
            return {"status": "no_data"}

        # 2. Save report
        report_path = await self._reporter.save(metrics, results)

        # 3. Analyze improvement opportunities
        actions = self._analyzer.analyze(metrics, results)

        # 4. Execute optimization
        applied = await self._optimizer.apply_actions(actions, dry_run=dry_run)

        summary = {
            "status": "completed",
            "traces_evaluated": len(results),
            "metrics": metrics.to_dict(),
            "optimization_actions": len(applied),
            "report_path": report_path,
        }

        logger.info(
            f"[DailyEval] Complete: {len(results)} traces, {len(applied)} optimizations applied"
        )

        return summary
