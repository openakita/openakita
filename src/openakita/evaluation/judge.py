"""
Agent-as-a-Judge 评估器

使用 LLM 作为评判者，对 Agent 的表现进行定性评估。
参考 "Agent-as-a-Judge" 论文的设计思路。

评估维度:
1. 任务理解: Agent 是否正确理解了用户意图
2. 工具使用: 工具选择和使用是否合理
3. 效率: 是否有多余的步骤或重复操作
4. 最终质量: 最终输出是否满足用户需求
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ..core.token_tracking import TokenTrackingContext, set_tracking_context, reset_tracking_context

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """你是一个 AI Agent 评估专家。请根据以下 Agent 执行记录，评估该 Agent 的表现。

## 评估维度 (每项 0-1 分)

1. **任务理解** (task_understanding): Agent 是否正确理解了用户意图？
2. **工具使用** (tool_usage): 工具选择是否合理？是否有不必要的工具调用？
3. **执行效率** (efficiency): 是否有多余的步骤、重复操作或循环？
4. **最终质量** (output_quality): 最终输出是否满足用户需求？
5. **错误处理** (error_handling): 遇到错误时的恢复策略是否合理？

## 输出格式

请以 JSON 格式输出评估结果:
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
    "reasoning": "简要说明评估理由",
    "suggestions": ["改进建议1", "改进建议2"],
    "failure_patterns": ["发现的问题模式1"]
}
```

## Agent 执行记录

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
            result.reasoning = f"解析失败: {response_text[:200]}"

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

        _tt = set_tracking_context(TokenTrackingContext(
            operation_type="evaluation",
        ))
        try:
            model = self._model or self._brain.model
            response = await asyncio.to_thread(
                self._brain.messages_create,
                model=model,
                max_tokens=2000,
                system="你是一个 AI Agent 评估专家，请严格按照 JSON 格式输出评估结果。",
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
                reasoning=f"评估失败: {e}",
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
        parts.append(f"总耗时: {summary.get('duration_ms', 0):.0f}ms")
        parts.append(f"LLM 调用次数: {summary.get('llm_calls', 0)}")
        parts.append(f"工具调用次数: {summary.get('tool_calls', 0)}")
        parts.append(f"工具错误次数: {summary.get('tool_errors', 0)}")
        parts.append(f"总 Input Tokens: {summary.get('total_input_tokens', 0)}")
        parts.append(f"总 Output Tokens: {summary.get('total_output_tokens', 0)}")

        if trace.metadata:
            parts.append("\n任务信息:")
            for k, v in trace.metadata.items():
                parts.append(f"  {k}: {v}")

        # Span 详情
        parts.append("\n执行时间线:")
        for span in trace.spans[:30]:  # 限制长度
            status_icon = "✅" if span.status.value == "ok" else "❌"
            duration = f"{span.duration_ms:.0f}ms" if span.duration_ms else "?"
            attrs_str = ""
            if span.attributes:
                key_attrs = {k: v for k, v in span.attributes.items()
                           if k in ("model", "tool_name", "error_type", "error_message")}
                if key_attrs:
                    attrs_str = f" {key_attrs}"
            parts.append(
                f"  {status_icon} [{span.span_type.value}] {span.name} "
                f"({duration}){attrs_str}"
            )

        return "\n".join(parts)
