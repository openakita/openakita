"""
LLM 调用可观测性基础设施

提供:
- 请求级唯一 ID (X-Request-ID)
- TTFT (Time to First Token) 追踪
- Stall 检测 (流式卡顿)
- 结构化指标记录
- 请求来源 (query_source) 维度统计
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from uuid import uuid4

logger = logging.getLogger(__name__)

STALL_THRESHOLD_SECONDS = 30.0


@dataclass
class LLMCallMetrics:
    """单次 LLM 调用的可观测指标"""

    request_id: str = field(default_factory=lambda: str(uuid4()))
    endpoint: str = ""
    model: str = ""
    query_source: str = ""
    is_streaming: bool = False

    # Timing
    start_time: float = field(default_factory=time.monotonic)
    ttft_ms: float | None = None  # Time to First Token
    total_ms: float | None = None

    # Tokens
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    # Status
    attempt: int = 1
    stop_reason: str | None = None
    error: str | None = None

    # Internal tracking
    _first_token_received: bool = field(default=False, repr=False)
    _last_chunk_time: float = field(default=0.0, repr=False)

    def record_first_token(self) -> None:
        """记录首个 token 到达时间。"""
        if not self._first_token_received:
            self._first_token_received = True
            self.ttft_ms = (time.monotonic() - self.start_time) * 1000
            self._last_chunk_time = time.monotonic()

    def record_chunk(self) -> float | None:
        """记录 chunk 到达，返回距上次 chunk 的间隔秒数（用于 stall 检测）。"""
        now = time.monotonic()
        if self._last_chunk_time > 0:
            gap = now - self._last_chunk_time
            self._last_chunk_time = now
            return gap
        self._last_chunk_time = now
        return None

    def record_completion(
        self,
        usage: dict | None = None,
        stop_reason: str | None = None,
    ) -> None:
        """记录请求完成。"""
        self.total_ms = (time.monotonic() - self.start_time) * 1000
        if stop_reason:
            self.stop_reason = stop_reason
        if usage:
            self.input_tokens = usage.get("input_tokens", self.input_tokens)
            self.output_tokens = usage.get("output_tokens", self.output_tokens)
            self.cache_read_tokens = usage.get("cache_read_input_tokens", self.cache_read_tokens)
            self.cache_creation_tokens = usage.get(
                "cache_creation_input_tokens", self.cache_creation_tokens
            )

    def record_error(self, error: str) -> None:
        """记录错误。"""
        self.error = error
        if self.total_ms is None:
            self.total_ms = (time.monotonic() - self.start_time) * 1000

    def to_log_dict(self) -> dict:
        """转换为日志友好的 dict。"""
        d = {
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "model": self.model,
            "is_streaming": self.is_streaming,
            "attempt": self.attempt,
        }
        if self.query_source:
            d["query_source"] = self.query_source
        if self.ttft_ms is not None:
            d["ttft_ms"] = round(self.ttft_ms, 1)
        if self.total_ms is not None:
            d["total_ms"] = round(self.total_ms, 1)
        if self.input_tokens:
            d["input_tokens"] = self.input_tokens
        if self.output_tokens:
            d["output_tokens"] = self.output_tokens
        if self.cache_read_tokens:
            d["cache_read_tokens"] = self.cache_read_tokens
        if self.cache_creation_tokens:
            d["cache_creation_tokens"] = self.cache_creation_tokens
        if self.stop_reason:
            d["stop_reason"] = self.stop_reason
        if self.error:
            d["error"] = self.error[:200]
        return d


class LLMObserver:
    """集中的 LLM 调用可观测性管理器。"""

    def __init__(self) -> None:
        self._listeners: list = []

    def on_request_start(self, metrics: LLMCallMetrics) -> None:
        logger.debug(
            "LLM request start: endpoint=%s model=%s request_id=%s",
            metrics.endpoint, metrics.model, metrics.request_id,
        )

    def on_first_token(self, metrics: LLMCallMetrics) -> None:
        logger.debug(
            "LLM TTFT: %.1fms request_id=%s",
            metrics.ttft_ms or 0, metrics.request_id,
        )

    def on_stall_detected(self, metrics: LLMCallMetrics, idle_seconds: float) -> None:
        logger.warning(
            "LLM streaming stall detected: %.1fs idle, request_id=%s endpoint=%s",
            idle_seconds, metrics.request_id, metrics.endpoint,
        )

    def on_request_end(self, metrics: LLMCallMetrics) -> None:
        log_data = metrics.to_log_dict()
        if metrics.error:
            logger.warning("LLM request failed: %s", log_data)
        else:
            logger.info("LLM request completed: %s", log_data)

    def on_error(self, metrics: LLMCallMetrics) -> None:
        logger.error(
            "LLM error: %s request_id=%s endpoint=%s",
            metrics.error, metrics.request_id, metrics.endpoint,
        )


_default_observer = LLMObserver()


def get_observer() -> LLMObserver:
    """获取全局 LLM Observer 实例。"""
    return _default_observer


def set_observer(observer: LLMObserver) -> None:
    """替换全局 LLM Observer（用于测试或自定义）。"""
    global _default_observer
    _default_observer = observer
