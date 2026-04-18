"""
LLM Provider base class

Defines the interface all providers must implement.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterator

from ..types import EndpointConfig, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


class RPMRateLimiter:
    """Sliding-window RPM (Requests Per Minute) rate limiter.

    Uses a 60-second sliding window plus asyncio.Lock for concurrency safety.
    When the request rate exceeds the limit, it waits automatically until
    quota is available in the window.
    """

    __slots__ = ("_rpm", "_window", "_timestamps", "_lock", "_lock_loop_id")

    def __init__(self, rpm: int):
        self._rpm = rpm
        self._window = 60.0
        self._timestamps: deque[float] = deque()
        self._lock: asyncio.Lock | None = None
        self._lock_loop_id: int | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Get or create an asyncio.Lock (bound to the current event loop)."""
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = None
        if self._lock is None or self._lock_loop_id != loop_id:
            self._lock = asyncio.Lock()
            self._lock_loop_id = loop_id
        return self._lock

    async def acquire(self, endpoint_name: str = "") -> None:
        """Acquire one request quota, waiting if necessary."""
        if self._rpm <= 0:
            return

        lock = self._get_lock()
        while True:
            async with lock:
                now = time.monotonic()
                while self._timestamps and self._timestamps[0] <= now - self._window:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._rpm:
                    self._timestamps.append(now)
                    return

                oldest = self._timestamps[0]
                wait_time = oldest + self._window - now

            tag = f" endpoint={endpoint_name}" if endpoint_name else ""
            logger.info(
                f"[RPM]{tag} rate limit reached ({self._rpm} rpm), waiting {wait_time:.1f}s"
            )
            await asyncio.sleep(max(wait_time, 0.1))


# Cooldown durations (seconds) — differentiated by error type
COOLDOWN_AUTH = 60  # Auth errors: 1 minute (needs human intervention, but don't lock too long)
COOLDOWN_QUOTA = 300  # Quota exhausted: 5 minutes (quota typically takes hours to recover)
COOLDOWN_STRUCTURAL = 10  # Structural errors: 10 seconds (upper layers recognize and handle quickly)
COOLDOWN_TRANSIENT = 5  # Transient errors: 5 seconds (timeouts/connection failures usually recover quickly)
COOLDOWN_DEFAULT = 30  # Default: 30 seconds
COOLDOWN_GLOBAL_FAILURE = 10  # Global failure (all endpoints failing at once): 10 seconds

# Progressive cooldown backoff — escalates with consecutive failures
COOLDOWN_ESCALATION_STEPS = [5, 10, 20, 60]  # 5s -> 10s -> 20s -> 60s (cap)
# Dedicated backoff for quota/auth — first 5 minutes, escalates up to 30 minutes on consecutive failures
COOLDOWN_QUOTA_ESCALATION = [300, 600, 1200, 1800]  # 5m -> 10m -> 20m -> 30m

# Backward compatibility (referenced by legacy code)
COOLDOWN_EXTENDED = COOLDOWN_ESCALATION_STEPS[-1]
CONSECUTIVE_FAILURE_THRESHOLD = 3  # Retained for backward compatibility; no longer triggers the 1h cooldown
COOLDOWN_SECONDS = COOLDOWN_DEFAULT


