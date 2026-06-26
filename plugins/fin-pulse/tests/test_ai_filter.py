"""Phase 3 — two-stage AI filter (host Brain) + prompts.

Uses a lightweight ``_StubBrain`` double so the tests stay hermetic —
the host ``LLMClient`` lives behind an awaitable ``chat(messages=...)``
method, which is trivial to fake.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from finpulse_ai.filter import (
    extract_tags,
    interests_digest,
    score_batch,
)
from finpulse_ai.prompts import (
    SCORE_SYSTEM_EN,
    SCORE_SYSTEM_ZH,
    SCORE_USER_TEMPLATE,
    TAG_EXTRACTION_SYSTEM_ZH,
    build_score_items_block,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@dataclass
class _BrainResponse:
    content: str


class _StubBrain:
    """Async Brain double — queue scripted responses.

    ``replies`` is consumed in order; ``exceptions`` mapping the 0-based
    call index to a raised exception lets us simulate failures. The
    recorder exposes ``calls`` so tests can assert the system prompt,
    messages, and kwargs landed correctly.
    """

    def __init__(
        self,
        *,
        replies: list[str] | None = None,
        exceptions: dict[int, Exception] | None = None,
    ) -> None:
        self.replies: list[str] = list(replies or [])
        self.exceptions: dict[int, Exception] = dict(exceptions or {})
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self, *, messages: list[dict[str, str]], system: str, **kw: Any
    ) -> _BrainResponse:
        idx = len(self.calls)
        self.calls.append({"messages": messages, "system": system, **kw})
        if idx in self.exceptions:
            raise self.exceptions[idx]
        body = self.replies.pop(0) if self.replies else "{}"
        return _BrainResponse(content=body)


# ── interests_digest ───────────────────────────────────────────────────


class TestInterestsDigest:
    def test_empty_is_stable(self) -> None:
        assert interests_digest("") == interests_digest("")

    def test_differs_on_whitespace(self) -> None:
        a = interests_digest("美联储")
        b = interests_digest("美联储 ")
        assert a != b

    def test_deterministic(self) -> None:
        a = interests_digest("macro + stocks")
        b = interests_digest("macro + stocks")
        assert a == b


# ── extract_tags ───────────────────────────────────────────────────────


class TestExtractTags:
    def test_parses_clean_json(self) -> None:
        brain = _StubBrain(
            replies=[
                json.dumps(
                    {
                        "tags": [
                            {"tag": "央行政策", "description": "央行流动性/利率"},
                            {"tag": "美股财报", "description": "美股头部公司业绩"},
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
        tags = _run(extract_tags(brain, interests="央行、美股财报"))
        assert len(tags) == 2
        assert tags[0]["tag"] == "央行政策"
        # ZH system prompt was selected by default.
        assert brain.calls[0]["system"] == TAG_EXTRACTION_SYSTEM_ZH

    def test_strips_markdown_fence(self) -> None:
        fenced = '```json\n{"tags":[{"tag":"x","description":"d"}]}\n```'
        brain = _StubBrain(replies=[fenced])
        tags = _run(extract_tags(brain, interests="x"))
        assert tags == [{"tag": "x", "description": "d"}]

    def test_empty_interests_skips_call(self) -> None:
        brain = _StubBrain(replies=[])
        tags = _run(extract_tags(brain, interests="   \n"))
        assert tags == []
        assert brain.calls == []

    def test_none_brain_raises(self) -> None:
        with pytest.raises(RuntimeError, match="brain.access"):
            _run(extract_tags(None, interests="x"))

    def test_exception_downgrades_to_empty(self) -> None:
        brain = _StubBrain(replies=[], exceptions={0: RuntimeError("boom")})
        tags = _run(extract_tags(brain, interests="macro"))
        assert tags == []

    def test_malformed_json_downgrades(self) -> None:
        brain = _StubBrain(replies=["not json at all"])
        tags = _run(extract_tags(brain, interests="macro"))
        assert tags == []


# ── score_batch ────────────────────────────────────────────────────────


class TestScoreBatch:
    def _items(self, n: int = 3) -> list[dict[str, Any]]:
        return [
            {
                "id": f"a{i}",
                "title": f"title-{i}",
                "summary": f"summary-{i}",
                "source_id": "wallstreetcn",
            }
            for i in range(n)
        ]

    def test_scores_and_clamps(self) -> None:
        brain = _StubBrain(
            replies=[
                json.dumps(
                    [
                        {"id": "a0", "tag_id": 0, "score": 8.5, "reason": "利率"},
                        {"id": "a1", "tag_id": 1, "score": 15.0, "reason": "oob"},
                        {"id": "a2", "tag_id": 0, "score": -3.0, "reason": "neg"},
                    ]
                )
            ]
        )
        out = _run(
            score_batch(
                brain,
                items=self._items(3),
                tags=[{"tag": "央行政策", "description": ""}],
                batch_size=10,
            )
        )
        scores = {r["id"]: r["score"] for r in out}
        assert scores["a0"] == 8.5
        assert scores["a1"] == 10.0  # clamped upper bound
        assert scores["a2"] == 0.0  # clamped lower bound

    def test_batch_exception_falls_back_to_zero(self) -> None:
        brain = _StubBrain(replies=[], exceptions={0: RuntimeError("rate limited")})
        out = _run(
            score_batch(
                brain,
                items=self._items(2),
                tags=[{"tag": "x", "description": ""}],
                batch_size=10,
            )
        )
        assert all(r["score"] == 0.0 for r in out)
        assert all(r["reason"] == "analysis failed" for r in out)

    def test_missing_ids_filled_with_failure_sentinel(self) -> None:
        # LLM only returns 1/3 ids; the other two must land graceful defaults.
        brain = _StubBrain(
            replies=[json.dumps([{"id": "a0", "tag_id": 0, "score": 7.0, "reason": "x"}])]
        )
        out = _run(
            score_batch(
                brain,
                items=self._items(3),
                tags=[{"tag": "x", "description": ""}],
                batch_size=10,
            )
        )
        byid = {r["id"]: r for r in out}
        assert byid["a0"]["score"] == 7.0
        assert byid["a1"]["score"] == 0.0
        assert byid["a2"]["score"] == 0.0

    def test_multiple_batches(self) -> None:
        # 5 items with batch_size=2 → 3 LLM calls.
        brain = _StubBrain(
            replies=[
                json.dumps(
                    [
                        {"id": "a0", "tag_id": 0, "score": 6.0},
                        {"id": "a1", "tag_id": 0, "score": 7.0},
                    ]
                ),
                json.dumps(
                    [
                        {"id": "a2", "tag_id": 0, "score": 8.0},
                        {"id": "a3", "tag_id": 0, "score": 9.0},
                    ]
                ),
                json.dumps([{"id": "a4", "tag_id": 0, "score": 5.0}]),
            ]
        )
        out = _run(
            score_batch(
                brain,
                items=self._items(5),
                tags=[{"tag": "x", "description": ""}],
                batch_size=2,
            )
        )
        assert len(brain.calls) == 3
        assert [r["score"] for r in out] == [6.0, 7.0, 8.0, 9.0, 5.0]

    def test_none_brain_raises(self) -> None:
        with pytest.raises(RuntimeError, match="brain.access"):
            _run(
                score_batch(
                    None,
                    items=self._items(1),
                    tags=[],
                )
            )

    def test_lang_switch_flips_system_prompt(self) -> None:
        brain = _StubBrain(replies=["[]"])
        _run(
            score_batch(
                brain,
                items=self._items(1),
                tags=[{"tag": "x", "description": ""}],
                lang="en",
            )
        )
        assert brain.calls[0]["system"] == SCORE_SYSTEM_EN

    def test_empty_items_returns_empty(self) -> None:
        brain = _StubBrain()
        out = _run(score_batch(brain, items=[], tags=[]))
        assert out == []


# ── Prompt builders ───────────────────────────────────────────────────


class TestPromptBuilders:
    def test_build_items_block_truncates_summary(self) -> None:
        items = [
            {
                "id": 0,
                "title": "t",
                "summary": "x" * 300,
                "source_id": "cls",
            }
        ]
        block = build_score_items_block(items)
        assert block.startswith("0 [cls]")
        assert block.endswith("...")
        assert len(block) < 280 + 40

    def test_score_template_interpolates(self) -> None:
        prompt = SCORE_USER_TEMPLATE.format(
            tags_json="[{}]",
            items_block="0 [cls] t :: s",
        )
        assert "0 [cls] t :: s" in prompt
        assert '"tag_id"' in prompt
