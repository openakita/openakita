"""Phase 6b — AI-assisted rule drafting (finpulse_ai.rules_suggest).

The Brain is faked with a lightweight stub so the test is hermetic
(no network I/O). Covers the happy path, empty-description guard,
Brain exception fallback, and ``None`` brain fallback.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from finpulse_ai.rules_suggest import suggest_rules_text


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@dataclass
class _BrainResponse:
    content: str


class _StubBrain:
    def __init__(
        self, *, replies: list[str] | None = None, exceptions: dict[int, Exception] | None = None
    ) -> None:
        self.replies = list(replies or [])
        self.exceptions = dict(exceptions or {})
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self, *, messages: list[dict[str, str]], system: str, **kw: Any
    ) -> _BrainResponse:
        idx = len(self.calls)
        self.calls.append({"messages": messages, "system": system, **kw})
        if idx in self.exceptions:
            raise self.exceptions[idx]
        body = self.replies.pop(0) if self.replies else ""
        return _BrainResponse(content=body)


class TestSuggestRulesHappyPath:
    def test_brain_rules_passthrough(self) -> None:
        brain = _StubBrain(replies=["+美联储\n+降息\n!传闻\n"])
        out = _run(suggest_rules_text(brain, description="美联储政策", lang="zh"))
        assert out["ok"] is True
        assert out["source"] == "brain"
        assert "+美联储" in out["rules_text"]
        assert "!传闻" in out["rules_text"]

    def test_strips_markdown_fence(self) -> None:
        brain = _StubBrain(replies=["```\n+降息\n+加息\n```"])
        out = _run(suggest_rules_text(brain, description="利率", lang="zh"))
        assert out["ok"] is True
        assert out["source"] == "brain"
        assert "```" not in out["rules_text"]
        assert "+降息" in out["rules_text"]

    def test_picks_english_prompt_on_lang_en(self) -> None:
        brain = _StubBrain(replies=["+Fed\n+rate\n"])
        _run(suggest_rules_text(brain, description="Fed policy", lang="en"))
        assert "English" in brain.calls[0]["system"] or "editor" in brain.calls[0]["system"].lower()


class TestSuggestRulesGuardRails:
    def test_empty_description_rejected(self) -> None:
        brain = _StubBrain(replies=[])
        out = _run(suggest_rules_text(brain, description="   \n"))
        assert out["ok"] is False
        assert "description" in out["error"]
        assert brain.calls == []

    def test_truncates_oversized_description(self) -> None:
        long = "Z" * 5000  # single-char sentinel that does NOT appear in the template
        brain = _StubBrain(replies=["+y"])
        out = _run(suggest_rules_text(brain, description=long))
        assert out["ok"] is True
        # user prompt slice should have been cut at _MAX_DESC_CHARS = 2000
        user_content = brain.calls[0]["messages"][0]["content"]
        assert user_content.count("Z") == 2000


class TestSuggestRulesFallback:
    def test_none_brain_falls_back(self) -> None:
        out = _run(suggest_rules_text(None, description="美联储, 降息, 排除传闻"))
        assert out["source"] == "fallback"
        assert "+美联储" in out["rules_text"]
        # "排除传闻" should become "!传闻"
        assert "!传闻" in out["rules_text"]

    def test_brain_exception_falls_back(self) -> None:
        brain = _StubBrain(replies=[], exceptions={0: RuntimeError("boom")})
        out = _run(suggest_rules_text(brain, description="央行, 利率"))
        assert out["source"] == "fallback"
        assert "+央行" in out["rules_text"]
        assert out["message"].startswith("brain error")

    def test_brain_empty_body_falls_back(self) -> None:
        brain = _StubBrain(replies=[""])
        out = _run(suggest_rules_text(brain, description="美联储, 降息"))
        assert out["source"] == "fallback"
        assert out["rules_text"].count("+") >= 2

    def test_all_junk_description_yields_empty_not_ok(self) -> None:
        out = _run(suggest_rules_text(None, description="的 了 在 是"))
        # Heuristic dropped everything as too short / filler -> ok=False
        assert out["source"] == "fallback"
        assert out["ok"] is False or out["rules_text"] == ""
