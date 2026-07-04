"""Tests for :mod:`openakita.runtime.retry_policy`.

Phase 1 commit 3. Asserts ADR-0004's promise:

* retriable exceptions retry;
* non-retriable exceptions raise immediately;
* cancellation always wins over retry;
* exhaustion is signalled with :class:`RetryGaveUp` carrying the last
  underlying exception in ``__cause__``;
* exponential backoff with cap is computed deterministically when
  jitter is disabled (the jitter path is tested separately for
  monotonicity and bounds).
"""

from __future__ import annotations

import asyncio

import pytest

from openakita.runtime.cancel_token import CancellationToken, CancelledByToken
from openakita.runtime.retry_policy import (
    RETRIABLE_EXCEPTION_NAMES,
    RetryAttempt,
    RetryGaveUp,
    RetryPolicy,
    is_retriable_exception,
)

# ---------------------------------------------------------------------------
# Predicate
# ---------------------------------------------------------------------------


def test_taxonomy_includes_expected_classes() -> None:
    """ADR-0004 names these explicitly; track drift here."""
    must_have = {
        "TimeoutError",
        "ConnectionError",
        "httpx.TimeoutException",
        "anthropic.RateLimitError",
        "openai.RateLimitError",
    }
    assert must_have.issubset(RETRIABLE_EXCEPTION_NAMES)


def test_predicate_matches_builtin_timeout() -> None:
    assert is_retriable_exception(TimeoutError("slow")) is True


def test_predicate_rejects_value_error() -> None:
    assert is_retriable_exception(ValueError("bad arg")) is False


def test_predicate_rejects_cancelled_error() -> None:
    """Cancellation is never retriable, even though the class name is
    sometimes seen in logs as 'CancelledError' — that is a deliberate
    contract."""
    assert is_retriable_exception(asyncio.CancelledError()) is False


def test_predicate_rejects_cooperative_cancel() -> None:
    assert is_retriable_exception(CancelledByToken("user")) is False


def test_predicate_matches_qualified_name() -> None:
    """Synthetic exception with a fake qualified name from a provider."""

    class FakeAnthropicRateLimitError(Exception):  # noqa: N801 — synthetic
        pass

    # Force the qualified name to look like the real anthropic one.
    FakeAnthropicRateLimitError.__qualname__ = "RateLimitError"
    FakeAnthropicRateLimitError.__module__ = "anthropic"
    assert is_retriable_exception(FakeAnthropicRateLimitError("429")) is True


# ---------------------------------------------------------------------------
# compute_delay
# ---------------------------------------------------------------------------


def test_compute_delay_first_attempt_is_zero() -> None:
    p = RetryPolicy(jitter=False)
    assert p.compute_delay(1) == 0.0


def test_compute_delay_exponential_no_jitter() -> None:
    p = RetryPolicy(initial_interval=1.0, multiplier=2.0, max_interval=8.0, jitter=False)
    assert p.compute_delay(2) == 1.0
    assert p.compute_delay(3) == 2.0
    assert p.compute_delay(4) == 4.0
    assert p.compute_delay(5) == 8.0
    assert p.compute_delay(6) == 8.0  # capped


def test_compute_delay_jittered_within_bounds() -> None:
    p = RetryPolicy(initial_interval=1.0, multiplier=2.0, max_interval=8.0, jitter=True)
    samples = [p.compute_delay(3) for _ in range(50)]
    # Full jitter: each sample in [0, 2.0]
    assert all(0.0 <= s <= 2.0 for s in samples)


def test_invalid_max_attempts() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(initial_interval=-1)
    with pytest.raises(ValueError):
        RetryPolicy(multiplier=0)


# ---------------------------------------------------------------------------
# run() happy paths
# ---------------------------------------------------------------------------


async def test_run_returns_first_success() -> None:
    p = RetryPolicy(jitter=False, initial_interval=0.0, max_attempts=3)
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        return "ok"

    out = await p.run(op)
    assert out == "ok"
    assert calls["n"] == 1


async def test_run_retries_then_succeeds() -> None:
    p = RetryPolicy(jitter=False, initial_interval=0.0, max_attempts=3)
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("blip")
        return "ok"

    out = await p.run(op)
    assert out == "ok"
    assert calls["n"] == 2


async def test_run_invokes_on_attempt_callback() -> None:
    p = RetryPolicy(jitter=False, initial_interval=0.0, max_attempts=3)
    seen: list[RetryAttempt] = []
    op_calls = {"n": 0}

    async def op() -> str:
        op_calls["n"] += 1
        if op_calls["n"] < 2:
            raise TimeoutError("first try")
        return "ok"

    await p.run(op, on_attempt=lambda a: seen.append(a))
    # on_attempt fires before each call: attempts 1 and 2.
    assert len(seen) == 2
    assert seen[0].attempt == 1
    assert seen[0].error is None
    assert seen[1].attempt == 2
    assert isinstance(seen[1].error, TimeoutError)


# ---------------------------------------------------------------------------
# run() failure paths
# ---------------------------------------------------------------------------


async def test_non_retriable_raises_immediately() -> None:
    p = RetryPolicy(jitter=False, initial_interval=0.0, max_attempts=5)
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        raise ValueError("contract violation")

    with pytest.raises(ValueError):
        await p.run(op)
    assert calls["n"] == 1


async def test_retry_gives_up_after_max_attempts() -> None:
    p = RetryPolicy(jitter=False, initial_interval=0.0, max_attempts=3)

    async def op() -> str:
        raise TimeoutError("always")

    with pytest.raises(RetryGaveUp) as info:
        await p.run(op)
    assert info.value.attempts == 3
    assert isinstance(info.value.__cause__, TimeoutError)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


async def test_cancel_before_first_attempt_raises_cancelled_by_token() -> None:
    token = CancellationToken()
    token.cancel("test")
    p = RetryPolicy(max_attempts=3, initial_interval=0.0, jitter=False)

    async def op() -> str:
        return "should not run"

    with pytest.raises(CancelledByToken):
        await p.run(op, cancel_token=token)


async def test_cancel_during_sleep_aborts_promptly() -> None:
    """A cancel issued while the policy is sleeping between retries must
    take effect inside one poll interval (~100 ms), never the full
    backoff window."""
    p = RetryPolicy(
        max_attempts=4, initial_interval=2.0, multiplier=1.0,
        max_interval=2.0, jitter=False,
    )
    token = CancellationToken()

    async def op() -> str:
        raise TimeoutError("blip")

    async def cancel_after() -> None:
        await asyncio.sleep(0.05)
        token.cancel("user")

    asyncio.create_task(cancel_after())
    with pytest.raises(CancelledByToken):
        await asyncio.wait_for(
            p.run(op, cancel_token=token), timeout=1.0
        )


async def test_asyncio_cancelled_propagates_unchanged() -> None:
    """Test runners may cancel the surrounding task; the policy must
    not swallow that into RetryGaveUp."""
    p = RetryPolicy(max_attempts=3, initial_interval=0.0, jitter=False)

    async def op() -> str:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await p.run(op)
