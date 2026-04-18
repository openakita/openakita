"""
General-purpose async retry utilities.

Provides a unified exponential-backoff retry mechanism for IM adapter HTTP
requests (sending messages, uploading files, downloading media, etc.).
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
    """Default retry predicate: network/timeout/server 5xx errors are retryable.

    Adapters can supply a custom predicate to handle platform-specific
    retryable error codes (e.g., token expiry, rate limiting).
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
    """Async retry executor with exponential backoff.

    Args:
        fn: The async function to execute.
        *args: Positional arguments forwarded to fn.
        max_retries: Maximum number of retries (excluding the first attempt).
        base_delay: Seconds to wait before the first retry.
        max_delay: Upper bound on the wait time.
        backoff_factor: Multiplier for each backoff step.
        should_retry: Predicate that decides whether an exception is retryable;
            None uses default_should_retry.
        operation_name: Label used in log messages to identify the operation.
        **kwargs: Keyword arguments forwarded to fn.

    Returns:
        The return value of fn.

    Raises:
        The original exception from the last failed attempt.
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
