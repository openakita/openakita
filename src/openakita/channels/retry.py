"""
通用异步重试工具

为 IM 适配器的 HTTP 请求（发送消息、上传文件、下载媒体等）
提供统一的指数退避重试机制。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def default_should_retry(exc: BaseException) -> bool:
    """默认重试判定：网络/超时/服务端 5xx 类错误可重试。

    各适配器可提供自定义判定函数来处理平台特有的可重试错误码
    （如 token 过期、限流等）。
    """
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError)):
        return True
    try:
        import httpx
    except ImportError:
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status == 429
    if isinstance(exc, httpx.TransportError):
        return True
    return False


def _extract_retry_after(exc: BaseException) -> float | None:
    """Extract Retry-After seconds from an HTTP 429 response, if available."""
    try:
        import httpx
    except ImportError:
        return None
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        ra = exc.response.headers.get("retry-after")
        if ra:
            try:
                return float(ra)
            except (ValueError, TypeError):
                pass
    return None


async def async_with_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    should_retry: Callable[[BaseException], bool] | None = None,
    operation_name: str = "",
    **kwargs: Any,
) -> T:
    """带指数退避的异步重试执行器。

    Args:
        fn: 要执行的异步函数
        *args: 传给 fn 的位置参数
        max_retries: 最大重试次数（不含首次执行）
        base_delay: 首次重试前的等待秒数
        max_delay: 最大等待秒数上限
        backoff_factor: 退避倍数
        should_retry: 判定异常是否可重试的函数；None 时使用 default_should_retry
        operation_name: 日志中标识操作名称
        **kwargs: 传给 fn 的关键字参数

    Returns:
        fn 的返回值

    Raises:
        最后一次失败的原始异常
    """
    if should_retry is None:
        should_retry = default_should_retry

    label = operation_name or fn.__qualname__
    last_exc: BaseException | None = None

    for attempt in range(1 + max_retries):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries or not should_retry(exc):
                raise
            delay = min(base_delay * (backoff_factor**attempt), max_delay)
            jitter = random.uniform(0, delay * 0.25)
            delay += jitter
            retry_after = _extract_retry_after(exc)
            if retry_after is not None:
                delay = max(delay, retry_after)
            logger.warning(
                f"[Retry] {label} attempt {attempt + 1}/{1 + max_retries} "
                f"failed: {exc!r}; retrying in {delay:.1f}s"
            )
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]
