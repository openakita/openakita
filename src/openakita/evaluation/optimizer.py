"""
评估反馈优化器

基于评估结果自动驱动系统优化:
1. 记忆反馈: 将失败模式和成功经验写入记忆系统
2. 技能反馈: 触发新技能生成或改进现有技能
3. Prompt 反馈: 调整 Agent 指导原则
4. 工具反馈: 更新工具描述中的警告信息

通过闭环反馈实现 Agent 的持续自我改进。
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
    """优化动作记录"""

    action_type: str  # "memory", "skill", "prompt", "tool"
    description: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    applied: bool = False


class FeedbackAnalyzer:
    """
    反馈分析器。

    分析评估结果，识别改进机会，
    生成具体的优化建议。
    """

    # 阈值配置
    COMPLETION_THRESHOLD = 0.8  # 完成率低于此值触发分析
    TOOL_ACCURACY_THRESHOLD = 0.7  # 工具准确率低于此值触发分析
    JUDGE_SCORE_THRESHOLD = 0.6  # Judge 评分低于此值关注
    LOOP_RATE_THRESHOLD = 0.1  # 循环率高于此值告警

    def analyze(
        self,
        metrics: EvalMetrics,
        results: list[EvalResult],
    ) -> list[OptimizationAction]:
        """
        分析评估结果，生成优化动作。

        Returns:
            优化动作列表
        """
        actions: list[OptimizationAction] = []

        # 1. 任务完成率分析
        if metrics.task_completion_rate < self.COMPLETION_THRESHOLD:
            failure_analysis = self._analyze_failures(results)
            actions.append(
                OptimizationAction(
                    action_type="memory",
                    description=(
                        f"任务完成率 ({metrics.task_completion_rate:.1%}) 低于阈值 "
                        f"({self.COMPLETION_THRESHOLD:.0%})，需要记录失败模式"
                    ),
                    details={
                        "completion_rate": metrics.task_completion_rate,
                        "failure_patterns": failure_analysis,
                    },
                )
            )

        # 2. 工具准确率分析
        if metrics.tool_selection_accuracy < self.TOOL_ACCURACY_THRESHOLD:
            tool_analysis = self._analyze_tool_errors(results)
            actions.append(
                OptimizationAction(
                    action_type="tool",
                    description=(
                        f"工具准确率 ({metrics.tool_selection_accuracy:.1%}) 低于阈值，"
                        f"需要更新工具描述"
                    ),
                    details={
                        "accuracy": metrics.tool_selection_accuracy,
                        "error_tools": tool_analysis,
                    },
                )
            )

        # 3. 循环检测率分析
        if metrics.loop_detection_rate > self.LOOP_RATE_THRESHOLD:
            actions.append(
                OptimizationAction(
                    action_type="prompt",
                    description=(
                        f"循环检测率 ({metrics.loop_detection_rate:.1%}) 过高，需要调整推理指导"
                    ),
                    details={
                        "loop_rate": metrics.loop_detection_rate,
                        "loop_traces": [r.trace_id for r in results if r.metrics.loop_detected],
                    },
                )
            )

        # 4. 效率分析
        if metrics.avg_iterations > 15:
            actions.append(
                OptimizationAction(
                    action_type="prompt",
                    description=(
                        f"平均迭代次数 ({metrics.avg_iterations:.1f}) 过高，需要优化推理效率"
                    ),
                    details={"avg_iterations": metrics.avg_iterations},
                )
            )

        # 5. Judge 建议汇总
        all_suggestions: list[str] = []
        for r in results:
            all_suggestions.extend(r.judge_suggestions)

        if all_suggestions:
            # 去重并取出现频率高的建议
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
                        description="Judge 频繁建议的能力改进",
                        details={"suggestions": frequent},
                    )
                )

        return actions

    def _analyze_failures(self, results: list[EvalResult]) -> list[dict]:
        """分析失败模式。"""
        failed = [r for r in results if not r.metrics.task_completed]
        patterns: list[dict] = []

        # 按标签分组统计
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
        """分析工具错误分布。"""
        tool_error_count: dict[str, int] = {}
        tool_total_count: dict[str, int] = {}

        for r in results:
            for tool in r.metrics.tools_used:
                tool_total_count[tool] = tool_total_count.get(tool, 0) + 1

        # 简化: 按 trace 级别统计有错误的工具
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
    反馈优化器。

    执行 FeedbackAnalyzer 生成的优化动作:
    - 将经验写入记忆系统 (MEMORY.md / memory storage)
    - 更新工具描述
    - 生成改进建议报告
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
        执行优化动作。

        Args:
            actions: 优化动作列表
            dry_run: 如果为 True，只记录不实际执行

        Returns:
            已执行的动作列表
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

        # 保存动作记录
        await self._save_action_log(applied)
        self._applied_actions.extend(applied)

        return applied

    async def _apply_memory_feedback(
        self,
        action: OptimizationAction,
        dry_run: bool = False,
    ) -> None:
        """将失败经验写入记忆文件。"""
        patterns = action.details.get("failure_patterns", [])
        if not patterns:
            return

        memory_entry = [
            "",
            f"## 评估反馈 ({time.strftime('%Y-%m-%d')})",
            "",
            f"任务完成率: {action.details.get('completion_rate', 0):.1%}",
            "",
            "### 失败模式分析",
            "",
        ]
        for p in patterns:
            memory_entry.append(
                f"- **{p['pattern']}**: 出现 {p['count']} 次 (占比 {p['percentage']:.0%})"
            )

        memory_entry.extend(
            [
                "",
                "### 改进方向",
                "",
                "- 针对高频失败模式优化推理策略",
                "- 加强工具错误后的恢复能力",
                "",
            ]
        )

        content = "\n".join(memory_entry)

        if dry_run:
            logger.info(f"[Optimizer][DryRun] Would append to {self._memory_file}:\n{content}")
            return

        # 追加到 MEMORY.md
        os.makedirs(os.path.dirname(self._memory_file), exist_ok=True)
        with open(self._memory_file, "a", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"[Optimizer] Appended memory feedback to {self._memory_file}")

    async def _apply_tool_feedback(
        self,
        action: OptimizationAction,
        dry_run: bool = False,
    ) -> None:
        """记录工具改进建议。"""
        error_tools = action.details.get("error_tools", [])
        if not error_tools:
            return

        # 保存工具反馈报告
        report = {
            "type": "tool_feedback",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "accuracy": action.details.get("accuracy", 0),
            "error_tools": error_tools,
            "recommendations": [f"检查工具 '{t['tool']}' 的错误处理逻辑" for t in error_tools[:5]],
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
        """记录 Prompt 改进建议。"""
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

        # 追加模式 (同一天可能有多次反馈)
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
        """记录技能改进建议。"""
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
        """保存优化动作日志。"""
        if not actions:
            return

        os.makedirs(self._output_dir, exist_ok=True)
        log_path = os.path.join(
            self._output_dir,
            f"optimization_log_{time.strftime('%Y%m%d')}.json",
        )

        # 追加模式
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
    每日自动评估器。

    与自检系统 (selfcheck) 集成，自动运行评估流水线并执行反馈闭环。

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
        """设置 LLM 客户端"""
        self._judge.set_brain(brain)

    async def run_daily_eval(self, dry_run: bool = False) -> dict[str, Any]:
        """
        运行每日评估。

        Returns:
            评估摘要 dict
        """
        logger.info("[DailyEval] Starting daily evaluation...")

        # 1. 运行评估
        metrics, results = await self._runner.run_evaluation()

        if not results:
            logger.info("[DailyEval] No traces to evaluate")
            return {"status": "no_data"}

        # 2. 保存报告
        report_path = await self._reporter.save(metrics, results)

        # 3. 分析改进机会
        actions = self._analyzer.analyze(metrics, results)

        # 4. 执行优化
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
