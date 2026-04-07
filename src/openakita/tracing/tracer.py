"""
核心追踪器

提供 Trace / Span 数据模型和 AgentTracer 追踪管理器。
"""

import logging
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SpanType(Enum):
    """Span 类型"""

    LLM = "llm"  # LLM 调用
    TOOL = "tool"  # 工具执行
    TOOL_BATCH = "tool_batch"  # 工具批量执行
    MEMORY = "memory"  # 记忆操作
    CONTEXT = "context"  # 上下文管理（压缩等）
    REASONING = "reasoning"  # 推理循环
    PROMPT = "prompt"  # 提示词构建
    TASK = "task"  # 完整任务
    # Agent Harness: 决策追踪扩展
    DECISION = "decision"  # 决策节点（工具选择/策略选择）
    VERIFICATION = "verification"  # 验证节点（任务完成验证/Plan 步骤验证）
    SUPERVISION = "supervision"  # 监督节点（循环检测/干预决策）
    DELEGATION = "delegation"  # 委派节点（多 Agent 委派）


class SpanStatus(Enum):
    """Span 状态"""

    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class Span:
    """
    单个操作的追踪记录。

    一个 Span 代表一次 LLM 调用、一次工具执行、一次记忆检索等。
    Span 可以嵌套形成父子关系。
    """

    span_id: str
    name: str
    span_type: SpanType
    start_time: float
    parent_id: str | None = None
    end_time: float | None = None
    status: SpanStatus = SpanStatus.OK
    attributes: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None

    @property
    def duration_ms(self) -> float | None:
        """耗时（毫秒）"""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    def set_attribute(self, key: str, value: Any) -> None:
        """设置属性"""
        self.attributes[key] = value

    def set_error(self, message: str) -> None:
        """标记为错误"""
        self.status = SpanStatus.ERROR
        self.error_message = message

    def finish(self, status: SpanStatus | None = None) -> None:
        """结束 Span"""
        self.end_time = time.time()
        if status is not None:
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        result = {
            "span_id": self.span_id,
            "name": self.name,
            "type": self.span_type.value,
            "start_time": self.start_time,
            "status": self.status.value,
            "attributes": self.attributes,
        }
        if self.parent_id:
            result["parent_id"] = self.parent_id
        if self.end_time is not None:
            result["end_time"] = self.end_time
            result["duration_ms"] = self.duration_ms
        if self.error_message:
            result["error"] = self.error_message
        return result


@dataclass
class Trace:
    """
    一次完整的用户请求追踪。

    一个 Trace 包含多个 Span，代表从接收用户消息到返回响应的全过程。
    """

    trace_id: str
    session_id: str
    start_time: float
    spans: list[Span] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    end_time: float | None = None

    @property
    def duration_ms(self) -> float | None:
        """总耗时（毫秒）"""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    @property
    def span_count(self) -> int:
        """Span 数量"""
        return len(self.spans)

    def add_span(self, span: Span) -> None:
        """添加 Span"""
        self.spans.append(span)

    def finish(self) -> None:
        """结束 Trace"""
        self.end_time = time.time()

    def get_summary(self) -> dict[str, Any]:
        """获取追踪摘要"""
        llm_spans = [s for s in self.spans if s.span_type == SpanType.LLM]
        tool_spans = [s for s in self.spans if s.span_type == SpanType.TOOL]

        total_input_tokens = sum(s.attributes.get("input_tokens", 0) for s in llm_spans)
        total_output_tokens = sum(s.attributes.get("output_tokens", 0) for s in llm_spans)

        tool_errors = sum(1 for s in tool_spans if s.status == SpanStatus.ERROR)

        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "duration_ms": self.duration_ms,
            "total_spans": self.span_count,
            "llm_calls": len(llm_spans),
            "tool_calls": len(tool_spans),
            "tool_errors": tool_errors,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "metadata": self.metadata,
        }

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
            "spans": [s.to_dict() for s in self.spans],
            "summary": self.get_summary(),
        }