class LLMProvider(ABC):
    """LLM Provider base class"""

    def __init__(self, config: EndpointConfig):
        self.config = config
        self._healthy = True
        self._last_error: str | None = None
        self._cooldown_until: float = 0  # Cooldown end timestamp
        self._error_category: str = ""  # Error category
        self._consecutive_cooldowns: int = 0  # Number of consecutive cooldowns (without an intervening successful request)
        self._is_extended_cooldown: bool = False  # Whether in an escalated cooldown
        _rpm = config.rpm_limit if isinstance(config.rpm_limit, int) else 0
        self._rate_limiter: RPMRateLimiter | None = RPMRateLimiter(_rpm) if _rpm > 0 else None

    @property
    def name(self) -> str:
        """Provider name"""
        return self.config.name

    @property
    def model(self) -> str:
        """Model name"""
        return self.config.model

    @property
    def is_healthy(self) -> bool:
        """Whether the provider is healthy.

        Checks:
        1. Whether it has been marked unhealthy
        2. Whether it is still in a cooldown window
        """
        # Automatically return to healthy once the cooldown ends
        if self._cooldown_until > 0 and time.time() >= self._cooldown_until:
            self._healthy = True
            self._cooldown_until = 0
            self._last_error = None
            self._error_category = ""
            if self._is_extended_cooldown:
                self._is_extended_cooldown = False
                # After a progressive backoff ends, reset the consecutive counter so the endpoint can prove itself again
                self._consecutive_cooldowns = 0
                logger.info(
                    f"[LLM] endpoint={self.name} progressive cooldown expired, reset to healthy"
                )

        return self._healthy

    @property
    def last_error(self) -> str | None:
        """Most recent error"""
        return self._last_error

    @property
    def error_category(self) -> str:
        """Error category: auth / quota / structural / transient / unknown"""
        return self._error_category

    @property
    def cooldown_remaining(self) -> int:
        """Seconds remaining in the cooldown"""
        if self._cooldown_until <= 0:
            return 0
        remaining = self._cooldown_until - time.time()
        return max(0, int(remaining))

    @property
    def consecutive_cooldowns(self) -> int:
        """Number of consecutive cooldowns"""
        return self._consecutive_cooldowns

    @property
    def is_extended_cooldown(self) -> bool:
        """Whether currently in a progressive-escalation cooldown"""
        return self._is_extended_cooldown

    def mark_unhealthy(self, error: str, category: str = "", is_local: bool = False):
        """Mark unhealthy and enter cooldown.

        Args:
            error: error message
            category: error category; determines cooldown duration
                - "auth": auth error (60s)
                - "quota": quota exhausted (20s)
                - "structural": structural/format error (10s)
                - "transient": timeout/connection error (5s)
                - "": default (30s)
            is_local: whether this is a local endpoint (Ollama, etc.). Local
                transient errors do not participate in progressive escalation
                (timeouts are resource pressure, not remote failures).

        Progressive cooldown backoff:
            Consecutive non-structural cooldowns (without an intervening
            successful request) use increasing durations from
            COOLDOWN_ESCALATION_STEPS, capped at 5 minutes.
            - Structural errors do not count toward the consecutive tally (retrying doesn't change the outcome)
            - Local-endpoint transient errors do not trigger escalation (timeouts are expected)
        """
        was_already_unhealthy = not self._healthy
        self._healthy = False
        self._last_error = error
        self._error_category = category or self._classify_error(error)

        # Accumulate consecutive cooldown count
        # - Only increment on healthy → unhealthy transitions (multiple mark_unhealthy calls in the same retry round don't double-count)
        # - Structural errors are not counted: retries produce the same result
        # - Local transient errors are not counted: timeouts are resource pressure, so punishment is pointless
        skip_escalation = self._error_category == "structural" or (
            is_local and self._error_category == "transient"
        )
        if not skip_escalation and not was_already_unhealthy:
            self._consecutive_cooldowns += 1

        # Progressive backoff: pick cooldown from the corresponding schedule by consecutive-failure count
        if self._error_category == "quota":
            step_idx = min(
                max(self._consecutive_cooldowns - 1, 0),
                len(COOLDOWN_QUOTA_ESCALATION) - 1,
            )
            cooldown = max(COOLDOWN_QUOTA, COOLDOWN_QUOTA_ESCALATION[step_idx])
            if self._consecutive_cooldowns >= 2:
                self._is_extended_cooldown = True
                logger.warning(
                    f"[LLM] endpoint={self.name} quota progressive cooldown "
                    f"step {step_idx + 1}/{len(COOLDOWN_QUOTA_ESCALATION)} "
                    f"({cooldown}s) after {self._consecutive_cooldowns} "
                    f"consecutive failures"
                )
        elif self._error_category == "auth":
            cooldown = COOLDOWN_AUTH
        elif self._error_category == "structural":
            cooldown = COOLDOWN_STRUCTURAL
        elif self._error_category == "transient":
            if is_local:
                # Local endpoints use a fixed short cooldown without escalation
                cooldown = COOLDOWN_TRANSIENT
            elif self._consecutive_cooldowns >= 2:
                # Consecutive failures on remote endpoints → progressive backoff
                step_idx = min(
                    self._consecutive_cooldowns - 1,
                    len(COOLDOWN_ESCALATION_STEPS) - 1,
                )
                cooldown = COOLDOWN_ESCALATION_STEPS[step_idx]
                self._is_extended_cooldown = True
                logger.warning(
                    f"[LLM] endpoint={self.name} progressive cooldown "
                    f"step {step_idx + 1}/{len(COOLDOWN_ESCALATION_STEPS)} "
                    f"({cooldown}s) after {self._consecutive_cooldowns} "
                    f"consecutive failures"
                )
            else:
                cooldown = COOLDOWN_TRANSIENT
        else:
            # Unknown category: also apply progressive backoff
            if self._consecutive_cooldowns >= 2:
                step_idx = min(
                    self._consecutive_cooldowns - 1,
                    len(COOLDOWN_ESCALATION_STEPS) - 1,
                )
                cooldown = COOLDOWN_ESCALATION_STEPS[step_idx]
                self._is_extended_cooldown = True
            else:
                cooldown = COOLDOWN_DEFAULT

        self._cooldown_until = time.time() + cooldown

    def mark_healthy(self):
        """Mark healthy and clear cooldown and consecutive-failure counters."""
        self._healthy = True
        self._last_error = None
        self._cooldown_until = 0
        self._error_category = ""
        self._consecutive_cooldowns = 0
        self._is_extended_cooldown = False

    def record_success(self):
        """Record a successful request; reset the consecutive-failure counter and restore healthy state.

        Called after a successful response in _try_endpoints.
        If the endpoint was previously in cooldown (including extended cooldown),
        a successful request proves the endpoint has recovered, so the cooldown
        state should be fully cleared rather than keeping it unhealthy.
        """
        was_unhealthy = not self._healthy or self._cooldown_until > 0
        if was_unhealthy or self._consecutive_cooldowns > 0:
            logger.debug(
                f"[LLM] endpoint={self.name} success, "
                f"reset consecutive cooldowns ({self._consecutive_cooldowns} → 0)"
                + (", clearing cooldown (endpoint proved functional)" if was_unhealthy else "")
            )
        self._consecutive_cooldowns = 0
        self._is_extended_cooldown = False
        # A successful request proves the endpoint is usable; clear the cooldown (including extended cooldown)
        if was_unhealthy:
            self._healthy = True
            self._cooldown_until = 0
            self._last_error = None
            self._error_category = ""

    async def acquire_rate_limit(self):
        """Acquire an RPM quota, waiting if necessary. Returns immediately when no rate limit is configured."""
        if self._rate_limiter:
            await self._rate_limiter.acquire(endpoint_name=self.name)

    def reset_cooldown(self):
        """Reset the cooldown so the endpoint can be retried immediately.

        Used for global-failure recovery / "last-resort bypass" scenarios: once
        all endpoints fail at once, bypass the cooldown so every endpoint
        becomes eligible for retry (matches the Portkey design).

        Note: does not reset the consecutive-failure counter, because a global
        reset does not mean the endpoint has actually recovered. If the endpoint
        is truly broken, the next request will mark it unhealthy again.
        """
        if self._cooldown_until > 0 or self._is_extended_cooldown or not self._healthy:
            self._cooldown_until = 0
            self._is_extended_cooldown = False
            self._healthy = True
            self._last_error = None
            self._error_category = ""

    def shorten_cooldown(self, seconds: int):
        """Shorten the cooldown to the given number of seconds (only if the current cooldown is longer).

        Args:
            seconds: new cooldown duration in seconds (counted from now)

        Note: if a progressive-backoff cooldown is shortened, _is_extended_cooldown
        must be cleared at the same time. Otherwise, when the shortened cooldown
        expires, is_healthy will mistakenly assume the full backoff has completed
        and reset _consecutive_cooldowns, preventing progressive escalation from
        ever kicking in.
        """
        new_until = time.time() + seconds
        if self._cooldown_until > new_until:
            if self._is_extended_cooldown:
                self._is_extended_cooldown = False
            self._cooldown_until = new_until

    @staticmethod
    def _classify_error(error: str) -> str:
        """Automatically classify an error based on its message.

        Priority: quota > auth > structural > transient > unknown.
        Quota must be detected before auth because a 403 quota-exhausted error
        also contains the "403" keyword.

        Returns a ``FailoverReason`` enum member (StrEnum, interoperable with strings).
        """
        from ..error_types import FailoverReason

        err_lower = error.lower()

        if any(
            kw in err_lower
            for kw in [
                "allocationquota",
                "freetieronly",
                "insufficient_quota",
                "quota_exceeded",
                "billing",
                "free tier",
                "free_tier",
                "quota",
                "exceeded your current",
            ]
        ):
            return FailoverReason.QUOTA

        if any(
            kw in err_lower
            for kw in [
                "auth",
                "401",
                "403",
                "api_key",
                "invalid key",
                "permission",
            ]
        ):
            return FailoverReason.AUTH

        # Note: use "(400)" instead of "400" to avoid matching CSS class names and similar content in HTML
        if any(
            kw in err_lower
            for kw in [
                "invalid_request",
                "invalid_parameter",
                "messages with role",
                "must be a response",
                "does not support",
                "not supported",
                "(400)",
                "(413)",
                "payload too large",
                "request entity too large",
                "larger than allowed",
            ]
        ):
            return FailoverReason.STRUCTURAL

        if any(
            kw in err_lower
            for kw in [
                "timeout",
                "timed out",
                "connect",
                "connection",
                "network",
                "unreachable",
                "reset",
                "eof",
                "broken pipe",
                "502",
                "503",
                "504",
                "529",
            ]
        ):
            return FailoverReason.TRANSIENT

        return FailoverReason.UNKNOWN

    @abstractmethod
    async def chat(self, request: LLMRequest) -> LLMResponse:
        """
        Send a chat request.

        Args:
            request: unified request format

        Returns:
            unified response format
        """
        pass

    @abstractmethod
    async def chat_stream(self, request: LLMRequest) -> AsyncIterator[dict]:
        """
        Stream a chat request.

        Args:
            request: unified request format

        Yields:
            streaming events
        """
        pass

    async def health_check(self, dry_run: bool = False) -> bool:
        """
        Health check.

        Default implementation: send a simple request to test the connection.

        Args:
            dry_run: if True, only probe connectivity without changing the
                     provider's health/cooldown state. Suitable for manual
                     checks from the desktop UI, avoiding interference with
                     in-flight agent calls.
        """
        try:
            from ..types import Message

            request = LLMRequest(
                messages=[Message(role="user", content="Hi")],
                max_tokens=10,
            )
            await self.chat(request)
            if not dry_run:
                self.mark_healthy()
            return True
        except Exception as e:
            if dry_run:
                # dry_run mode: do not modify state; re-raise so the caller can inspect the error
                raise
            else:
                # Normal mode: mark unhealthy and return False (preserve original behavior)
                self.mark_unhealthy(str(e))
                return False

    @property
    def supports_tools(self) -> bool:
        """Whether tool calls are supported"""
        return self.config.has_capability("tools")

    @property
    def supports_vision(self) -> bool:
        """Whether images are supported"""
        return self.config.has_capability("vision")

    @property
    def supports_video(self) -> bool:
        """Whether video is supported"""
        return self.config.has_capability("video")

    @property
    def supports_thinking(self) -> bool:
        """Whether extended-thinking mode is supported"""
        return self.config.has_capability("thinking")

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} model={self.model}>"
