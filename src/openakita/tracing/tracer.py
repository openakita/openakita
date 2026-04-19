"""
Core Tracer

Provides Trace / Span data models and AgentTracer tracing manager.
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
    """Span type"""

    LLM = "llm"  # LLM call
    TOOL = "tool"  # Tool execution
    TOOL_BATCH = "tool_batch"  # Batch tool execution
    MEMORY = "memory"  # Memory operation
    CONTEXT = "context"  # Context management (compression, etc.)
    REASONING = "reasoning"  # Reasoning loop
    PROMPT = "prompt"  # Prompt construction
    TASK = "task"  # Complete task
    # Agent Harness: decision tracing extension
    DECISION = "decision"  # Decision node (tool selection / strategy selection)
    VERIFICATION = "verification"  # Verification node (task completion verification / plan step verification)
    SUPERVISION = "supervision"  # Supervision node (loop detection / intervention decision)
    DELEGATION = "delegation"  # Delegation node (multi-agent delegation)


class SpanStatus(Enum):
    """Span status"""

    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class Span:
    """
    Trace record for a single operation.

    A Span represents an LLM call, tool execution, memory retrieval, etc.
    Spans can be nested to form parent-child relationships.
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
        """Duration in milliseconds"""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    def set_attribute(self, key: str, value: Any) -> None:
        """Set attribute"""
        self.attributes[key] = value

    def set_error(self, message: str) -> None:
        """Mark as error"""
        self.status = SpanStatus.ERROR
        self.error_message = message

    def finish(self, status: SpanStatus | None = None) -> None:
        """End Span"""
        self.end_time = time.time()
        if status is not None:
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict"""
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
    Trace of a complete user request.

    A Trace contains multiple Spans, representing the entire process from receiving
    a user message to returning a response.
    """

    trace_id: str
    session_id: str
    start_time: float
    spans: list[Span] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    end_time: float | None = None

    @property
    def duration_ms(self) -> float | None:
        """Total duration in milliseconds"""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    @property
    def span_count(self) -> int:
        """Span count"""
        return len(self.spans)

    def add_span(self, span: Span) -> None:
        """Add Span"""
        self.spans.append(span)

    def finish(self) -> None:
        """End Trace"""
        self.end_time = time.time()

    def get_summary(self) -> dict[str, Any]:
        """Get trace summary"""
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
        """Serialize to dict"""
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
    Agent tracer.

    Manages the lifecycle of Traces and Spans, supports nested Spans and multiple exporters.

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
        """Add trace exporter"""
        self._exporters.append(exporter)

    @contextmanager
    def start_trace(self, session_id: str, **metadata: Any) -> Generator[Trace, None, None]:
        """
        Start a new Trace.

        Use as a context manager; automatically finishes and exports on exit.
        """
        if not self._enabled:
            # Return an empty Trace without recording
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
        Create and start a new Span.

        If no parent is specified, the top of the span stack is used as parent.
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
        """End a Span"""
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
        Generic Span context manager.

        Automatically manages start/end and the span stack.
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
        """LLM call Span"""
        attrs = {"model": model, **attributes}
        with self.span("llm.call", SpanType.LLM, **attrs) as s:
            yield s

    @contextmanager
    def tool_span(self, tool_name: str = "", **attributes: Any) -> Generator[Span, None, None]:
        """Tool execution Span"""
        attrs = {"tool_name": tool_name, **attributes}
        with self.span("tool.execute", SpanType.TOOL, **attrs) as s:
            yield s

    @contextmanager
    def tool_batch_span(self, count: int = 0, **attributes: Any) -> Generator[Span, None, None]:
        """Batch tool execution Span"""
        attrs = {"tool_count": count, **attributes}
        with self.span("tool.batch", SpanType.TOOL_BATCH, **attrs) as s:
            yield s

    @contextmanager
    def memory_span(self, operation: str = "", **attributes: Any) -> Generator[Span, None, None]:
        """Memory operation Span"""
        attrs = {"operation": operation, **attributes}
        with self.span("memory." + operation, SpanType.MEMORY, **attrs) as s:
            yield s

    @contextmanager
    def context_span(self, operation: str = "", **attributes: Any) -> Generator[Span, None, None]:
        """Context operation Span"""
        attrs = {"operation": operation, **attributes}
        with self.span("context." + operation, SpanType.CONTEXT, **attrs) as s:
            yield s

    @contextmanager
    def reasoning_span(self, iteration: int = 0, **attributes: Any) -> Generator[Span, None, None]:
        """Reasoning loop Span"""
        attrs = {"iteration": iteration, **attributes}
        with self.span("reasoning.iteration", SpanType.REASONING, **attrs) as s:
            yield s

    @contextmanager
    def task_span(self, session_id: str = "", **attributes: Any) -> Generator[Span, None, None]:
        """Complete task Span"""
        attrs = {"session_id": session_id, **attributes}
        with self.span("agent.task", SpanType.TASK, **attrs) as s:
            yield s

    # ==================== Agent Harness: Decision tracing ====================

    @contextmanager
    def decision_span(
        self,
        decision_type: str = "",
        reasoning: str = "",
        **attributes: Any,
    ) -> Generator[Span, None, None]:
        """Decision node Span (tool selection / strategy selection / task decomposition)"""
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
        """Verification node Span (task completion check / plan step verification)"""
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
        """Supervision node Span (loop detection / intervention decision)"""
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
        """Delegation node Span (multi-agent delegation)"""
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
        """Quickly record a decision event (non-context-manager, for lightweight tracing)"""
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

    # ==================== Non-context-manager API ====================
    # For multi-return-path scenarios like run()

    def begin_trace(self, session_id: str, metadata: dict[str, Any] | None = None) -> Trace | None:
        """
        Start a new Trace (non-context-manager version).

        Must be paired with a manual end_trace() call.
        Suitable for long methods with multiple return paths.
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
        End the current Trace (non-context-manager version).

        Must be paired with begin_trace().
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
        """Export Trace to all registered exporters"""
        for exporter in self._exporters:
            try:
                exporter.export(trace)
            except Exception as e:
                logger.warning(
                    f"[Tracing] Failed to export trace to {type(exporter).__name__}: {e}"
                )


# Global tracer instance
_global_tracer: AgentTracer | None = None


def get_tracer() -> AgentTracer:
    """Get the global tracer instance"""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = AgentTracer(enabled=False)  # Disabled by default; must be explicitly enabled
    return _global_tracer


def set_tracer(tracer: AgentTracer) -> None:
    """Set the global tracer instance"""
    global _global_tracer
    _global_tracer = tracer
