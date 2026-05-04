"""Phase 2.5 — script_writer.py tests.

The writer wraps the host's ``brain`` interface, so every test stubs
out the brain. We exercise:

- Permission gate (``brain.access`` revoked → fallback path).
- Brain unavailable / not configured → fallback path.
- Brain returns malformed JSON → fallback path.
- Brain returns valid JSON with the wrong panel count → normalised to
  the requested count.
- ``_fallback_panels`` always produces the requested panel count.
- Multi-method dispatch order: ``think_lightweight`` first, then
  ``think``, then ``chat``.
"""

from __future__ import annotations

from typing import Any

import pytest

from script_writer import (
    BrainResult,
    MangaScriptWriter,
    _build_user_prompt,
    _fallback_panels,
    _normalize_storyboard,
)

# ─── Stub host API ─────────────────────────────────────────────────────────


class _StubAPI:
    def __init__(self, *, brain: Any = None, has_perm: bool = True) -> None:
        self._brain = brain
        self._has_perm = has_perm

    def has_permission(self, name: str) -> bool:
        return self._has_perm

    def get_brain(self) -> Any:
        return self._brain


class _LightweightBrain:
    """Brain that exposes ``think_lightweight`` (and falls through if
    the test wants to force think_lightweight to fail)."""

    def __init__(
        self,
        *,
        lightweight_response: str | None = None,
        think_response: str | None = None,
        chat_response: str | None = None,
        lightweight_raises: bool = False,
    ) -> None:
        self.lightweight_response = lightweight_response
        self.think_response = think_response
        self.chat_response = chat_response
        self.lightweight_raises = lightweight_raises
        self.calls: list[str] = []

    async def think_lightweight(self, *, prompt: str, system: str, max_tokens: int) -> str:
        self.calls.append("light")
        if self.lightweight_raises:
            raise RuntimeError("simulated failure")
        return self.lightweight_response or ""

    async def think(self, *, prompt: str, system: str, max_tokens: int) -> str:
        self.calls.append("think")
        return self.think_response or ""

    async def chat(self, *, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.calls.append("chat")
        return {"content": self.chat_response or ""}


# ─── Capability probe ──────────────────────────────────────────────────────


def test_is_available_false_when_no_permission() -> None:
    w = MangaScriptWriter(_StubAPI(brain=object(), has_perm=False))
    assert w.is_available() is False


def test_is_available_false_when_no_brain() -> None:
    w = MangaScriptWriter(_StubAPI(brain=None, has_perm=True))
    assert w.is_available() is False


def test_is_available_true_with_brain_and_permission() -> None:
    w = MangaScriptWriter(_StubAPI(brain=_LightweightBrain(), has_perm=True))
    assert w.is_available() is True


# ─── write_storyboard: brain unavailable → fallback ────────────────────────


async def test_write_storyboard_falls_back_when_no_permission() -> None:
    w = MangaScriptWriter(_StubAPI(brain=_LightweightBrain(), has_perm=False))
    res = await w.write_storyboard(story="主角走进剑道馆", n_panels=3)
    assert isinstance(res, BrainResult)
    assert res.ok is True
    assert res.used_brain is False
    assert "brain.access not granted" in res.error
    assert len(res.data["panels"]) == 3


async def test_write_storyboard_rejects_empty_story() -> None:
    w = MangaScriptWriter(_StubAPI(brain=_LightweightBrain()))
    with pytest.raises(ValueError, match="story must not be empty"):
        await w.write_storyboard(story="   ", n_panels=3)


# ─── write_storyboard: happy path ─────────────────────────────────────────


async def test_write_storyboard_uses_brain_when_available() -> None:
    payload = (
        '{"episode_title":"剑道初心","summary":"少年挑战自我",'
        '"panels":['
        '{"idx":0,"narration":"暮色中的剑道馆","dialogue":[],'
        '"characters_in_scene":["李雷"],"camera":"远景","action":"走入","mood":"紧张","background":"剑道馆"},'
        '{"idx":1,"narration":"师傅起身","dialogue":[],"characters_in_scene":["师傅"],"camera":"中景","action":"举刀","mood":"沉稳","background":"剑道馆"},'
        '{"idx":2,"narration":"对决开始","dialogue":[],"characters_in_scene":["李雷","师傅"],"camera":"特写","action":"挥刀","mood":"激烈","background":"剑道馆"}'
        "]}"
    )
    brain = _LightweightBrain(lightweight_response=payload)
    w = MangaScriptWriter(_StubAPI(brain=brain))
    res = await w.write_storyboard(story="李雷挑战剑道部师傅", n_panels=3)
    assert res.ok is True
    assert res.used_brain is True
    assert res.data["episode_title"] == "剑道初心"
    assert len(res.data["panels"]) == 3
    assert res.data["panels"][0]["camera"] == "远景"


async def test_write_storyboard_normalises_panel_count_when_llm_returns_too_few() -> None:
    payload = (
        '{"episode_title":"x","summary":"y","panels":[{"idx":0,"narration":"only one panel"}]}'
    )
    brain = _LightweightBrain(lightweight_response=payload)
    w = MangaScriptWriter(_StubAPI(brain=brain))
    res = await w.write_storyboard(story="测试", n_panels=4)
    assert res.ok is True
    assert len(res.data["panels"]) == 4
    # The first panel keeps the LLM data; remaining are normalised empty.
    assert res.data["panels"][0]["narration"] == "only one panel"
    for p in res.data["panels"][1:]:
        assert p["narration"] == ""


async def test_write_storyboard_truncates_when_llm_returns_too_many() -> None:
    panels = ",".join(f'{{"idx":{i},"narration":"p{i}"}}' for i in range(7))
    payload = f'{{"episode_title":"t","summary":"s","panels":[{panels}]}}'
    brain = _LightweightBrain(lightweight_response=payload)
    w = MangaScriptWriter(_StubAPI(brain=brain))
    res = await w.write_storyboard(story="x", n_panels=3)
    assert res.ok is True
    assert len(res.data["panels"]) == 3
    assert res.data["panels"][2]["narration"] == "p2"


# ─── write_storyboard: dispatch order ─────────────────────────────────────


async def test_write_storyboard_dispatch_prefers_lightweight() -> None:
    brain = _LightweightBrain(
        lightweight_response='{"episode_title":"a","summary":"b","panels":[{"idx":0}]}'
    )
    w = MangaScriptWriter(_StubAPI(brain=brain))
    await w.write_storyboard(story="x", n_panels=1)
    assert brain.calls == ["light"]


async def test_write_storyboard_falls_through_to_think_when_lightweight_raises() -> None:
    brain = _LightweightBrain(
        lightweight_raises=True,
        think_response='{"episode_title":"a","summary":"b","panels":[{"idx":0}]}',
    )
    w = MangaScriptWriter(_StubAPI(brain=brain))
    res = await w.write_storyboard(story="x", n_panels=1)
    assert brain.calls == ["light", "think"]
    assert res.ok is True


# ─── write_storyboard: malformed responses ────────────────────────────────


async def test_write_storyboard_returns_fallback_on_garbage_response() -> None:
    brain = _LightweightBrain(lightweight_response="this is not json at all")
    w = MangaScriptWriter(_StubAPI(brain=brain))
    res = await w.write_storyboard(story="李雷走进剑道馆。师傅在等他。", n_panels=2)
    assert res.ok is False
    assert res.used_brain is True
    # Fallback ALWAYS produces the requested number of panels.
    assert len(res.data["panels"]) == 2
    assert res.error  # error is propagated for the UI


async def test_write_storyboard_returns_fallback_on_empty_response() -> None:
    brain = _LightweightBrain(lightweight_response="", think_response="")
    w = MangaScriptWriter(_StubAPI(brain=brain))
    res = await w.write_storyboard(story="测试故事", n_panels=2)
    assert res.ok is False
    assert res.used_brain is True
    assert len(res.data["panels"]) == 2


async def test_write_storyboard_handles_brain_raising() -> None:
    class _RaisingBrain:
        async def think(self, *, prompt: str, system: str, max_tokens: int) -> str:
            raise ConnectionError("network down")

    w = MangaScriptWriter(_StubAPI(brain=_RaisingBrain()))
    res = await w.write_storyboard(story="测试", n_panels=2)
    assert res.ok is False
    assert res.used_brain is True
    assert "network down" in res.error
    assert len(res.data["panels"]) == 2


# ─── _normalize_storyboard pure function ───────────────────────────────────


def test_normalize_storyboard_handles_legacy_keys() -> None:
    out = _normalize_storyboard(
        {
            "title": "old key",
            "description": "summary via desc",
            "storyboard": [{"description": "narr via desc", "shot": "wide"}],
        },
        n_panels=1,
    )
    assert out["episode_title"] == "old key"
    assert out["summary"] == "summary via desc"
    assert out["panels"][0]["narration"] == "narr via desc"
    assert out["panels"][0]["camera"] == "wide"


def test_normalize_storyboard_drops_invalid_dialogue_lines() -> None:
    out = _normalize_storyboard(
        {
            "panels": [
                {
                    "narration": "x",
                    "dialogue": [
                        "garbage string",
                        {"character": "李雷", "line": ""},  # empty line filtered
                        {"speaker": "韩梅梅", "text": "hi"},  # alt keys
                    ],
                }
            ]
        },
        n_panels=1,
    )
    assert out["panels"][0]["dialogue"] == [{"character": "韩梅梅", "line": "hi"}]


def test_normalize_storyboard_filters_non_string_characters() -> None:
    out = _normalize_storyboard(
        {"panels": [{"characters_in_scene": [123, "李雷", None, ""]}]},
        n_panels=1,
    )
    assert out["panels"][0]["characters_in_scene"] == ["李雷"]


# ─── _fallback_panels pure function ────────────────────────────────────────


def test_fallback_panels_always_returns_requested_count() -> None:
    for n in (1, 3, 8):
        out = _fallback_panels(story="一句话故事", n_panels=n, characters=[])
        assert len(out["panels"]) == n


def test_fallback_panels_uses_first_main_character() -> None:
    out = _fallback_panels(
        story="李雷走进剑道馆。师傅在等他。",
        n_panels=2,
        characters=[
            {"name": "李雷", "role_type": "main"},
            {"name": "师傅", "role_type": "support"},
        ],
    )
    for panel in out["panels"]:
        assert panel["characters_in_scene"] == ["李雷"]


def test_fallback_panels_handles_empty_story() -> None:
    out = _fallback_panels(story=" ", n_panels=2, characters=[])
    assert len(out["panels"]) == 2


# ─── _build_user_prompt sanity ─────────────────────────────────────────────


def test_build_user_prompt_includes_character_block() -> None:
    prompt = _build_user_prompt(
        story="x",
        n_panels=3,
        seconds_per_panel=5,
        available_characters=[
            {
                "name": "李雷",
                "role_type": "main",
                "gender": "male",
                "age_range": "18-25",
                "personality": "勇敢",
            }
        ],
        visual_style_label="少年热血",
    )
    assert "李雷" in prompt
    assert "main" in prompt
    assert "少年热血" in prompt
    assert "3" in prompt  # n_panels


def test_build_user_prompt_handles_no_characters() -> None:
    prompt = _build_user_prompt(
        story="x",
        n_panels=2,
        seconds_per_panel=5,
        available_characters=[],
        visual_style_label="水彩",
    )
    assert "用户没有提供角色" in prompt
