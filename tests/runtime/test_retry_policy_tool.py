"""Tests for the tool-call retry predicate + default policy (P-RC-4 P4.9)."""

from __future__ import annotations

import asyncio

import pytest

from openakita.runtime.cancel_token import CancelledByToken
from openakita.runtime.retry_policy import (
    RetryGaveUp,
    RetryPolicy,
    default_tool_retry_policy,
    is_retriable_tool_error,
)


def test_tool_skipped_is_not_retriable() -> None:
    class ToolSkipped(Exception):
        pass

    assert is_retriable_tool_error(ToolSkipped("user declined")) is False


def test_permission_error_is_not_retriable() -> None:
    assert is_retriable_tool_error(PermissionError("denied")) is False


def test_file_not_found_is_not_retriable() -> None:
    assert is_retriable_tool_error(FileNotFoundError("missing")) is False


def test_timeout_is_retriable() -> None:
    assert is_retriable_tool_error(TimeoutError("slow")) is True


def test_connection_reset_is_retriable() -> None:
    assert is_retriable_tool_error(ConnectionResetError("oops")) is True


def test_unknown_exception_is_not_retriable() -> None:
    assert is_retriable_tool_error(ValueError("bad arg")) is False


def test_cancelled_by_token_is_never_retriable() -> None:
    assert is_retriable_tool_error(CancelledByToken("/stop")) is False
    assert is_retriable_tool_error(asyncio.CancelledError()) is False


def test_default_tool_retry_policy_shape() -> None:
    p = default_tool_retry_policy()
    assert isinstance(p, RetryPolicy)
    assert p.max_attempts == 3
    assert p.initial_interval == 0.1


@pytest.mark.asyncio
async def test_default_tool_retry_policy_retries_transient_then_gives_up() -> None:
    attempts: list[int] = []

    async def flaky() -> str:
        attempts.append(len(attempts) + 1)
        raise TimeoutError("transient")

    p = RetryPolicy(max_attempts=3, initial_interval=0.0, max_interval=0.0, jitter=False)
    with pytest.raises(RetryGaveUp):
        await p.run(flaky, retry_predicate=is_retriable_tool_error)
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_default_tool_retry_policy_skips_non_retriable_immediately() -> None:
    class ToolSkipped(Exception):
        pass

    attempts: list[int] = []

    async def declined() -> str:
        attempts.append(len(attempts) + 1)
        raise ToolSkipped("user declined")

    p = RetryPolicy(max_attempts=3, initial_interval=0.0, max_interval=0.0, jitter=False)
    with pytest.raises(ToolSkipped):
        await p.run(declined, retry_predicate=is_retriable_tool_error)
    assert len(attempts) == 1
