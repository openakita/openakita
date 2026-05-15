"""happyhorse_long_video — storyboard / concat / chain smoke tests."""

from __future__ import annotations

import pytest
from happyhorse_long_video import (
    STORYBOARD_SYSTEM_PROMPT,
    ChainGenerator,
    decompose_storyboard,
    ffmpeg_available,
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
