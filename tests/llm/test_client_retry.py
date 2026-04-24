"""Tests for LLM client error classification and retry logic."""

import pytest

from openakita.llm.client import (
    ErrorCategory,
    classify_error_for_retry,
    should_reduce_max_tokens,
)


def test_402_classified_as_token_limit():
    """402 (insufficient credits) should be classified like 413 (token limit)."""
    category = classify_error_for_retry(status_code=402)

    assert category == ErrorCategory.TOKEN_LIMIT


def test_413_classified_as_token_limit():
    """413 should still be classified as token limit."""
    category = classify_error_for_retry(status_code=413)

    assert category == ErrorCategory.TOKEN_LIMIT


def test_402_triggers_max_tokens_reduction():
    """402 should trigger max_tokens reduction like 413."""
    assert should_reduce_max_tokens(status_code=402) is True
    assert should_reduce_max_tokens(status_code=413) is True
    assert should_reduce_max_tokens(status_code=500) is False


def test_429_classified_as_rate_limit():
    """429 should be classified as rate limit."""
    category = classify_error_for_retry(status_code=429)

    assert category == ErrorCategory.RATE_LIMIT


def test_5xx_classified_as_server_error():
    """5xx errors should be classified as server error."""
    for code in (500, 502, 503, 504, 529):
        category = classify_error_for_retry(status_code=code)
        assert category == ErrorCategory.SERVER_ERROR, f"Expected SERVER_ERROR for {code}"


def test_401_403_classified_as_auth_error():
    """401 and 403 should be classified as auth error."""
    assert classify_error_for_retry(status_code=401) == ErrorCategory.AUTH_ERROR
    assert classify_error_for_retry(status_code=403) == ErrorCategory.AUTH_ERROR


def test_other_4xx_classified_as_client_error():
    """Other 4xx errors should be classified as client error."""
    for code in (400, 404, 405, 422):
        category = classify_error_for_retry(status_code=code)
        assert category == ErrorCategory.CLIENT_ERROR, f"Expected CLIENT_ERROR for {code}"
