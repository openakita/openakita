"""Compiler-LLM circuit breaker extracted from ``core.brain``.

The legacy :class:`openakita.core.brain.Brain` carried a small but
nuanced state machine that kept the *Prompt Compiler* LLM endpoint
out of the request path after repeated failures: a 5-strike count
plus an auth-aware reset window (5 minutes for transient failures,
30 minutes for auth failures so a hot-swap of ``api_key`` does not
require a process restart). The state was sprayed across four
instance fields and three methods, all welded to Brain.

This module lifts that state machine into a small, well-typed class
that the v2 :mod:`openakita.agent.brain` rewrite composes alongside
:class:`runtime.llm.failover.EndpointFailoverView`. Brain delegates
to one of these instances so behaviour stays byte-faithful; tests
can drive the breaker directly without instantiating a real LLM
client.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Keywords whose presence in an error string flips the breaker into
# the long auth-failure cooldown. The list mirrors the legacy
# ``_compiler_on_failure`` literal.
_AUTH_FAILURE_KEYWORDS: tuple[str, ...] = (
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "401",
    "api key",
    "auth_failed",
)


class CompilerCircuitBreaker:
    """State machine guarding the Prompt-Compiler LLM endpoint.

    Three transitions, no I/O:

    * :meth:`on_success` -- success path, resets the strike count and
      closes the breaker if it was open.
    * :meth:`on_failure` -- failure path, increments the strike count;
      opens the breaker either immediately (auth-failure keyword spotted)
      or after :attr:`fail_threshold` consecutive failures.
    * :meth:`is_available` -- read predicate the caller checks before
      attempting a compiler-LLM call. Auto-resets the breaker once the
      cooldown window elapses.

    Cooldowns are monotonic-clock based so testing can inject a
    ``time_fn`` (callable returning ``float`` seconds) to drive
    deterministic expiry without sleeping.
    """

    def __init__(
        self,
        *,
        fail_threshold: int = 5,
        reset_seconds: float = 300.0,
        auth_reset_seconds: float = 1800.0,
        time_fn=time.monotonic,
    ) -> None:
        self.fail_threshold = fail_threshold
        self.reset_seconds = reset_seconds
        self.auth_reset_seconds = auth_reset_seconds
        self._time_fn = time_fn
        # Mutable state.
        self.fail_count: int = 0
        self.circuit_open: bool = False
        self.circuit_open_at: float = 0.0
        self.auth_failed: bool = False

    # ---- Predicates -----------------------------------------------------

    def is_available(self) -> bool:
        """Return True when a compiler call may proceed."""
        if not self.circuit_open:
            return True
        elapsed = self._time_fn() - self.circuit_open_at
        cooldown = self.auth_reset_seconds if self.auth_failed else self.reset_seconds
        if elapsed >= cooldown:
            # Auto-reset: half-open the breaker for the next request.
            self.circuit_open = False
            self.fail_count = 0
            self.auth_failed = False
            logger.info("[CompilerCircuitBreaker] cooldown elapsed; closing breaker")
            return True
        return False

    # ---- Transitions ----------------------------------------------------

    def on_success(self) -> None:
        """Record a successful compiler call; resets strikes."""
        self.fail_count = 0
        if self.circuit_open:
            self.circuit_open = False
            logger.info("[CompilerCircuitBreaker] closed (success)")

    def on_failure(self, error_str: str = "") -> None:
        """Record a failed compiler call; may open the breaker."""
        self.fail_count += 1
        is_auth = bool(error_str) and any(
            kw in error_str.lower() for kw in _AUTH_FAILURE_KEYWORDS
        )
        if is_auth:
            self.auth_failed = True
            self.circuit_open = True
            self.circuit_open_at = self._time_fn()
            logger.error(
                "[CompilerCircuitBreaker] OPEN (auth failure); skipping compiler "
                "for %.0fs. Fix the API key in settings to restore.",
                self.auth_reset_seconds,
            )
            return
        if not self.circuit_open and self.fail_count >= self.fail_threshold:
            self.circuit_open = True
            self.circuit_open_at = self._time_fn()
            logger.warning(
                "[CompilerCircuitBreaker] OPEN after %d consecutive failures; "
                "skipping compiler for %.0fs",
                self.fail_count,
                self.reset_seconds,
            )

    def force_reset(self) -> None:
        """Drop all breaker state (used by ``Brain.reload_compiler_client``)."""
        self.circuit_open = False
        self.fail_count = 0
        self.auth_failed = False
        self.circuit_open_at = 0.0


__all__ = ["CompilerCircuitBreaker"]
