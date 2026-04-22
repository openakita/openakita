"""Tests for openakita_plugin_sdk.contrib.errors."""

from __future__ import annotations

import pytest

from openakita_plugin_sdk.contrib import ErrorCoach, ErrorPattern, RenderedError


def test_render_status_401_classified_as_invalid_key() -> None:
    coach = ErrorCoach()
    out = coach.render(Exception("Unauthorized"), status=401, raw_message="invalid key")
    assert out.pattern_id == "api_key_invalid"
    assert "API Key" in out.cause_category
    assert out.retryable is False


def test_render_429_is_retryable() -> None:
    coach = ErrorCoach()
    out = coach.render(status=429, raw_message="Too Many Requests")
    assert out.pattern_id == "rate_limit"
    assert out.retryable is True
    assert out.severity == "warning"


def test_render_unknown_falls_back() -> None:
    coach = ErrorCoach()
    out = coach.render(Exception("???"), status=999, raw_message="weird")
    assert out.pattern_id == "_fallback"
    assert out.retryable is True


def test_render_ffmpeg_missing_pattern() -> None:
    coach = ErrorCoach()
    out = coach.render(raw_message="ffmpeg not found in PATH")
    assert out.pattern_id == "ffmpeg_missing"
    assert "ffmpeg" in out.problem.lower()


def test_register_overrides_pattern() -> None:
    coach = ErrorCoach()
    coach.register(ErrorPattern(
        pattern_id="api_key_invalid",
        cause_category="自定义分类",
        problem_template="自定义 problem",
        next_step_template="自定义 next",
        priority=99,
        status_codes=(401,),
    ))
    out = coach.render(status=401)
    assert out.cause_category == "自定义分类"
    assert "自定义 problem" in out.problem


def test_to_dict_serializable() -> None:
    coach = ErrorCoach()
    out = coach.render(status=503, raw_message="Bad Gateway")
    d = out.to_dict()
    assert isinstance(d, dict)
    for key in ("pattern_id", "cause_category", "problem", "next_step", "severity"):
        assert key in d


def test_render_content_moderation_not_retryable() -> None:
    coach = ErrorCoach()
    out = coach.render(
        status=400,
        raw_message="Your prompt violates content policy",
    )
    assert out.pattern_id == "content_moderation"
    assert out.retryable is False


def test_render_timeout_uses_exception_type() -> None:
    coach = ErrorCoach()
    out = coach.render(TimeoutError("read timeout"))
    assert out.pattern_id == "network_timeout"
    assert out.retryable is True


def test_rendered_error_is_dataclass() -> None:
    re = RenderedError(
        pattern_id="x",
        cause_category="c",
        problem="p",
        evidence="e",
        next_step="n",
    )
    assert re.severity == "error"   # default
    assert re.retryable is False
