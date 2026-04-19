"""
OpenAkita Observability and Tracing System

Provides structured tracing capabilities covering:
- LLM calls (model, token usage, latency)
- Tool execution (tool name, success/failure, duration)
- Memory operations (retrieval, extraction, writes)
- Context compression (token counts before/after compression)
- Reasoning loops (iteration count, state transitions)

Supported exporters:
- FileExporter: JSON file (default)
- ConsoleExporter: Console output
- OpenTelemetry: OTEL-compatible export (optional)
"""

from .exporter import ConsoleExporter, FileExporter, TraceExporter
from .tracer import AgentTracer, Span, SpanStatus, SpanType, Trace, get_tracer, set_tracer

__all__ = [
    "AgentTracer",
    "Trace",
    "Span",
    "SpanType",
    "SpanStatus",
    "TraceExporter",
    "FileExporter",
    "ConsoleExporter",
    "get_tracer",
    "set_tracer",
]
