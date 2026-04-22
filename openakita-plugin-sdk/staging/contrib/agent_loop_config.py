"""AgentLoopConfig — explicit, type-checked config for agent run-loops.

Why this module exists (C0.5 from
``D:\\OpenAkita_AI_Video\\findings\\_summary_to_plan.md``):

    早期假设："CutClaw ``TRIM_SHOT_MAX_SCENES_IN_HISTORY`` 在 ``config.py`` 定义"
    真实情况：仅 ``getattr(..., 8)`` 隐式默认 8，``config.py`` 未定义
    影响：OpenAkita 必须显式写到配置

CutClaw shipped a ``getattr(config, "TRIM_SHOT_MAX_SCENES_IN_HISTORY", 8)``
sprinkled across the loop file with **no** explicit definition anywhere.
This is a documented anti-pattern: changing the constant requires
``grep`` to find every call site, default values silently drift between
modules, and there is no single source of truth a maintainer can read.

This module is the explicit single source of truth for OpenAkita's
agent loops.  Every loop module in the host (and in plugins that ship
their own mini-loops) imports the dataclass instead of using
``getattr`` with magic defaults.

Knobs covered (all named after their CutClaw analogue where one exists):

* ``max_scenes_in_history`` — D2.9 / C0.5: the CutClaw constant.
* ``max_iterations`` — hard ceiling on tool-call rounds per task.
* ``max_consecutive_tool_failures`` — circuit breaker.
* ``context_overflow_markers`` — D2.9: substring triggers in API
  responses that mean "restart the conversation, the model lost track."
* ``retry_status_codes`` — N1.2: 429/5xx allow-list, 4xx is **never**
  retried (avoids the CutClaw mistake of retrying content-moderation
  rejections four times).
* ``finish_prompt`` / ``use_tool_prompt`` — P3.3 catch-all prompts the
  loop injects after N rounds without commit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Default markers that mean "model context overflowed, restart" — sourced
# from CutClaw ``src/core.py:1007-1037`` (D2.9).  Sub-string match,
# case-insensitive at use site.
DEFAULT_CONTEXT_OVERFLOW_MARKERS: tuple[str, ...] = (
    "context length",
    "context_length_exceeded",
    "too large",
    "max_tokens",
    "maximum context",
    "context window",
)

# N1.2 — the only HTTP status codes we ever retry.  4xx (except 408/429)
# is never retried because the request is structurally wrong (auth,
# moderation, validation) and retrying would burn quota or accelerate a
# rate-limit ban.  503 explicitly included for upstream maintenance.
DEFAULT_RETRY_STATUS_CODES: tuple[int, ...] = (
    408, 425, 429, 500, 502, 503, 504,
)


@dataclass(frozen=True)
class AgentLoopConfig:
    """Frozen config bundle passed to every agent loop run.

    All fields have sane defaults so a caller may construct
    ``AgentLoopConfig()`` and tweak only what they need.  ``frozen=True``
    so the loop cannot mutate the config mid-run (a real CutClaw bug
    we're avoiding by construction).

    Attributes:
        max_scenes_in_history: Window size for the trim-shot stage's
            scene history.  CutClaw used 8; we keep that as the default
            but make it a dial.  Setting too low degrades coherence;
            setting too high blows the context window (see
            ``context_overflow_markers``).
        max_iterations: Absolute ceiling on tool-call rounds.  When hit
            the loop terminates and surfaces "loop exceeded N rounds —
            review the trace" so a runaway agent cannot drain budget.
        max_consecutive_tool_failures: Circuit breaker — after N
            consecutive tool errors the loop stops and asks for human
            help instead of spamming the same broken call.
        context_overflow_markers: Sub-strings checked against the API
            response body / exception message.  When a marker matches
            the loop sets ``should_restart=True`` and replays the
            conversation summary in the next turn (D2.9).
        retry_status_codes: HTTP statuses eligible for retry.  Anything
            outside this tuple is fatal-on-first-occurrence (N1.2).
        finish_prompt: Prompt injected when the loop has run for
            ``finish_prompt_after_iters`` rounds without a commit call
            (CutClaw P3.3 ``EDITOR_FINISH_PROMPT``).  Empty string
            disables.
        finish_prompt_after_iters: Round threshold for ``finish_prompt``.
        use_tool_prompt: Prompt injected when the model returns prose
            text instead of a ``tool_calls`` payload (CutClaw P3.3
            ``EDITOR_USE_TOOL_PROMPT``).  Empty string disables.
        request_timeout_sec: Per-request HTTP timeout.  N1.4 says every
            outbound call MUST have a timeout — never None.
    """

    max_scenes_in_history: int = 8
    max_iterations: int = 25
    max_consecutive_tool_failures: int = 3
    context_overflow_markers: tuple[str, ...] = DEFAULT_CONTEXT_OVERFLOW_MARKERS
    retry_status_codes: tuple[int, ...] = DEFAULT_RETRY_STATUS_CODES
    finish_prompt: str = (
        "Please call the `commit` function to finish the task."
    )
    finish_prompt_after_iters: int = 15
    use_tool_prompt: str = (
        "You must call a tool function. Do not output your reasoning as "
        "text — use the tool_calls format."
    )
    request_timeout_sec: float = 60.0

    def __post_init__(self) -> None:
        if self.max_scenes_in_history < 1:
            raise ValueError(
                f"max_scenes_in_history must be >= 1, got {self.max_scenes_in_history}"
            )
        if self.max_iterations < 1:
            raise ValueError(
                f"max_iterations must be >= 1, got {self.max_iterations}"
            )
        if self.max_consecutive_tool_failures < 1:
            raise ValueError(
                "max_consecutive_tool_failures must be >= 1, got "
                f"{self.max_consecutive_tool_failures}"
            )
        if self.request_timeout_sec <= 0:
            raise ValueError(
                "request_timeout_sec must be > 0 — N1.4 forbids "
                "unbounded waits"
            )
        if self.finish_prompt_after_iters < 1:
            raise ValueError(
                "finish_prompt_after_iters must be >= 1, got "
                f"{self.finish_prompt_after_iters}"
            )

    def is_retryable_status(self, status_code: int) -> bool:
        """Return True only if ``status_code`` is in the retry allow-list.

        N1.2 invariant: NEVER call this with a "default True" fallback.
        Outside the allow-list = fail closed.
        """
        return status_code in self.retry_status_codes

    def is_context_overflow(self, message: str) -> bool:
        """Return True when any of the configured markers appears in
        ``message`` (case-insensitive).  Empty messages always return
        False so a missing exception body never silently triggers a
        restart loop."""
        if not message:
            return False
        haystack = message.lower()
        return any(m.lower() in haystack for m in self.context_overflow_markers)

    def should_inject_finish_prompt(self, iteration: int) -> bool:
        """True when the loop has run ``finish_prompt_after_iters`` or
        more rounds (counting from 1) and a finish prompt is configured."""
        if not self.finish_prompt:
            return False
        return iteration >= self.finish_prompt_after_iters

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe view — keeps tuples as lists for serialization."""
        d = asdict(self)
        d["context_overflow_markers"] = list(self.context_overflow_markers)
        d["retry_status_codes"] = list(self.retry_status_codes)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentLoopConfig:
        """Inverse of :meth:`to_dict` — accepts tuples or lists."""
        kwargs: dict[str, Any] = {}
        for key in (
            "max_scenes_in_history",
            "max_iterations",
            "max_consecutive_tool_failures",
            "finish_prompt",
            "finish_prompt_after_iters",
            "use_tool_prompt",
            "request_timeout_sec",
        ):
            if key in data:
                kwargs[key] = data[key]
        if "context_overflow_markers" in data:
            kwargs["context_overflow_markers"] = tuple(
                data["context_overflow_markers"]
            )
        if "retry_status_codes" in data:
            kwargs["retry_status_codes"] = tuple(
                int(c) for c in data["retry_status_codes"]
            )
        return cls(**kwargs)


# Module-level default — import this when a caller has no opinion.
DEFAULT_AGENT_LOOP_CONFIG = AgentLoopConfig()


__all__ = [
    "AgentLoopConfig",
    "DEFAULT_AGENT_LOOP_CONFIG",
    "DEFAULT_CONTEXT_OVERFLOW_MARKERS",
    "DEFAULT_RETRY_STATUS_CODES",
]
