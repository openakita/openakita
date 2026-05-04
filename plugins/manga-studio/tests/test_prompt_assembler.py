"""Phase 2.5 — prompt_assembler.py tests.

The assembler is pure functions, so every test is a one-liner: feed a
panel dict + characters list, assert on the resulting string fragments.
We focus on the edge cases that would crash a downstream vendor call:

- Unknown character refs must NOT crash (Pixelle C5).
- Missing fields (no narration / no dialogue / empty appearance) must
  produce a non-empty prompt.
- Reference image cap at 9 (DashScope max).
- Prompt clipping never cuts mid-word.
"""

from __future__ import annotations

import logging

import pytest

from manga_models import VISUAL_STYLES_BY_ID
from prompt_assembler import (
    MAX_IMAGE_PROMPT_CHARS,
    MAX_REF_IMAGES,
    MAX_VIDEO_PROMPT_CHARS,
    ImagePromptResult,
    VideoPromptResult,
    _clip_chars,
    _describe_character,
    _normalize_ref_images,
    compose_i2v_prompt,
    compose_image_prompt,
    compose_t2v_prompt,
    compose_tts_text,
)


def _shonen():
    return VISUAL_STYLES_BY_ID["shonen"]


def _watercolor():
    return VISUAL_STYLES_BY_ID["watercolor"]


# ─── compose_image_prompt: happy path ─────────────────────────────────────


def test_compose_image_prompt_includes_style_fragment_first() -> None:
    res = compose_image_prompt(
        panel={"narration": "李雷握紧了手中的木刀。"},
        characters=[],
        style=_shonen(),
        ratio="9:16",
    )
    assert isinstance(res, ImagePromptResult)
    # Style anchor MUST come first so the diffusion model latches on.
    assert res.prompt.startswith("shonen manga style")
    assert "李雷握紧了手中的木刀" in res.prompt
    assert res.ratio == "9:16"


def test_compose_image_prompt_attaches_character_appearance() -> None:
    char = {
        "id": "c1",
        "name": "李雷",
        "role_type": "main",
        "gender": "male",
        "age_range": "18-25",
        "personality": "勇敢正直",
        "appearance_json": {"hair_color": "black", "outfit": "school uniform"},
        "description": "学校剑道部队长",
    }
    res = compose_image_prompt(
        panel={
            "narration": "暮色中的剑道馆。",
            "characters_in_scene": ["李雷"],
        },
        characters=[char],
        style=_shonen(),
    )
    assert "李雷" in res.prompt
    assert "hair color: black" in res.prompt
    assert "outfit: school uniform" in res.prompt
    assert "school uniform" in res.prompt


def test_compose_image_prompt_dedupes_same_character_referenced_twice() -> None:
    """A panel that names the same character in characters_in_scene
    AND in dialogue should only emit one description."""
    char = {
        "id": "c1",
        "name": "李雷",
        "role_type": "main",
        "personality": "勇敢正直",
        "appearance_json": {"hair_color": "black"},
    }
    res = compose_image_prompt(
        panel={
            "characters_in_scene": ["李雷", "李雷"],
            "dialogue": [{"character": "李雷", "line": "今天必须赢。"}],
        },
        characters=[char],
        style=_shonen(),
    )
    # Should appear in the prompt at most twice (once in description
    # head, possibly via in_scene de-dup logic). Hair color fragment
    # MUST appear exactly once.
    assert res.prompt.count("hair color: black") == 1