class AgentTracer:
    """
    Agent 追踪器。

    管理 Trace 和 Span 的生命周期，支持嵌套 Span 和多种导出器。

    Usage:
        tracer = AgentTracer()
        tracer.add_exporter(FileExporter("data/traces"))

        with tracer.start_trace("session-123") as trace:
            with tracer.llm_span(model="claude-4") as span:
                # ... LLM call ...
                span.set_attribute("input_tokens", 100)
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._exporters: list[Any] = []  # TraceExporter instances
        self._current_trace: Trace | None = None
        self._span_stack: list[Span] = []
        self._trace_stack: list[tuple[Trace | None, list[Span]]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def add_exporter(self, exporter: Any) -> None:
        """添加追踪导出器"""
        self._exporters.append(exporter)

    @contextmanager
    def start_trace(self, session_id: str, **metadata: Any) -> Generator[Trace, None, None]:
        """
        开始一个新的 Trace。

        作为上下文管理器使用，退出时自动结束并导出。
        """
        if not self._enabled:
            # 返回一个空 Trace，不记录
            yield Trace(trace_id="", session_id=session_id, start_time=time.time())
            return

        saved_trace = self._current_trace
        saved_stack = self._span_stack

        trace = Trace(
            trace_id=str(uuid.uuid4()),
            session_id=session_id,
            start_time=time.time(),
            metadata=metadata,
        )
        self._current_trace = trace
        self._span_stack = []

        try:
            yield trace
        finally:
            trace.finish()
            self._export_trace(trace)
            self._current_trace = saved_trace
            self._span_stack = saved_stack

    def start_span(
        self,
        name: str,
        span_type: SpanType,
        parent: Span | None = None,
        **attributes: Any,
    ) -> Span:
        """
        创建并开始一个新的 Span。

        如果不指定 parent，自动使用 span stack 栈顶作为 parent。
        """
        if not self._enabled:
            return Span(span_id="", name=name, span_type=span_type, start_time=time.time())

        parent_id = None
        if parent:
            parent_id = parent.span_id
        elif self._span_stack:
            parent_id = self._span_stack[-1].span_id

        span = Span(
            span_id=str(uuid.uuid4()),
            name=name,
            span_type=span_type,
            start_time=time.time(),
            parent_id=parent_id,
            attributes=attributes,
        )

        if self._current_trace:
            self._current_trace.add_span(span)

        return span

    def end_span(self, span: Span, status: SpanStatus | None = None) -> None:
        """结束一个 Span"""
        if not self._enabled or not span.span_id:
            return
        span.finish(status)

    @contextmanager
    def span(
        self,
        name: str,
        span_type: SpanType,
        **attributes: Any,
    ) -> Generator[Span, None, None]:
        """
        通用 Span 上下文管理器。

        自动管理开始/结束和 span stack。
        """
        span = self.start_span(name, span_type, **attributes)
        self._span_stack.append(span)
        try:
            yield span
        except Exception as e:
            span.set_error(str(e))
            raise
        finally:
            if self._span_stack:
                self._span_stack.pop()
            span.finish()

    @contextmanager
    def llm_span(self, model: str = "", **attributes: Any) -> Generator[Span, None, None]:
        """LLM 调用 Span"""
        attrs = {"model": model, **attributes}
        with self.span("llm.call", SpanType.LLM, **attrs) as s:
            yield s

    @contextmanager
    def tool_span(self, tool_name: str = "", **attributes: Any) -> Generator[Span, None, None]:
        """工具执行 Span"""
        attrs = {"tool_name": tool_name, **attributes}
        with self.span("tool.execute", SpanType.TOOL, **attrs) as s:
            yield s

    @contextmanager
    def tool_batch_span(self, count: int = 0, **attributes: Any) -> Generator[Span, None, None]:
        """工具批量执行 Span"""
        attrs = {"tool_count": count, **attributes}
        with self.span("tool.batch", SpanType.TOOL_BATCH, **attrs) as s:
            yield s

    @contextmanager
    def memory_span(self, operation: str = "", **attributes: Any) -> Generator[Span, None, None]:
        """记忆操作 Span"""
        attrs = {"operation": operation, **attributes}
        with self.span("memory." + operation, SpanType.MEMORY, **attrs) as s:
            yield s

    @contextmanager
    def context_span(self, operation: str = "", **attributes: Any) -> Generator[Span, None, None]:
        """上下文操作 Span"""
        attrs = {"operation": operation, **attributes}
        with self.span("context." + operation, SpanType.CONTEXT, **attrs) as s:
            yield s

    @contextmanager
    def reasoning_span(self, iteration: int = 0, **attributes: Any) -> Generator[Span, None, None]:
        """推理循环 Span"""
        attrs = {"iteration": iteration, **attributes}
        with self.span("reasoning.iteration", SpanType.REASONING, **attrs) as s:
            yield s

    @contextmanager
    def task_span(self, session_id: str = "", **attributes: Any) -> Generator[Span, None, None]:
        """完整任务 Span"""
        attrs = {"session_id": session_id, **attributes}
        with self.span("agent.task", SpanType.TASK, **attrs) as s:
            yield s

    # ==================== Agent Harness: 决策追踪 ====================

    @contextmanager
    def decision_span(
        self,
        decision_type: str = "",
        reasoning: str = "",
        **attributes: Any,
    ) -> Generator[Span, None, None]:
        """决策节点 Span（工具选择/策略选择/任务分解）"""
        attrs = {
            "decision_type": decision_type,
            "reasoning": reasoning[:500] if reasoning else "",
            **attributes,
        }
        with self.span(f"decision.{decision_type}", SpanType.DECISION, **attrs) as s:
            yield s

    @contextmanager
    def verification_span(
        self,
        verification_type: str = "",
        **attributes: Any,
    ) -> Generator[Span, None, None]:
        """验证节点 Span（任务完成验证/Plan 步骤验证）"""
        attrs = {"verification_type": verification_type, **attributes}
        with self.span(f"verification.{verification_type}", SpanType.VERIFICATION, **attrs) as s:
            yield s

    @contextmanager
    def supervision_span(
        self,
        pattern: str = "",
        level: str = "",
        **attributes: Any,
    ) -> Generator[Span, None, None]:
        """监督节点 Span（循环检测/干预决策）"""
        attrs = {"pattern": pattern, "level": level, **attributes}
        with self.span(f"supervision.{pattern}", SpanType.SUPERVISION, **attrs) as s:
            yield s

    @contextmanager
    def delegation_span(
        self,
        from_agent: str = "",
        to_agent: str = "",
        **attributes: Any,
    ) -> Generator[Span, None, None]:
        """委派节点 Span（多 Agent 委派）"""
        attrs = {"from_agent": from_agent, "to_agent": to_agent, **attributes}
        with self.span("delegation.agent", SpanType.DELEGATION, **attrs) as s:
            yield s

    def record_decision(
        self,
        decision_type: str,
        reasoning: str = "",
        outcome: str = "",
        **metadata: Any,
    ) -> None:
        """快速记录一个决策事件（非上下文管理器，用于轻量追踪模式）"""
        if not self._enabled:
            return
        span = self.start_span(
            f"decision.{decision_type}",
            SpanType.DECISION,
            decision_type=decision_type,
            reasoning=reasoning[:500] if reasoning else "",
            outcome=outcome,
            **metadata,
        )
        span.finish()

    # ==================== 非上下文管理器 API ====================
    # 用于 run() 等多返回路径的场景

    def begin_trace(self, session_id: str, metadata: dict[str, Any] | None = None) -> Trace | None:
        """
        开始一个新的 Trace（非上下文管理器版本）。

        必须手动调用 end_trace() 来结束。
        适合多 return 路径的长方法。
        """
        if not self._enabled:
            return None

        # Save parent trace context so nested begin_trace (e.g. from sub-agent delegation)
        # doesn't destroy the parent's span stack.
        self._trace_stack.append((self._current_trace, self._span_stack))

        trace = Trace(
            trace_id=str(uuid.uuid4()),
            session_id=session_id,
            start_time=time.time(),
            metadata=metadata or {},
        )
        self._current_trace = trace
        self._span_stack = []
        return trace

    def end_trace(self, metadata: dict[str, Any] | None = None) -> None:
        """
        结束当前 Trace（非上下文管理器版本）。

        与 begin_trace() 配对使用。
        """
        if not self._enabled or not self._current_trace:
            return

        if metadata:
            self._current_trace.metadata.update(metadata)

        self._current_trace.finish()
        self._export_trace(self._current_trace)

        # Restore parent trace context
        if self._trace_stack:
            self._current_trace, self._span_stack = self._trace_stack.pop()
        else:
            self._current_trace = None
            self._span_stack = []

    def _export_trace(self, trace: Trace) -> None:
        """导出 Trace 到所有已注册的导出器"""
        for exporter in self._exporters:
            try:
                exporter.export(trace)
            except Exception as e:
                logger.warning(
                    f"[Tracing] Failed to export trace to {type(exporter).__name__}: {e}"
                )


# 全局 tracer 实例
_global_tracer: AgentTracer | None = None


def get_tracer() -> AgentTracer:
    """获取全局 tracer 实例"""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = AgentTracer(enabled=False)  # 默认禁用，需要显式启用
    return _global_tracer


def set_tracer(tracer: AgentTracer) -> None:
    """设置全局 tracer 实例"""
    global _global_tracer
    _global_tracer = tracer
