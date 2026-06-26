"""
OpenAkita 观测追踪系统

提供结构化的追踪能力，覆盖:
- LLM 调用 (模型、token 消耗、延迟)
- 工具执行 (工具名、成功/失败、耗时)
- 记忆操作 (检索、提取、写入)
- 上下文压缩 (压缩前后 token 数)
- 推理循环 (迭代次数、状态转换)

支持多种导出器:
- FileExporter: JSON 文件 (默认)
- ConsoleExporter: 控制台输出
- OpenTelemetry: OTEL 兼容导出 (可选)
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