def test_compose_image_prompt_handles_unknown_character_gracefully(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pixelle C5: a hallucinated name should warn, not crash."""
    with caplog.at_level(logging.WARNING):
        res = compose_image_prompt(
            panel={
                "narration": "幽灵出现了。",
                "characters_in_scene": ["不存在的人"],
            },
            characters=[],
            style=_shonen(),
            panel_index=3,
        )
    assert "unknown character ref" in caplog.text
    assert "panel 3" in caplog.text
    # Prompt is still usable.
    assert "shonen manga style" in res.prompt
    assert "幽灵出现了" in res.prompt


def test_compose_image_prompt_caps_reference_images_at_nine() -> None:
    """DashScope wan2.7-image accepts at most 9 reference images.
    Three characters with 5 refs each (15 total) → must clip to 9."""
    chars = [
        {
            "id": f"c{i}",
            "name": f"角色{i}",
            "ref_images_json": [f"https://oss/c{i}/{j}.png" for j in range(5)],
        }
        for i in range(3)
    ]
    res = compose_image_prompt(
        panel={"characters_in_scene": [f"角色{i}" for i in range(3)]},
        characters=chars,
        style=_shonen(),
    )
    assert len(res.reference_image_urls) == MAX_REF_IMAGES


def test_compose_image_prompt_dedupes_reference_image_urls() -> None:
    chars = [
        {"id": "c1", "name": "A", "ref_images_json": ["https://oss/x.png"]},
        {"id": "c2", "name": "B", "ref_images_json": ["https://oss/x.png"]},
    ]
    res = compose_image_prompt(
        panel={"characters_in_scene": ["A", "B"]},
        characters=chars,
        style=_shonen(),
    )
    assert res.reference_image_urls == ["https://oss/x.png"]


def test_compose_image_prompt_clips_to_max_chars() -> None:
    """A 10 000-char narration must clip to MAX_IMAGE_PROMPT_CHARS."""
    res = compose_image_prompt(
        panel={"narration": "极长描述" * 1000},
        characters=[],
        style=_shonen(),
    )
    assert len(res.prompt) <= MAX_IMAGE_PROMPT_CHARS


def test_compose_image_prompt_negative_includes_style_negative() -> None:
    res = compose_image_prompt(panel={"narration": "x"}, characters=[], style=_shonen())
    assert "photorealistic" in res.negative_prompt  # shonen.negative_prompt
    assert "watermark" in res.negative_prompt  # universal manga negatives


def test_compose_image_prompt_with_no_panel_text_still_produces_prompt() -> None:
    res = compose_image_prompt(panel={}, characters=[], style=_shonen())
    assert res.prompt
    assert res.prompt.startswith("shonen manga style")


# ─── compose_i2v_prompt ───────────────────────────────────────────────────


def test_compose_i2v_prompt_includes_motion_and_camera() -> None:
    res = compose_i2v_prompt(
        panel={"camera": "推镜头", "action": "缓慢举刀", "mood": "紧张"},
        style=_shonen(),
        duration_sec=5,
    )
    assert isinstance(res, VideoPromptResult)
    assert res.has_reference_image is True
    assert res.duration_sec == 5
    assert "推镜头" in res.prompt
    assert "缓慢举刀" in res.prompt
    assert "紧张" in res.prompt
    assert "5s" in res.prompt


def test_compose_i2v_prompt_clips_to_video_max() -> None:
    res = compose_i2v_prompt(
        panel={"action": "动作描述" * 200, "camera": "x", "mood": "y"},
        style=_shonen(),
        duration_sec=10,
    )
    assert len(res.prompt) <= MAX_VIDEO_PROMPT_CHARS


def test_compose_i2v_prompt_clamps_zero_duration() -> None:
    res = compose_i2v_prompt(panel={"action": "x"}, style=_shonen(), duration_sec=0)
    assert res.duration_sec == 1


# ─── compose_t2v_prompt ───────────────────────────────────────────────────


def test_compose_t2v_prompt_carries_full_scene_description() -> None:
    res = compose_t2v_prompt(
        panel={
            "narration": "李雷在剑道馆门口。",
            "camera": "远景",
            "action": "走入剑道馆",
            "characters_in_scene": ["李雷"],
        },
        characters=[
            {
                "id": "c1",
                "name": "李雷",
                "appearance_json": {"hair_color": "black"},
            }
        ],
        style=_shonen(),
        duration_sec=5,
    )
    assert isinstance(res, VideoPromptResult)
    assert res.has_reference_image is False
    assert "manga drama animation" in res.prompt
    assert "Scene:" in res.prompt
    assert "远景" in res.prompt or "走入" in res.prompt


# ─── compose_tts_text ─────────────────────────────────────────────────────


def test_compose_tts_picks_first_dialogue_with_character_voice() -> None:
    text, voice = compose_tts_text(
        panel={
            "narration": "旁白",
            "dialogue": [
                {"character": "李雷", "line": "今天必须赢。"},
                {"character": "韩梅梅", "line": "加油！"},
            ],
        },
        characters=[
            {"id": "c1", "name": "李雷", "default_voice_id": "zh-CN-YunjianNeural"},
            {"id": "c2", "name": "韩梅梅", "default_voice_id": "zh-CN-XiaoyiNeural"},
        ],
    )
    assert text == "今天必须赢。"
    assert voice == "zh-CN-YunjianNeural"


def test_compose_tts_falls_back_to_narration_when_no_dialogue() -> None:
    text, voice = compose_tts_text(
        panel={"narration": "旁白叙述。"},
        characters=[],
        fallback_voice="zh-CN-XiaoxiaoNeural",
    )
    assert text == "旁白叙述。"
    assert voice == "zh-CN-XiaoxiaoNeural"


def test_compose_tts_uses_fallback_voice_when_speaker_unknown() -> None:
    text, voice = compose_tts_text(
        panel={"dialogue": [{"character": "陌生人", "line": "你好。"}]},
        characters=[],
        fallback_voice="zh-CN-XiaoxiaoNeural",
    )
    assert text == "你好。"
    assert voice == "zh-CN-XiaoxiaoNeural"


def test_compose_tts_returns_empty_when_panel_silent() -> None:
    text, voice = compose_tts_text(panel={"camera": "推镜头"}, characters=[])
    assert text == ""
    assert voice == ""


def test_compose_tts_skips_dialogue_when_include_dialogue_false() -> None:
    text, voice = compose_tts_text(
        panel={
            "narration": "旁白",
            "dialogue": [{"character": "李雷", "line": "对话"}],
        },
        include_dialogue=False,
    )
    assert text == "旁白"


# ─── _describe_character ──────────────────────────────────────────────────


def test_describe_character_handles_minimum_fields() -> None:
    out = _describe_character({"id": "c1", "name": "无名"})
    assert "无名" in out


def test_describe_character_returns_empty_when_no_name() -> None:
    assert _describe_character({"id": "c1"}) == ""


def test_describe_character_accepts_string_appearance() -> None:
    """Legacy rows might store appearance as a free-form string."""
    out = _describe_character({"id": "c1", "name": "李雷", "appearance_json": "穿白色制服"})
    assert "李雷" in out
    assert "穿白色制服" in out


def test_describe_character_skips_unknown_gender() -> None:
    out = _describe_character(
        {"id": "c1", "name": "李雷", "gender": "unknown", "personality": "勇敢"}
    )
    assert "unknown" not in out
    assert "勇敢" in out


# ─── _normalize_ref_images ────────────────────────────────────────────────


def test_normalize_ref_images_accepts_list_of_strings() -> None:
    assert _normalize_ref_images(["a", "b"]) == ["a", "b"]


def test_normalize_ref_images_accepts_list_of_dicts() -> None:
    assert _normalize_ref_images([{"url": "a"}, {"image": "b"}]) == ["a", "b"]


def test_normalize_ref_images_accepts_json_string() -> None:
    assert _normalize_ref_images('["a", "b"]') == ["a", "b"]


def test_normalize_ref_images_rejects_garbage() -> None:
    assert _normalize_ref_images("not json") == []
    assert _normalize_ref_images(None) == []
    assert _normalize_ref_images(123) == []


def test_normalize_ref_images_skips_empty_strings() -> None:
    assert _normalize_ref_images(["", "   ", "ok"]) == ["ok"]


# ─── _clip_chars ──────────────────────────────────────────────────────────


def test_clip_chars_short_string_unchanged() -> None:
    assert _clip_chars("abc", 100) == "abc"


def test_clip_chars_breaks_on_comma_separator() -> None:
    s = "shonen, dynamic, kinetic, vibrant, electric"
    out = _clip_chars(s, 25)
    assert out.endswith("kinetic") or out.endswith("dynamic")
    assert ", " not in out[-2:]


def test_clip_chars_handles_empty() -> None:
    assert _clip_chars("", 100) == ""
    assert _clip_chars("anything", 0) == ""
