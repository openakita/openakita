"""IntentVerifier — "verify, not guess" pre-flight + post-hoc self-eval.

Two complementary checks:

* :meth:`IntentVerifier.verify` — **before** kicking off an expensive job,
  ask the LLM to summarize what the user *actually* wants in 1-3 sentences
  and surface 0-3 clarifying questions.  Avoids the most common beginner
  failure mode: spending 30 seconds and 5 cents on a video that completely
  missed the point.

* :meth:`IntentVerifier.self_eval_loop` — **after** producing output, ask
  a second model whether the output actually delivers what the original
  brief promised (C0.6, modelled on refs/video-use ``SKILL.md:84-93`` —
  *not* on the unstable CutClaw commit ``083e3cb`` which was rolled back).
  Returns an :class:`EvalResult` with a pass/fail verdict and a list of
  delivery gaps.  Plugins use this to drive D2.10 ``verification`` field
  generation and to decide whether to surface a yellow trust badge.

Both checks are **best-effort**: any LLM error is logged and converted
into a low-confidence result, never raised, so the host pipeline never
crashes because the verifier hiccupped.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class IntentSummary:
    """Result of a single ``IntentVerifier.verify()`` call."""

    summary: str                              # 1-3 sentences, user-facing
    clarifying_questions: list[str] = field(default_factory=list)  # 0-3 short questions
    confidence: str = "medium"                # "high" | "medium" | "low"
    risks: list[str] = field(default_factory=list)  # e.g. ["prompt 涉及敏感词，可能被风控"]
    raw: str = ""                             # raw LLM output (debug / fallback)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "clarifying_questions": list(self.clarifying_questions),
            "confidence": self.confidence,
            "risks": list(self.risks),
        }


_DEFAULT_SYSTEM_PROMPT = """You are the Intent Reviewer for OpenAkita. A user is about to submit an AI generation job, and your job is to confirm that you understand their intent correctly before any cost is incurred.

Output strict JSON only:

{
  "summary": "1-2 sentences restating what the user actually wants, in plain language",
  "clarifying_questions": ["up to 3 questions that have the biggest impact on the result — enough to get started"],
  "confidence": "high | medium | low",
  "risks": ["up to 3 potential problem areas (sensitive terms, missing assets, expectation vs. price mismatch, etc.)"]
}

Rules:
- No explanations, no greetings, no markdown wrapper — output JSON only.
- If the user input is already clear and risk-free, return empty arrays for clarifying_questions and risks.
- summary must be based on the user's input — do not invent details.
"""


_DEFAULT_SELF_EVAL_SYSTEM_PROMPT = """You are the Delivery Reviewer for OpenAkita.
Another model has just produced output based on the user's brief. Answer one question:
**Does this output actually deliver what the brief asked for?**

Output strict JSON only:

{
  "passed": true | false,
  "gaps": ["up to 5 specific gaps, phrased so the user can understand them"],
  "suggestions": ["one fix suggestion per gap, same length or shorter than gaps"],
  "confidence": "high | medium | low"
}

