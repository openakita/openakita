"""
Agent-as-a-Judge Evaluator

Uses LLM as a judge to qualitatively evaluate Agent performance.
Inspired by the "Agent-as-a-Judge" paper.

Evaluation Dimensions:
1. Task Understanding: Did the Agent correctly understand user intent?
2. Tool Usage: Was tool selection and usage reasonable?
3. Efficiency: Were there redundant steps or repetitive operations?
4. Final Quality: Does the final output satisfy user requirements?
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..core.token_tracking import TokenTrackingContext, reset_tracking_context, set_tracking_context

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """You are an AI Agent Evaluation Expert. Please evaluate the following Agent execution record.

## Evaluation Dimensions (Score 0-1 for each)

1. **Task Understanding** (task_understanding): Did the Agent correctly understand user intent?
2. **Tool Usage** (tool_usage): Was tool selection reasonable? Were there unnecessary tool calls?
3. **Execution Efficiency** (efficiency): Were there redundant steps, repetitive operations, or loops?
4. **Final Quality** (output_quality): Does the final output satisfy user requirements?
5. **Error Handling** (error_handling): Was the recovery strategy reasonable when encountering errors?

## Output Format

Please output evaluation results in JSON format:
```json
{
    "scores": {
        "task_understanding": 0.0,
        "tool_usage": 0.0,
        "efficiency": 0.0,
        "output_quality": 0.0,
        "error_handling": 0.0
    },
    "overall_score": 0.0,
    "reasoning": "Brief evaluation explanation",
    "suggestions": ["Improvement suggestion 1", "Improvement suggestion 2"],
    "failure_patterns": ["Identified problem pattern 1"]
}
```

## Agent Execution Record

{trace_summary}
"""


@dataclass
class JudgeResult:
    """Judge 评估结果"""

    trace_id: str
    scores: dict[str, float] = field(default_factory=dict)
    overall_score: float = 0.0
    reasoning: str = ""
    suggestions: list[str] = field(default_factory=list)
    failure_patterns: list[str] = field(default_factory=list)
    raw_response: str = ""

    @classmethod
    def from_llm_response(cls, trace_id: str, response_text: str) -> "JudgeResult":
        """从 LLM 响应解析 JudgeResult。"""
        result = cls(trace_id=trace_id, raw_response=response_text)

        try:
            # 尝试提取 JSON
            text = response_text
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            data = json.loads(text.strip())
            result.scores = data.get("scores", {})
            result.overall_score = data.get("overall_score", 0.0)
            result.reasoning = data.get("reasoning", "")
            result.suggestions = data.get("suggestions", [])
            result.failure_patterns = data.get("failure_patterns", [])

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning(f"[Judge] Failed to parse LLM response: {e}")
            result.reasoning = f"Parsing failed: {response_text[:200]}"

        return result


class Judge:
    """
    Agent-as-a-Judge 评估器。

    使用一个 LLM 实例来评估 Agent 的表现。
    可以使用与 Agent 不同的模型 (通常用更强的模型做评估)。
    """

    def __init__(
        self,
        brain: Any = None,
        model: str | None = None,
    ) -> None:
        self._brain = brain
        self._model = model  # 评估用的模型，None 则使用 brain 默认模型

    def set_brain(self, brain: Any) -> None:
        """设置 LLM 客户端（延迟注入）"""
        self._brain = brain

    async def evaluate(self, trace: Any) -> JudgeResult:
        """
        评估单个 Trace。

        Args:
            trace: Trace 对象 (from tracing.tracer)

        Returns:
            JudgeResult
        """
        if not self._brain:
            logger.warning("[Judge] No brain configured, returning empty result")
            return JudgeResult(trace_id=getattr(trace, "trace_id", ""))

        # 构建 trace 摘要
        trace_summary = self._format_trace_for_judge(trace)

        prompt = JUDGE_PROMPT.format(trace_summary=trace_summary)

        _tt = set_tracking_context(
            TokenTrackingContext(
                operation_type="evaluation",
            )
        )
        try:
            model = self._model or self._brain.model
            response = await asyncio.to_thread(
                self._brain.messages_create,
                model=model,
                max_tokens=2000,
                system="You are an AI Agent Evaluation Expert. Please output evaluation results in strict JSON format.",
                messages=[{"role": "user", "content": prompt}],
            )

            # 提取文本
            text = ""
            for block in getattr(response, "content", []):
                if getattr(block, "type", "") == "text":
                    text += getattr(block, "text", "")

            return JudgeResult.from_llm_response(
                trace_id=getattr(trace, "trace_id", ""),
                response_text=text,
            )

        except Exception as e:
            logger.error(f"[Judge] Evaluation failed: {e}")
            return JudgeResult(
                trace_id=getattr(trace, "trace_id", ""),
                reasoning=f"Evaluation failed: {e}",
            )
        finally:
            reset_tracking_context(_tt)

    async def evaluate_batch(self, traces: list[Any]) -> list[JudgeResult]:
        """批量评估多个 Trace。"""
        results = []
        for trace in traces:
            result = await self.evaluate(trace)
            results.append(result)
        return results

    def _format_trace_for_judge(self, trace: Any) -> str:
        """将 Trace 格式化为 Judge 可读的摘要。"""

        parts = []
        summary = trace.get_summary()

        parts.append(f"Trace ID: {trace.trace_id}")
        parts.append(f"Total Duration: {summary.get('duration_ms', 0):.0f}ms")
        parts.append(f"LLM Calls: {summary.get('llm_calls', 0)}")
        parts.append(f"Tool Calls: {summary.get('tool_calls', 0)}")
        parts.append(f"Tool Errors: {summary.get('tool_errors', 0)}")
        parts.append(f"Total Input Tokens: {summary.get('total_input_tokens', 0)}")
        parts.append(f"Total Output Tokens: {summary.get('total_output_tokens', 0)}")

        if trace.metadata:
            parts.append("\nTask Info:")
            for k, v in trace.metadata.items():
                parts.append(f"  {k}: {v}")

        # Span 详情
        parts.append("\nExecution Timeline:")
        for span in trace.spans[:30]:  # 限制长度
            status_icon = "✅" if span.status.value == "ok" else "❌"
            duration = f"{span.duration_ms:.0f}ms" if span.duration_ms else "?"
            attrs_str = ""
            if span.attributes:
                key_attrs = {
                    k: v
                    for k, v in span.attributes.items()
                    if k in ("model", "tool_name", "error_type", "error_message")
                }
                if key_attrs:
                    attrs_str = f" {key_attrs}"
            parts.append(
                f"  {status_icon} [{span.span_type.value}] {span.name} ({duration}){attrs_str}"
            )

        return "\n".join(parts)
