"""happyhorse_prompt_optimizer — templates + 12-mode formula coverage."""

from __future__ import annotations

import pytest
from happyhorse_prompt_optimizer import (
    ATMOSPHERE_KEYWORDS,
    CAMERA_KEYWORDS,
    MODE_FORMULAS,
    OPTIMIZE_SYSTEM_PROMPT,
    PROMPT_TEMPLATES,
    PromptOptimizeError,
    optimize_prompt,
)

EXPECTED_MODES = {
    "t2v",
    "i2v",
    "i2v_end",
    "video_extend",
    "r2v",
    "video_edit",
    "photo_speak",
    "video_relip",
    "video_reface",
    "pose_drive",
    "avatar_compose",
    "long_video",
}


def test_mode_formulas_cover_all_twelve_modes():
    assert set(MODE_FORMULAS.keys()) == EXPECTED_MODES


def test_prompt_templates_cover_all_twelve_modes():
    used = set()
    for tpl in PROMPT_TEMPLATES:
        used.update(tpl.get("modes", []))
    assert EXPECTED_MODES.issubset(used)


def test_system_prompt_documents_happyhorse_audio_sync():
    assert "HappyHorse" in OPTIMIZE_SYSTEM_PROMPT
    assert "音视频同步" in OPTIMIZE_SYSTEM_PROMPT


def test_camera_and_atmosphere_keywords_nonempty():
    assert len(CAMERA_KEYWORDS) >= 10
    for k in ("light", "color", "texture", "mood"):
        assert k in ATMOSPHERE_KEYWORDS
        assert len(ATMOSPHERE_KEYWORDS[k]) >= 5


@pytest.mark.asyncio
async def test_optimize_prompt_raises_when_no_brain_methods():
    class _Brain:
        pass

    with pytest.raises(PromptOptimizeError):
        await optimize_prompt(brain=_Brain(), user_prompt="x", mode="t2v")


@pytest.mark.asyncio
async def test_optimize_prompt_uses_chat_when_only_chat_present():
    class _Brain:
        async def chat(self, messages):
            return {"content": "OK 优化结果"}

    out = await optimize_prompt(brain=_Brain(), user_prompt="x", mode="t2v")
    assert "OK" in out