Rules:
- Only assess delivery consistency — do not evaluate aesthetics or creativity.
- A gap is something the brief explicitly requested but the output did not provide. Extra content is not a gap.
- No explanations, no greetings, no markdown wrapper — output JSON only.
- If the brief is too vague to judge, set passed=false and confidence="low".
"""


class IntentVerifier:
    """LLM-backed intent verifier.

    The actual LLM call is injected via ``llm_call`` so the SDK has no hard
    dependency on a specific brain implementation.  In Plugin 2.0 you can do::

        brain = api.get_brain()
        async def llm_call(messages, **kwargs):
            return await brain.chat(messages=messages, **kwargs)
        verifier = IntentVerifier(llm_call=llm_call)
    """

    def __init__(
        self,
        *,
        llm_call: Callable[..., Awaitable[Any]] | None = None,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        plugin_specific_context: str = "",
    ) -> None:
        self._llm_call = llm_call
        self._system_prompt = system_prompt
        self._plugin_ctx = plugin_specific_context.strip()

    def with_context(self, plugin_specific_context: str) -> IntentVerifier:
        """Return a verifier instance with extra plugin-specific guidance.

        Example::

            v2 = verifier.with_context("当前插件: highlight-cutter, 仅支持视频文件 mp4/mov.")
        """
        return IntentVerifier(
            llm_call=self._llm_call,
            system_prompt=self._system_prompt,
            plugin_specific_context=plugin_specific_context,
        )

    async def verify(
        self,
        user_input: str,
        *,
        attachments_summary: str = "",
        max_tokens: int = 500,
    ) -> IntentSummary:
        """Ask the LLM to verify user intent.  Falls back to ``user_input``
        on any failure — never raises (intent verification is best-effort)."""
        if not self._llm_call:
            return IntentSummary(
                summary=user_input.strip()[:200] or "(empty input)",
                confidence="low",
                risks=["No LLM configured — intent review skipped"],
            )

        ctx_block = ""
        if self._plugin_ctx:
            ctx_block = f"\n\n## 插件上下文\n{self._plugin_ctx}"
        if attachments_summary:
            ctx_block += f"\n\n## 用户素材\n{attachments_summary}"

        messages = [
            {"role": "system", "content": self._system_prompt + ctx_block},
            {"role": "user", "content": user_input or ""},
        ]

        try:
            raw = await self._llm_call(messages=messages, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning("IntentVerifier LLM call failed: %s", e)
            return IntentSummary(
                summary=user_input.strip()[:200] or "(empty input)",
                confidence="low",
                risks=[f"Intent review failed: {type(e).__name__}"],
            )

        text = self._extract_text(raw)
        return self._parse(text, fallback=user_input)

    async def self_eval_loop(
        self,
        *,
        original_brief: str,
        produced_output: str,
        max_tokens: int = 500,
        system_prompt: str = _DEFAULT_SELF_EVAL_SYSTEM_PROMPT,
    ) -> EvalResult:
        """C0.6 — post-hoc delivery consistency check.

        Modelled on refs/video-use ``SKILL.md:84-93`` ("ship → re-read brief
        → list gaps").  This is *not* the unstable CutClaw commit
        ``083e3cb`` flow which was rolled back.

        Returns a fail-safe ``EvalResult``: any LLM error or non-JSON
        response yields ``passed=False`` with a low-confidence note,
        never raises.  Plugins use the result to:

        * decide between a green / yellow trust badge
          (D2.10 ``Verification`` envelope),
        * surface a "复核器发现 N 条缺漏" toast in the UI,
        * trigger an automatic re-run loop (capped by the agent loop
          config — see ``AgentLoopConfig.max_iterations``).

        Args:
            original_brief: User's request (or the
                :class:`IntentSummary.summary` from the pre-flight check).
            produced_output: The plugin's actual delivery — text, JSON
                string, or any string-coercible artefact summary.
            max_tokens: Upper bound for the verifier's response tokens.
            system_prompt: Override only for tests / locale tweaks; the
                default is intentionally tightly worded to keep the JSON
                clean.
        """
        if not self._llm_call:
            return EvalResult(
                passed=False,
                gaps=[],
                confidence="low",
                raw="(no llm_call configured — self-eval skipped)",
            )

        ctx_block = ""
        if self._plugin_ctx:
            ctx_block = f"\n\n## 插件上下文\n{self._plugin_ctx}"

        user_block = (
            "## Original user brief\n"
            f"{original_brief or '(empty)'}\n\n"
            "## Model output\n"
            f"{produced_output or '(empty)'}"
        )

        messages = [
            {"role": "system", "content": system_prompt + ctx_block},
            {"role": "user", "content": user_block},
        ]

        try:
            raw = await self._llm_call(messages=messages, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning("IntentVerifier.self_eval_loop LLM call failed: %s", e)
            return EvalResult(
                passed=False,
                gaps=[f"Reviewer call failed: {type(e).__name__}"],
                confidence="low",
                raw="",
            )

        text = self._extract_text(raw)
        return _parse_eval(text)

    @staticmethod
    def _extract_text(raw: Any) -> str:
        """Best-effort text extraction across brain backends."""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            for k in ("content", "text", "output", "message"):
                v = raw.get(k)
                if isinstance(v, str):
                    return v
                if isinstance(v, dict):
                    inner = v.get("content") or v.get("text")
                    if isinstance(inner, str):
                        return inner
            choices = raw.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(msg, dict):
                    c = msg.get("content")
                    if isinstance(c, str):
                        return c
        return str(raw or "")

    @staticmethod
    def _parse(text: str, *, fallback: str) -> IntentSummary:
        """Robust JSON extraction — try direct, then code-fenced, then regex."""
        candidates = [text]
        # ```json ... ``` blocks
        for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
            candidates.append(m.group(1))
        # First {...} balanced-ish block
        m2 = re.search(r"\{.*\}", text, re.DOTALL)
        if m2:
            candidates.append(m2.group(0))

        for c in candidates:
            try:
                data = json.loads(c)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            return IntentSummary(
                summary=str(data.get("summary") or "").strip()
                    or fallback.strip()[:200] or "(none)",
                clarifying_questions=[
                    str(q).strip() for q in (data.get("clarifying_questions") or []) if q
                ][:3],
                confidence=_norm_confidence(data.get("confidence")),
                risks=[str(r).strip() for r in (data.get("risks") or []) if r][:3],
                raw=text,
            )

        return IntentSummary(
            summary=text.strip()[:200] or fallback.strip()[:200] or "(none)",
            confidence="low",
            risks=["LLM did not return valid JSON — falling back to raw text"],
            raw=text,
        )


def _norm_confidence(value: Any) -> str:
    s = str(value or "").lower().strip()
    if s in {"high", "medium", "low"}:
        return s
    return "medium"


# ── self-eval (C0.6) ─────────────────────────────────────────────────


@dataclass
class EvalResult:
    """Outcome of :meth:`IntentVerifier.self_eval_loop`.

    Attributes:
        passed: ``True`` when the second model judged the output a
            faithful delivery of the original brief.  ``False`` when any
            gap was found OR the verifier failed (fail-safe — never
            silently passes a verifier crash).
        gaps: Concrete delivery gaps the verifier flagged.  Capped at 5
            so the UI never has to paginate.
        suggestions: Short remediation hints, paired index-wise with
            ``gaps`` when the verifier produced them.
        confidence: ``"high" | "medium" | "low"`` — same scale as
            :class:`IntentSummary.confidence`.
        raw: Raw verifier text for debug / fallback display.
    """

    passed: bool
    gaps: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence: str = "medium"
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "gaps": list(self.gaps),
            "suggestions": list(self.suggestions),
            "confidence": self.confidence,
        }


def _parse_eval(text: str) -> EvalResult:
    """Robust JSON extraction for the self-eval verifier output."""
    candidates = [text]
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        candidates.append(m.group(1))
    m2 = re.search(r"\{.*\}", text, re.DOTALL)
    if m2:
        candidates.append(m2.group(0))

    for c in candidates:
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        gaps = [str(g).strip() for g in (data.get("gaps") or []) if g][:5]
        suggestions = [
            str(s).strip() for s in (data.get("suggestions") or []) if s
        ][: len(gaps) or 5]
        passed = bool(data.get("passed", False)) and not gaps
        return EvalResult(
            passed=passed,
            gaps=gaps,
            suggestions=suggestions,
            confidence=_norm_confidence(data.get("confidence")),
            raw=text,
        )

    return EvalResult(
        passed=False,
        gaps=["Reviewer did not return a valid JSON verdict — falling back to raw text"],
        confidence="low",
        raw=text,
    )
