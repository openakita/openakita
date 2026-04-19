"""IntentVerifier — "verify, not guess" pre-flight (AnyGen pattern).

Before kicking off an expensive job, ask the LLM to summarize what the user
*actually* wants in 1-3 short sentences and surface 0-3 clarifying questions.
The plugin UI shows this summary back to the user — they confirm or edit
*then* we run.

This avoids the most common failure mode for beginners: spending 30 seconds
and 5 cents on a video that completely missed the point.
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


_DEFAULT_SYSTEM_PROMPT = """你是 OpenAkita 的「意图复核员」。用户即将提交一个 AI 生成任务，
你的工作是在花钱之前确认理解正确。

请输出严格的 JSON：

{
  "summary": "用一两句话复述用户真正想要的产物，要用普通人能懂的话",
  "clarifying_questions": ["最多 3 个对结果影响最大的问题，问完就能动手"],
  "confidence": "high | medium | low",
  "risks": ["最多 3 条可能踩雷的点（敏感词/资源不全/期望与价格不匹配等）"]
}

规则：
- 不解释、不寒暄、不要 markdown 包裹，只输出 JSON。
- 如果用户输入已经很清晰且无风险，clarifying_questions 与 risks 都给空数组。
- summary 必须基于用户输入，不要编造细节。
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
                summary=user_input.strip()[:200] or "(空输入)",
                confidence="low",
                risks=["未配置 LLM，跳过意图复核"],
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
                summary=user_input.strip()[:200] or "(空输入)",
                confidence="low",
                risks=[f"意图复核失败: {type(e).__name__}"],
            )

        text = self._extract_text(raw)
        return self._parse(text, fallback=user_input)

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
                    or fallback.strip()[:200] or "(无)",
                clarifying_questions=[
                    str(q).strip() for q in (data.get("clarifying_questions") or []) if q
                ][:3],
                confidence=_norm_confidence(data.get("confidence")),
                risks=[str(r).strip() for r in (data.get("risks") or []) if r][:3],
                raw=text,
            )

        return IntentSummary(
            summary=text.strip()[:200] or fallback.strip()[:200] or "(无)",
            confidence="low",
            risks=["LLM 输出未按 JSON 返回，已退回原始文本"],
            raw=text,
        )


def _norm_confidence(value: Any) -> str:
    s = str(value or "").lower().strip()
    if s in {"high", "medium", "low"}:
        return s
    return "medium"
