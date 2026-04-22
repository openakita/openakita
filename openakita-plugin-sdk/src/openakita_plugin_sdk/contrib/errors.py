"""ErrorCoach — translate raw exceptions/error codes into "problem + evidence + next step".

Inspired by:

- CutClaw ``ReviewerAgent`` ("reviewer as a coach")
- AnyGen FAQ tone: every error message has a *cause category* and *actionable suggestion*
- CapCut Web help-center 3-part error layout (Why does it happen / What to do / Tip)

C0.8 — **template, not LLM**: ``ErrorCoach.render()`` is a pure dict-lookup +
``str.format`` call.  It does NOT invoke any model and has zero network
dependencies.  This matters because:

* the host can render an error in <1ms even when the brain is wedged,
* ``D:\\OpenAkita_AI_Video\\findings\\_summary_to_plan.md`` C0.8 explicitly
  flagged the misconception that ``ErrorCoach`` "translates with an LLM",
* a deterministic mapping is auditable — operators can grep the
  ``ErrorPattern`` library and predict every output the user will see.

Pattern library is intentionally a plain dict so plugins can extend at
runtime.  D2.11/D2.14 three-segment shape (cause → problem → next_step,
with optional ``tip``) is enforced by :class:`RenderedError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern
from typing import Any


@dataclass(frozen=True)
class RenderedError:
    """User-facing error rendered through a pattern.

    Attributes:
        pattern_id: Id of the matched ``ErrorPattern`` (or ``"_fallback"``).
        cause_category: One short Chinese label, e.g. ``"网络问题"`` / ``"配额耗尽"``.
        problem: Why does it happen — 1 sentence, user words, no jargon.
        evidence: What we observed — short fact (status code, file name, ...).
        next_step: What to do — concrete clickable / actionable action.
        tip: Optional "📍 Tip" — preventive hint, may be empty.
        severity: ``"info" | "warning" | "error"``.
        retryable: Whether the host UI should show a "重试" button.
    """

    pattern_id: str
    cause_category: str
    problem: str
    evidence: str
    next_step: str
    tip: str = ""
    severity: str = "error"
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "cause_category": self.cause_category,
            "problem": self.problem,
            "evidence": self.evidence,
            "next_step": self.next_step,
            "tip": self.tip,
            "severity": self.severity,
            "retryable": self.retryable,
        }


@dataclass
class ErrorPattern:
    """A single error pattern.

    Matching rules: any of (status_code, exc_type, message_regex) that is set
    must match.  ``priority`` resolves ties (higher wins).

    Templates may use ``{evidence}`` placeholder which receives the matched
    excerpt or status code.
    """

    pattern_id: str
    cause_category: str
    problem_template: str
    next_step_template: str
    tip: str = ""
    severity: str = "error"
    retryable: bool = False
    priority: int = 0

    status_codes: tuple[int, ...] = ()
    exc_types: tuple[str, ...] = ()
    message_regex: Pattern[str] | None = None


def _default_patterns() -> list[ErrorPattern]:
    return [
        ErrorPattern(
            pattern_id="api_key_missing",
            cause_category="API Key not configured",
            problem_template="No API Key has been set for the provider, so requests cannot be sent.",
            next_step_template="Click Settings (top right) → paste your API Key → retry.",
            tip="Configure your API Key once and it will be reused automatically.",
            severity="warning",
            retryable=False,
            priority=20,
            message_regex=re.compile(
                r"(api[\s_-]?key|access[\s_-]?key).*(missing|empty|not\s*set|未配置)",
                re.IGNORECASE,
            ),
        ),
        ErrorPattern(
            pattern_id="api_key_invalid",
            cause_category="API Key invalid",
            problem_template="The provider rejected this key ({evidence}) — it may be incorrect or expired.",
            next_step_template="Check the key in your provider dashboard → re-paste it → save and retry.",
            tip="If you have multiple accounts, make sure you are not using a test key in production.",
            severity="error",
            retryable=False,
            priority=20,
            status_codes=(401, 403),
        ),
        ErrorPattern(
            pattern_id="rate_limit",
            cause_category="Rate limited",
            problem_template="The provider is rate-limiting requests ({evidence}). Please wait a moment.",
            next_step_template="No action needed — the plugin will retry automatically in 10 seconds. If this happens repeatedly, upgrade your plan.",
            tip="You can increase poll_interval in Settings to reduce request frequency.",
            severity="warning",
            retryable=True,
            priority=15,
            status_codes=(429,),
        ),
        ErrorPattern(
            pattern_id="server_error",
            cause_category="Provider outage",
            problem_template="The provider returned a server error ({evidence}). This is not your fault.",
            next_step_template="Click Retry. If it fails 3 times in a row, check the provider's status page for announcements.",
            tip="You can switch to a backup provider in Settings.",
            severity="warning",
            retryable=True,
            priority=10,
            status_codes=(500, 502, 503, 504),
        ),
        ErrorPattern(
            pattern_id="content_moderation",
            cause_category="Content policy violation",
            problem_template="Content moderation failed ({evidence}). Retrying will not help.",
            next_step_template="Edit the sensitive terms in your prompt, or upload different source material.",
            tip="Use Intent Verification to pre-screen your prompt and avoid this failure.",
            severity="error",
            retryable=False,
            priority=18,
            status_codes=(400, 422),
            message_regex=re.compile(
                r"(content[\s_-]?policy|moderation|sensitive|敏感|违规|风控)",
                re.IGNORECASE,
            ),
        ),
        ErrorPattern(
            pattern_id="quota_exhausted",
            cause_category="Quota exhausted",
            problem_template="Your monthly quota is used up ({evidence}).",
            next_step_template="Top up in your provider dashboard → come back and click Retry.",
            tip="Enable cost alerts in Settings to get notified at 80% usage.",
            severity="error",
            retryable=False,
            priority=18,
            message_regex=re.compile(
                r"(quota|insufficient|余额|额度.*不足|over.*limit)",
                re.IGNORECASE,
            ),
        ),
        ErrorPattern(
            pattern_id="network_timeout",
            cause_category="Network timeout",
            problem_template="Request timed out ({evidence}). Your network connection may be unstable.",
            next_step_template="Check your network or switch to a proxy, then click Retry.",
            tip="A proxy is recommended when accessing overseas APIs from China.",
            severity="warning",
            retryable=True,
            priority=12,
            exc_types=("TimeoutError", "ReadTimeout", "ConnectTimeout", "ConnectError"),
            message_regex=re.compile(r"(timeout|timed?\s*out|超时|connection.*reset)", re.IGNORECASE),
        ),
        ErrorPattern(
            pattern_id="ffmpeg_missing",
            cause_category="FFmpeg not installed",
            problem_template="The system cannot find the ffmpeg command ({evidence}). Video processing is unavailable.",
            next_step_template="Download FFmpeg from https://ffmpeg.org/download.html → add it to PATH → restart the app.",
            tip="On Windows, run: winget install Gyan.FFmpeg",
            severity="error",
            retryable=False,
            priority=20,
            message_regex=re.compile(
                r"(ffmpeg|ffprobe).*(not\s*found|missing|找不到)",
                re.IGNORECASE,
            ),
        ),
        ErrorPattern(
            pattern_id="file_not_found",
            cause_category="File missing",
            problem_template="Cannot find the file ({evidence}). It may have been deleted or moved.",
            next_step_template="Re-upload the source material. For a historical task, restore it from the Media Library first.",
            tip="Enabling Auto Backup can reduce this type of issue.",
            severity="error",
            retryable=False,
            priority=18,
            exc_types=("FileNotFoundError",),
        ),
        ErrorPattern(
            pattern_id="task_not_found",
            cause_category="Task not found",
            problem_template="Cannot find task ID ({evidence}). It may have expired or been cleaned up.",
            next_step_template="Refresh the task list and start from the latest entry.",
            severity="warning",
            retryable=False,
            priority=12,
            status_codes=(404,),
        ),
    ]


class ErrorCoach:
    """Translate raw errors into actionable user-facing messages.

    Usage::

        coach = ErrorCoach()  # built-in patterns
        rendered = coach.render(exc, status=503, raw_message="Bad Gateway")

        # Plugin-specific patterns: register more
        coach.register(ErrorPattern(
            pattern_id="seedance_image_unsupported",
            cause_category="模型不支持",
            ...
        ))
    """

    def __init__(self, patterns: list[ErrorPattern] | None = None) -> None:
        self._patterns: list[ErrorPattern] = list(patterns) if patterns else _default_patterns()

    def register(self, pattern: ErrorPattern) -> None:
        """Add or override a pattern (matched by ``pattern_id``)."""
        self._patterns = [p for p in self._patterns if p.pattern_id != pattern.pattern_id]
        self._patterns.append(pattern)

    def patterns(self) -> list[ErrorPattern]:
        """Return current pattern list (copy)."""
        return list(self._patterns)

    def render(
        self,
        exc: BaseException | None = None,
        *,
        status: int | None = None,
        raw_message: str | None = None,
        evidence: str | None = None,
    ) -> RenderedError:
        """Match the best pattern and render a user-facing error.

        At least one of ``exc`` / ``status`` / ``raw_message`` must be given.
        """
        message = raw_message or (str(exc) if exc else "")
        exc_name = type(exc).__name__ if exc else ""
        ev = evidence or self._auto_evidence(status, exc_name, message)

        best: ErrorPattern | None = None
        best_score = -1
        for pat in self._patterns:
            score = self._match_score(pat, status, exc_name, message)
            if score < 0:
                continue
            score += pat.priority
            if score > best_score:
                best, best_score = pat, score

        if best is None:
            return RenderedError(
                pattern_id="_fallback",
                cause_category="Unknown error",
                problem=f"An unexpected error occurred ({ev or 'unknown'}).",
                evidence=ev,
                next_step="Click Retry. If it keeps happening, send us the logs via Settings → Feedback.",
                tip="Error logs are located under data/plugins/<id>/logs/",
                severity="error",
                retryable=True,
            )

        return RenderedError(
            pattern_id=best.pattern_id,
            cause_category=best.cause_category,
            problem=self._fmt(best.problem_template, ev),
            evidence=ev,
            next_step=self._fmt(best.next_step_template, ev),
            tip=best.tip,
            severity=best.severity,
            retryable=best.retryable,
        )

    @staticmethod
    def _fmt(template: str, evidence: str) -> str:
        try:
            return template.format(evidence=evidence or "no details")
        except (KeyError, IndexError):
            return template

    @staticmethod
    def _auto_evidence(status: int | None, exc_name: str, message: str) -> str:
        bits: list[str] = []
        if status is not None:
            bits.append(f"HTTP {status}")
        if exc_name and exc_name not in {"Exception", "BaseException"}:
            bits.append(exc_name)
        if message:
            short = message.strip().splitlines()[0][:120]
            if short:
                bits.append(short)
        return " · ".join(bits)

    @staticmethod
    def _match_score(
        pat: ErrorPattern,
        status: int | None,
        exc_name: str,
        message: str,
    ) -> int:
        criteria_total = 0
        criteria_matched = 0

        if pat.status_codes:
            criteria_total += 1
            if status is not None and status in pat.status_codes:
                criteria_matched += 1
        if pat.exc_types:
            criteria_total += 1
            if exc_name in pat.exc_types:
                criteria_matched += 1
        if pat.message_regex is not None:
            criteria_total += 1
            if message and pat.message_regex.search(message):
                criteria_matched += 1

        if criteria_total == 0:
            return -1
        if criteria_matched == 0:
            return -1
        return criteria_matched
