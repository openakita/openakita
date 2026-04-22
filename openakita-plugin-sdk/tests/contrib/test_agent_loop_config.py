"""Tests for openakita_plugin_sdk.contrib.agent_loop_config (C0.5).

The dataclass replaces CutClaw's ``getattr(config, "...", default)``
anti-pattern.  These tests pin the explicit defaults *and* the
fail-closed validation rules so the next maintainer cannot regress them
silently.
"""

from __future__ import annotations

import pytest

from openakita_plugin_sdk.contrib import (
    DEFAULT_AGENT_LOOP_CONFIG,
    DEFAULT_CONTEXT_OVERFLOW_MARKERS,
    DEFAULT_RETRY_STATUS_CODES,
    AgentLoopConfig,
)


# ── defaults / construction ───────────────────────────────────────────


def test_default_max_scenes_in_history_matches_cutclaw() -> None:
    """C0.5: CutClaw used 8 as the implicit default — pin it explicitly
    so a future bump shows up in a diff, not as a silent behaviour drift."""
    assert AgentLoopConfig().max_scenes_in_history == 8


def test_default_retry_codes_exclude_4xx_except_408_429() -> None:
    """N1.2 invariant: 4xx is structurally wrong (auth, moderation,
    validation) — never retry except 408 (request timeout) and 429
    (rate-limit)."""
    cfg = AgentLoopConfig()
    fourxx_in_list = [c for c in cfg.retry_status_codes if 400 <= c < 500]
    assert sorted(fourxx_in_list) == [408, 425, 429]
    assert all(c not in cfg.retry_status_codes for c in (400, 401, 403, 404, 422))


def test_module_level_default_is_a_singleton_view() -> None:
    """Callers without an opinion import ``DEFAULT_AGENT_LOOP_CONFIG``
    instead of building a fresh one — this guards against accidental
    rebinding to a different default."""
    assert DEFAULT_AGENT_LOOP_CONFIG == AgentLoopConfig()


def test_module_default_constants_are_tuples_not_lists() -> None:
    """Tuples are immutable — a stray ``.append`` cannot silently
    poison every loop in the process."""
    assert isinstance(DEFAULT_CONTEXT_OVERFLOW_MARKERS, tuple)
    assert isinstance(DEFAULT_RETRY_STATUS_CODES, tuple)


# ── frozen behaviour ──────────────────────────────────────────────────


def test_config_is_frozen() -> None:
    """A loop must not mutate its own config mid-run — a real CutClaw
    bug we're avoiding by construction."""
    cfg = AgentLoopConfig()
    with pytest.raises(Exception):  # FrozenInstanceError subclasses AttributeError
        cfg.max_iterations = 99  # type: ignore[misc]


# ── __post_init__ validation ──────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs, msg",
    [
        ({"max_scenes_in_history": 0}, "max_scenes_in_history"),
        ({"max_iterations": 0}, "max_iterations"),
        ({"max_consecutive_tool_failures": 0}, "max_consecutive_tool_failures"),
        ({"request_timeout_sec": 0}, "request_timeout_sec"),
        ({"request_timeout_sec": -1.0}, "request_timeout_sec"),
        ({"finish_prompt_after_iters": 0}, "finish_prompt_after_iters"),
    ],
)
def test_validation_rejects_non_positive_values(kwargs, msg) -> None:
    """N1.4 invariant: every numeric knob has a hard lower bound — pass
    zero / negative and the dataclass refuses to construct."""
    with pytest.raises(ValueError, match=msg):
        AgentLoopConfig(**kwargs)


# ── helper methods ────────────────────────────────────────────────────


def test_is_retryable_status_uses_allow_list() -> None:
    cfg = AgentLoopConfig()
    assert cfg.is_retryable_status(429) is True
    assert cfg.is_retryable_status(503) is True
    assert cfg.is_retryable_status(403) is False  # auth — never retry
    assert cfg.is_retryable_status(422) is False  # validation — never retry


def test_is_retryable_status_respects_custom_allow_list() -> None:
    """A plugin that wants to retry only on rate-limit (e.g. a strict
    one-shot moderation API) can shrink the list — and 5xx becomes
    fail-fast."""
    cfg = AgentLoopConfig(retry_status_codes=(429,))
    assert cfg.is_retryable_status(429) is True
    assert cfg.is_retryable_status(500) is False


def test_is_context_overflow_is_case_insensitive() -> None:
    cfg = AgentLoopConfig()
    assert cfg.is_context_overflow("ERROR: Context length exceeded for model") is True
    assert cfg.is_context_overflow("max_tokens limit hit") is True


def test_is_context_overflow_returns_false_for_empty_message() -> None:
    """A missing exception body must NEVER trigger a restart — D2.9
    says only an explicit overflow marker counts."""
    assert AgentLoopConfig().is_context_overflow("") is False
    assert AgentLoopConfig().is_context_overflow("connection refused") is False


def test_should_inject_finish_prompt_threshold() -> None:
    cfg = AgentLoopConfig(finish_prompt_after_iters=15)
    assert cfg.should_inject_finish_prompt(14) is False
    assert cfg.should_inject_finish_prompt(15) is True
    assert cfg.should_inject_finish_prompt(20) is True


def test_should_inject_finish_prompt_disabled_when_prompt_empty() -> None:
    """Empty ``finish_prompt`` opts the plugin out of the nudge — even
    after threshold the loop must not inject an empty user message."""
    cfg = AgentLoopConfig(finish_prompt="")
    assert cfg.should_inject_finish_prompt(99) is False


# ── (de)serialization ─────────────────────────────────────────────────


def test_to_dict_converts_tuples_to_lists_for_json() -> None:
    """Tuples are not JSON-native — to_dict must hand back plain lists
    so ``json.dumps`` works without a custom encoder."""
    d = AgentLoopConfig().to_dict()
    assert isinstance(d["context_overflow_markers"], list)
    assert isinstance(d["retry_status_codes"], list)
    assert d["max_scenes_in_history"] == 8


def test_from_dict_round_trip() -> None:
    original = AgentLoopConfig(
        max_scenes_in_history=12,
        retry_status_codes=(429, 500),
        context_overflow_markers=("custom marker",),
    )
    restored = AgentLoopConfig.from_dict(original.to_dict())
    assert restored == original


def test_from_dict_ignores_unknown_keys() -> None:
    """Forward-compat: a host config file written for a newer SDK may
    carry unknown keys — those must be silently dropped rather than
    crashing the loop."""
    cfg = AgentLoopConfig.from_dict({
        "max_iterations": 50,
        "future_knob_we_dont_have_yet": "hi",
    })
    assert cfg.max_iterations == 50


def test_from_dict_coerces_status_codes_to_int() -> None:
    """JSON parsers sometimes hand back strings for numeric fields —
    coerce to int so ``is_retryable_status(500)`` keeps working."""
    cfg = AgentLoopConfig.from_dict({"retry_status_codes": ["429", "500"]})
    assert cfg.retry_status_codes == (429, 500)
    assert cfg.is_retryable_status(429) is True
