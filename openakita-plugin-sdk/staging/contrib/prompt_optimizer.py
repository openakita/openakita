"""PromptOptimizer — generic LLM-backed prompt refinement for media plugins.

Generalized from ``plugins/seedance-video/prompt_optimizer.py``.  Plugins
provide:

- a **system prompt** (vendor-specific style guide), and
- an **input formatter** (turn user fields into a single user message).

Levels (inspired by Seedance's three-tier optimization):

- ``"basic"``         — light cleanup, fix typos, clarify subject
- ``"professional"``  — add cinematography / lighting / camera details
- ``"creative"``      — also propose alternative angles / hooks

The actual LLM call is injected via ``llm_call`` so SDK has no hard dep.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OptimizedPrompt:
    """Result of one ``optimize()`` call."""

    optimized: str       # final prompt to feed the vendor
    original: str        # the user's original input
    level: str           # echoed level
    rationale: str = ""  # short why-this-changed explanation
    raw: str = ""        # raw LLM output (debug)

    def to_dict(self) -> dict[str, Any]:
        return {
            "optimized": self.optimized,
            "original": self.original,
            "level": self.level,
            "rationale": self.rationale,
        }


class PromptOptimizeError(RuntimeError):
    """Raised when LLM call returns nothing usable."""


_DEFAULT_LEVELS = ("basic", "professional", "creative")


class PromptOptimizer:
    """Vendor-agnostic prompt optimizer.

    Example::

        opt = PromptOptimizer(
            llm_call=brain_call,
            system_prompt="你是 Seedance 提示词专家，按 [风格]+[镜头]+[氛围] 输出...",
            levels=("basic", "professional", "creative"),
        )
        result = await opt.optimize("一只柯基在草地", level="professional")
    """

    def __init__(
        self,
        *,
        llm_call: Callable[..., Awaitable[Any]],
        system_prompt: str,
        levels: tuple[str, ...] = _DEFAULT_LEVELS,
        default_level: str = "professional",
    ) -> None:
        self._llm_call = llm_call
        self._system_prompt = system_prompt
        self._levels = tuple(levels)
        self._default_level = default_level if default_level in levels else levels[0]

    @property
    def levels(self) -> tuple[str, ...]:
        return self._levels

    async def optimize(
        self,
        user_input: str,
        *,
        level: str | None = None,
        extra_context: str = "",
        max_tokens: int = 800,
    ) -> OptimizedPrompt:
        """Refine ``user_input`` with the LLM.

        Raises :class:`PromptOptimizeError` if the LLM returns nothing.
        """
        if not user_input or not user_input.strip():
            raise PromptOptimizeError("user_input is empty")

        lvl = level if level in self._levels else self._default_level

        system = self._system_prompt
        if extra_context:
            system = system + "\n\n## 额外上下文\n" + extra_context.strip()
        system = system + f"\n\n## 优化级别\n{lvl}"

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_input.strip()},
        ]
        try:
            raw = await self._llm_call(messages=messages, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001
            raise PromptOptimizeError(f"LLM call failed: {e}") from e

        text = self._extract_text(raw).strip()
        if not text:
            raise PromptOptimizeError("LLM returned empty text")

        optimized, rationale = self._split_rationale(text)
        return OptimizedPrompt(
            optimized=optimized,
            original=user_input,
            level=lvl,
            rationale=rationale,
            raw=text,
        )

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
    def _split_rationale(text: str) -> tuple[str, str]:
        """Detect the common pattern: optimized prompt followed by ``# rationale``."""
        m = re.split(r"\n+(?:#+\s*)?(?:rationale|说明|理由|解释)[:：]?\s*\n+", text, maxsplit=1, flags=re.IGNORECASE)
        if len(m) == 2:
            return m[0].strip(), m[1].strip()
        return text.strip(), ""
