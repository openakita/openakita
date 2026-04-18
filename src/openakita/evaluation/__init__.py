"""
OpenAkita Agent Evaluation Framework

Provides comprehensive agent performance evaluation:
- EvalMetrics: Metric definitions and aggregation
- Judge: LLM-based quality assessment (Agent-as-a-Judge)
- Runner: Evaluation task runner
- Reporter: Evaluation report generation
- Optimizer: Evaluation-feedback-driven automatic optimization

Evaluation dimensions:
- Task completion rate
- Tool selection accuracy
- Average iterations / token consumption / latency
- Loop detection rate
- Error recovery rate
- Memory retrieval relevance
"""

from .judge import Judge, JudgeResult
from .metrics import EvalMetrics, EvalResult, TraceMetrics
from .optimizer import DailyEvaluator, FeedbackAnalyzer, FeedbackOptimizer, OptimizationAction
from .reporter import Reporter
from .runner import EvalRunner

__all__ = [
    "EvalMetrics",
    "EvalResult",
    "TraceMetrics",
    "Judge",
    "JudgeResult",
    "EvalRunner",
    "Reporter",
    "FeedbackAnalyzer",
    "FeedbackOptimizer",
    "OptimizationAction",
    "DailyEvaluator",
]
