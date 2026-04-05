"""
重试与退避策略

参考 Claude Code withRetry.ts 设计:
- 指数退避: BASE_DELAY * 2^(attempt-1)，上限 32s，加 25% jitter
- Retry-After 头优先
- 429 vs 529 区分
- 连续 529 达 3 次触发 fallback model
- Persistent 模式: 长等待 + 心跳
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

RETRY_BASE_DELAY_MS = 500
RETRY_MAX_DELAY_MS = 32_000
RETRY_JITTER_FACTOR = 0.25

MAX_RETRIES = 10
MAX_529_RETRIES = 3

PERSISTENT_MAX_DELAY_MS = 300_000  # 5 min
PERSISTENT_HEARTBEAT_INTERVAL_S = 30

FOREGROUND_SOURCES = {"main_loop", "repl_main_thread"}


@dataclass
class RetryContext:
    """重试上下文（可变状态，不污染 LLMRequest）"""

    max_tokens_override: int | None = None
    consecutive_529: int = 0
    total_attempts: int = 0
    should_fallback: bool = False
    last_error: Exception | None = None
    retry_after_seconds: float | None = None


@dataclass
class RetryEvent:
    """重试过程中的事件（供 UI/日志消费）"""

    type: str  # 'retry_wait', 'heartbeat', 'fallback_triggered', 'error'
    message: str = ""
    data: dict = field(default_factory=dict)


def calculate_retry_delay(
    attempt: int,
    retry_after: float | None = None,
    persistent: bool = False,
) -> float:
    """计算重试延迟（毫秒）。

    Args:
        attempt: 当前尝试次数 (1-based)
        retry_after: 服务器返回的 Retry-After 秒数
        persistent: 是否持久模式（长等待）

    Returns:
        延迟毫秒数
    """
    if retry_after is not None and retry_after > 0:
        return retry_after * 1000

    max_delay = PERSISTENT_MAX_DELAY_MS if persistent else RETRY_MAX_DELAY_MS
    base = min(RETRY_BASE_DELAY_MS * (2 ** (attempt - 1)), max_delay)
    jitter = random.random() * RETRY_JITTER_FACTOR * base
    return base + jitter


def parse_retry_after(headers: dict | None) -> float | None:
    """从 HTTP 响应头解析 Retry-After 值。

    支持: 秒数 (int/float) 和 HTTP-date 格式。
    """
    if not headers:
        return None

    value = None
    for key in ("retry-after", "Retry-After", "x-ratelimit-reset-requests"):
        if key in headers:
            value = headers[key]
            break

    if value is None:
        return None

    try:
        return float(value)
    except (ValueError, TypeError):
        pass

    return None


def should_retry(
    error: Exception,
    attempt: int,
    max_retries: int = MAX_RETRIES,
) -> bool:
    """判断是否应该重试。"""
    if attempt >= max_retries:
        return False

    error_str = str(error).lower()

    # Never retry auth errors
    if any(kw in error_str for kw in ("401", "403", "auth", "api_key", "permission")):
        # But exclude quota errors masquerading as 403
        if not any(kw in error_str for kw in ("quota", "billing", "insufficient")):
            return False

    # Always retry transient errors
    if any(kw in error_str for kw in (
        "timeout", "timed out", "connect", "connection", "network",
        "429", "502", "503", "504", "529",
        "econnreset", "epipe", "eof",
        "readerror", "read error", "remotedisconnected", "broken pipe",
        "readtimeout", "incompleteread",
    )):
        return True

    # Retry rate limit errors
    if "rate" in error_str and "limit" in error_str:
        return True

    # Retry server errors
    if any(kw in error_str for kw in ("500", "server error", "internal")):
        return True

    return False


def is_529_error(error: Exception) -> bool:
    """判断是否为 529 (overloaded) 错误。"""
    return "529" in str(error)


def is_context_overflow_error(error: Exception) -> int | None:
    """检查是否为上下文窗口溢出错误，返回建议的 max_tokens。

    Returns:
        建议的 max_tokens，如果不是溢出错误则返回 None
    """
    error_str = str(error).lower()
    overflow_indicators = (
        "context_window_exceeded",
        "prompt is too long",
        "maximum context length",
        "max_tokens is too large",
    )
    if not any(ind in error_str for ind in overflow_indicators):
        return None

    # Try to extract suggested max from error message
    import re
    patterns = [
        r"max_tokens.*?(\d+)",
        r"maximum.*?(\d+)\s*tokens",
        r"reduce.*?(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, error_str)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


async def retry_with_backoff(
    fn,
    *,
    max_retries: int = MAX_RETRIES,
    query_source: str = "",
    persistent: bool = False,
) -> AsyncIterator[RetryEvent | Any]:
    """带退避的重试包装器 (async generator)。

    在重试等待期间 yield RetryEvent，成功时 yield 结果。
    调用方通过 async for 消费。

    Args:
        fn: async callable，调用目标
        max_retries: 最大重试次数
        query_source: 请求来源（影响 529 策略）
        persistent: 持久模式
    """
    ctx = RetryContext()

    for attempt in range(1, max_retries + 1):
        ctx.total_attempts = attempt
        try:
            result = await fn()
            yield result
            return

        except Exception as e:
            ctx.last_error = e

            if is_529_error(e):
                ctx.consecutive_529 += 1
                if ctx.consecutive_529 >= MAX_529_RETRIES:
                    ctx.should_fallback = True
                    yield RetryEvent(
                        type="fallback_triggered",
                        message=f"Triggered fallback after {MAX_529_RETRIES} consecutive 529s",
                    )
                    raise

                if query_source and query_source not in FOREGROUND_SOURCES:
                    raise
            else:
                ctx.consecutive_529 = 0

            if not should_retry(e, attempt, max_retries):
                raise

            # Parse Retry-After from exception attributes
            retry_after = getattr(e, "retry_after_seconds", None)
            delay_ms = calculate_retry_delay(attempt, retry_after, persistent)

            yield RetryEvent(
                type="retry_wait",
                message=f"Retry {attempt}/{max_retries} after {delay_ms:.0f}ms: {e}",
                data={"attempt": attempt, "delay_ms": delay_ms},
            )

            if persistent and delay_ms > PERSISTENT_HEARTBEAT_INTERVAL_S * 1000:
                elapsed = 0.0
                while elapsed < delay_ms:
                    wait = min(PERSISTENT_HEARTBEAT_INTERVAL_S * 1000, delay_ms - elapsed)
                    await asyncio.sleep(wait / 1000)
                    elapsed += wait
                    if elapsed < delay_ms:
                        yield RetryEvent(
                            type="heartbeat",
                            message=f"Waiting... ({elapsed / 1000:.0f}s / {delay_ms / 1000:.0f}s)",
                        )
            else:
                await asyncio.sleep(delay_ms / 1000)

    if ctx.last_error:
        raise ctx.last_error
