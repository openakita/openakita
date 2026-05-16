"""happyhorse_long_video — storyboard / concat / chain smoke tests."""

from __future__ import annotations

import pytest
from happyhorse_long_video import (
    STORYBOARD_SYSTEM_PROMPT,
    ChainGenerator,
    decompose_storyboard,
    ffmpeg_available,
    normalize_transition,
)


def test_storyboard_prompt_mentions_happyhorse():
    """Prompt must say HappyHorse 1.0 / Wan to keep the LLM on-brand."""
    assert "HappyHorse" in STORYBOARD_SYSTEM_PROMPT
    assert "Wan" in STORYBOARD_SYSTEM_PROMPT


def test_ffmpeg_available_returns_bool():
    assert isinstance(ffmpeg_available(), bool)


@pytest.mark.asyncio
async def test_decompose_storyboard_handles_missing_brain():
    """Without a usable brain object the function must return an error
    envelope instead of raising."""
    result = await decompose_storyboard(brain=None, story="x")
    assert result.get("error") or result == {"error": "No LLM available"}


@pytest.mark.asyncio
async def test_decompose_storyboard_parses_fenced_json(monkeypatch):
    class FakeBrain:
        async def chat(self, messages):
            return {
                "content": (
                    "好的。\n```json\n"
                    '{"segments": [{"index": 1, "duration": 5, "prompt": "x"}]}\n'
                    "```"
                )
            }

    result = await decompose_storyboard(
        brain=FakeBrain(), story="测试故事"
    )
    assert "error" not in result
    assert isinstance(result["segments"], list)
    assert result["segments"][0]["index"] == 1


def test_chain_generator_constructor_does_not_raise():
    chain = ChainGenerator(client=None, task_manager=None, chain_group_id="g1")
    assert chain._chain_group_id == "g1"


# ─── Bug 2 regression — transition alias normalization ───────────────


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("fade", "crossfade"),
        ("crossfade", "crossfade"),
        ("CROSSFADE", "crossfade"),
        ("xfade", "crossfade"),
        ("dissolve", "crossfade"),
        ("none", "none"),
        ("cut", "none"),
        ("hard", "none"),
        ("", "none"),
        (None, "none"),
        ("totally-bogus", "none"),
    ],
)
def test_normalize_transition_aliases(alias, expected):
    """Regression: the frontend ships 'fade' and historical agent prompts
    use 'xfade' / 'dissolve'. All of these must drive the crossfade
    branch — previously only the exact string 'crossfade' worked, so the
    UI button silently fell back to a hard cut."""
    assert normalize_transition(alias) == expected
