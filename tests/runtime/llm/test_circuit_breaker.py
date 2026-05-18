"""Tests for :class:`openakita.runtime.llm.circuit_breaker.CompilerCircuitBreaker`.

The breaker is pure-state: a fake monotonic clock (``_FakeClock``)
drives deterministic cooldown expiry, no real sleep. Coverage hits
the three documented transitions:

* **happy** -- start closed, success keeps it closed.
* **strikes-open** -- N consecutive failures open the breaker; the
  cooldown auto-resets it once elapsed.
* **auth-open** -- a single error string matching the auth keywords
  trips the breaker immediately with the longer auth cooldown.
"""

from __future__ import annotations

import pytest

from openakita.runtime.llm import CompilerCircuitBreaker


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make(threshold: int = 5, reset: float = 300.0, auth_reset: float = 1800.0):
    clock = _FakeClock()
    breaker = CompilerCircuitBreaker(
        fail_threshold=threshold,
        reset_seconds=reset,
        auth_reset_seconds=auth_reset,
        time_fn=clock,
    )
    return breaker, clock


def test_happy_starts_closed_and_success_keeps_closed() -> None:
    breaker, _ = _make()
    assert breaker.is_available() is True
    breaker.on_success()
    assert breaker.is_available() is True
    assert breaker.fail_count == 0
    assert breaker.circuit_open is False


def test_strikes_open_and_cooldown_auto_resets() -> None:
    breaker, clock = _make(threshold=3, reset=60.0)
    # 2 failures: still closed.
    breaker.on_failure("transient")
    breaker.on_failure("transient")
    assert breaker.is_available() is True
    assert breaker.fail_count == 2
    # 3rd failure trips the breaker.
    breaker.on_failure("transient")
    assert breaker.circuit_open is True
    assert breaker.is_available() is False
    # Before cooldown elapses, still open.
    clock.advance(30.0)
    assert breaker.is_available() is False
    # After cooldown, auto-reset.
    clock.advance(31.0)
    assert breaker.is_available() is True
    assert breaker.circuit_open is False
    assert breaker.fail_count == 0


def test_auth_failure_trips_immediately_with_long_cooldown() -> None:
    breaker, clock = _make(threshold=5, reset=60.0, auth_reset=600.0)
    breaker.on_failure("HTTP 401 invalid_api_key returned")
    assert breaker.circuit_open is True
    assert breaker.auth_failed is True
    assert breaker.is_available() is False
    # The shorter (transient) cooldown does NOT apply.
    clock.advance(120.0)
    assert breaker.is_available() is False
    # Auth cooldown does.
    clock.advance(500.0)
    assert breaker.is_available() is True
    assert breaker.auth_failed is False


def test_success_after_open_closes_breaker() -> None:
    breaker, _ = _make(threshold=2)
    breaker.on_failure("x")
    breaker.on_failure("x")
    assert breaker.circuit_open is True
    breaker.on_success()
    assert breaker.circuit_open is False
    assert breaker.fail_count == 0


def test_force_reset_clears_state() -> None:
    breaker, _ = _make(threshold=2)
    breaker.on_failure("y")
    breaker.on_failure("y")
    breaker.on_failure("401 unauthorized")
    assert breaker.circuit_open is True
    breaker.force_reset()
    assert breaker.is_available() is True
    assert breaker.fail_count == 0
    assert breaker.auth_failed is False


@pytest.mark.parametrize(
    "msg",
    [
        "invalid_api_key",
        "AUTHENTICATION_FAILED",
        "the request was unauthorized",
        "HTTP 401 Unauthorized",
        "no api key configured",
        "auth_failed_internal",
    ],
)
def test_each_auth_keyword_classifies_as_auth(msg: str) -> None:
    breaker, _ = _make()
    breaker.on_failure(msg)
    assert breaker.auth_failed is True
