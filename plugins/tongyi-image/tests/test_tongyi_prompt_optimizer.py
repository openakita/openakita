"""Unit tests for ``tongyi_prompt_optimizer``.

Focuses on pure functions (locale normalisation, guide-data assembly,
ecommerce prompt rendering, text extraction).  ``optimize_prompt`` is
async + brain-dependent and is exercised through integration tests
elsewhere — covering it here would force a brittle mock surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from tongyi_prompt_optimizer import (  # noqa: E402
    COMPOSITION_KEYWORDS,
    LEVEL_INSTRUCTIONS,
    LIGHTING_KEYWORDS,
    MODE_FORMULAS,
    NEGATIVE_PROMPT_PRESETS,
    PROMPT_TEMPLATES,
    STYLE_KEYWORDS,
    PromptOptimizeError,
    _extract_text,
    _localize_composition,
    _normalize_locale,
    generate_ecommerce_prompts,
    get_prompt_guide_data,
)

# ── _normalize_locale ─────────────────────────────────────────────────


def test_normalize_locale_known_values() -> None:
    assert _normalize_locale("zh") == "zh"
    assert _normalize_locale("en") == "en"


def test_normalize_locale_strips_region_suffix() -> None:
    """``zh-CN`` and ``en_US`` must collapse to base codes — UI sends
    BCP-47 (``zh-Hans-CN``) or POSIX (``en_US``) freely."""
    assert _normalize_locale("zh-CN") == "zh"
    assert _normalize_locale("zh-Hans-CN") == "zh"
    assert _normalize_locale("en_US") == "en"
    assert _normalize_locale("EN-GB") == "en"


def test_normalize_locale_unknown_falls_back_to_zh() -> None:
    assert _normalize_locale(None) == "zh"
    assert _normalize_locale("") == "zh"
    assert _normalize_locale("fr") == "zh"
    assert _normalize_locale("ja-JP") == "zh"


def test_normalize_locale_handles_garbage() -> None:
    """Defensive: callers may forward query-string junk (e.g. ``locale=42``)."""
    assert _normalize_locale("42") == "zh"
    assert _normalize_locale("   ") == "zh"


# ── get_prompt_guide_data ─────────────────────────────────────────────


def test_guide_data_default_locale_is_zh() -> None:
    out = get_prompt_guide_data()
    assert out["locale"] == "zh"
    assert out["templates"] is PROMPT_TEMPLATES
    assert out["mode_formulas"] is MODE_FORMULAS
    assert out["negative_presets"] is NEGATIVE_PROMPT_PRESETS


def test_guide_data_english_locale_swaps_long_form() -> None:
    out = get_prompt_guide_data("en")
    assert out["locale"] == "en"
    # Templates / mode formulas / negative presets must be the EN copies.
    assert out["templates"] is not PROMPT_TEMPLATES
    assert out["mode_formulas"] is not MODE_FORMULAS
    assert out["negative_presets"] is not NEGATIVE_PROMPT_PRESETS
    # Spot-check the EN copy actually reads as English.
    assert any("Subject" in v.get("basic", "") for v in out["mode_formulas"].values())


def test_guide_data_short_keywords_are_always_bilingual() -> None:
    """Style / lighting lists are bilingual {zh,en} regardless of locale —
    the UI renders both side-by-side, so a locale switch must NOT shrink
    them to a single language."""
    for loc in ("zh", "en", "fr"):
        out = get_prompt_guide_data(loc)
        for kw_list in out["style_keywords"].values():
            assert all("zh" in kw and "en" in kw for kw in kw_list)
        for kw_list in out["lighting_keywords"].values():
            assert all("zh" in kw and "en" in kw for kw in kw_list)


def test_guide_data_composition_localizes_label() -> None:
    out_zh = get_prompt_guide_data("zh")
    out_en = get_prompt_guide_data("en")
    # 景别 vs Shot size — labels must differ across locales.
    assert out_zh["composition_keywords"]["distance"]["label"] == "景别"
    assert out_en["composition_keywords"]["distance"]["label"] == "Shot size"
    assert out_zh["composition_keywords"]["angle"]["label"] == "视角"
    assert out_en["composition_keywords"]["angle"]["label"] == "Camera angle"


def test_guide_data_composition_keywords_keep_bilingual_pairs() -> None:
    """Even in the localized projection, each keyword entry must keep
    BOTH zh and en — UI shows the chip as ``中文 / English``."""
    for loc in ("zh", "en"):
        out = get_prompt_guide_data(loc)
        for cat in out["composition_keywords"].values():
            for kw in cat["keywords"]:
                assert "zh" in kw and "en" in kw and kw["zh"] and kw["en"]


def test_guide_data_unknown_locale_uses_zh_long_form() -> None:
    out = get_prompt_guide_data("ja")
    assert out["locale"] == "zh"
    assert out["templates"] is PROMPT_TEMPLATES


def test_guide_data_all_default_keys_present() -> None:
    """Stable contract for the UI fetch — adding keys is OK, removing is not."""
    out = get_prompt_guide_data()
    expected = {
        "locale", "templates", "style_keywords", "lighting_keywords",
        "composition_keywords", "negative_presets", "mode_formulas",
    }
    assert expected <= set(out)


# ── _localize_composition ─────────────────────────────────────────────


def test_localize_composition_zh_keeps_original_desc() -> None:
    out = _localize_composition("zh")
    # 特写 keeps its Chinese desc when locale=zh.
    distance_kws = {k["zh"]: k for k in out["distance"]["keywords"]}
    assert distance_kws["特写"]["desc"] == "聚焦局部细节"


def test_localize_composition_en_swaps_desc_when_translation_exists() -> None:
    out = _localize_composition("en")
    distance_kws = {k["zh"]: k for k in out["distance"]["keywords"]}
    # 特写 → "Tight focus on a small detail" per _COMPOSITION_DESC_EN.
    assert distance_kws["特写"]["desc"] == "Tight focus on a small detail"


def test_localize_composition_en_keeps_original_desc_when_no_translation() -> None:
    """Defensive: if a future entry is added to COMPOSITION_KEYWORDS but
    not to _COMPOSITION_DESC_EN, fall back to the source desc instead of
    crashing or emitting an empty string."""
    out = _localize_composition("en")
    # All keyword entries must still have a non-empty desc.
    for cat in out.values():
        for kw in cat["keywords"]:
            assert kw.get("desc", ""), f"missing desc on {kw}"


# ── _extract_text ────────────────────────────────────────────────────


def test_extract_text_from_string() -> None:
    assert _extract_text("hello") == "hello"


def test_extract_text_from_dict_with_content() -> None:
    assert _extract_text({"content": "abc"}) == "abc"


def test_extract_text_from_dict_without_content() -> None:
    """Missing ``content`` → empty string (not KeyError)."""
    assert _extract_text({"foo": "bar"}) == ""
    assert _extract_text({}) == ""


def test_extract_text_from_object_with_content_attr() -> None:
    class Resp:
        content = "from attr"
    assert _extract_text(Resp()) == "from attr"


def test_extract_text_from_object_without_content_falls_back_to_str() -> None:
    class Weird:
        def __str__(self) -> str:
            return "stringified"
    out = _extract_text(Weird())
    assert out == "stringified"


def test_extract_text_from_none() -> None:
    """Most fallible path — a brain that returns ``None`` must still yield
    a string so downstream ``.strip()`` does not blow up."""
    assert _extract_text(None) == "None"  # str(None) — empty-after-strip is fine


# ── generate_ecommerce_prompts ───────────────────────────────────────


def test_ecommerce_prompts_default_returns_all_scenes() -> None:
    """No ``scenes`` arg → emit one entry per built-in scene (hero, white,
    scene, lifestyle, detail, banner)."""
    out = generate_ecommerce_prompts(product_name="香水")
    scene_ids = [sid for sid, _ in out]
    assert set(scene_ids) == {"hero", "bg_white", "bg_scene", "bg_lifestyle",
                              "detail", "banner"}


def test_ecommerce_prompts_uses_base_prompt_when_provided() -> None:
    out = generate_ecommerce_prompts(product_name="ignored",
                                       base_prompt="精致的法式香水")
    # All emitted prompts must reference the base description, not the name.
    for _, prompt in out:
        assert "精致的法式香水" in prompt
        assert "ignored" not in prompt


def test_ecommerce_prompts_falls_back_to_product_name_when_base_empty() -> None:
    out = generate_ecommerce_prompts(product_name="香水")
    for _, prompt in out:
        assert "香水" in prompt


def test_ecommerce_prompts_falls_back_to_placeholder_when_both_empty() -> None:
    """Defensive: empty ``product_name`` AND empty ``base_prompt`` must
    still produce something instead of leaving ``{product}`` unrendered."""
    out = generate_ecommerce_prompts(product_name="", base_prompt="")
    for _, prompt in out:
        assert "{product}" not in prompt
        assert "产品" in prompt


def test_ecommerce_prompts_filters_by_scenes() -> None:
    out = generate_ecommerce_prompts("香水", scenes=["hero", "bg_white"])
    scene_ids = [sid for sid, _ in out]
    assert scene_ids == ["hero", "bg_white"]


def test_ecommerce_prompts_skips_unknown_scenes() -> None:
    """Unknown scene ids must be dropped silently — UI may send an
    outdated scene id after a server upgrade; no crash, just skip."""
    out = generate_ecommerce_prompts("香水", scenes=["hero", "bogus_scene"])
    scene_ids = [sid for sid, _ in out]
    assert scene_ids == ["hero"]


def test_ecommerce_prompts_strips_whitespace() -> None:
    out = generate_ecommerce_prompts("   ", base_prompt="  香水  ")
    for _, prompt in out:
        # Outer whitespace stripped, but inner whitespace in the template
        # itself remains (we don't aggressively normalise).
        assert "香水" in prompt


def test_ecommerce_prompts_returns_list_of_tuples() -> None:
    """Type-shape contract — callers index ``[0]`` (scene id) and
    ``[1]`` (prompt) on each entry."""
    out = generate_ecommerce_prompts("X", scenes=["hero"])
    assert isinstance(out, list)
    assert len(out) == 1
    assert isinstance(out[0], tuple) and len(out[0]) == 2
    assert isinstance(out[0][0], str) and isinstance(out[0][1], str)


# ── static data invariants ───────────────────────────────────────────


def test_prompt_templates_all_have_required_fields() -> None:
    """UI grid renders these — a missing field crashes the row."""
    required = {"id", "name", "description", "categories", "template", "example"}
    for tpl in PROMPT_TEMPLATES:
        assert required <= set(tpl), f"template missing fields: {tpl}"
        assert tpl["id"]
        assert isinstance(tpl["categories"], list) and tpl["categories"]


def test_prompt_template_ids_are_unique() -> None:
    """Otherwise the UI key warning + selection bug surfaces."""
    ids = [t["id"] for t in PROMPT_TEMPLATES]
    assert len(ids) == len(set(ids))


def test_negative_presets_have_general() -> None:
    """``general`` is the fallback the UI picks when no mode is active."""
    assert "general" in NEGATIVE_PROMPT_PRESETS
    assert NEGATIVE_PROMPT_PRESETS["general"]


def test_mode_formulas_cover_known_modes() -> None:
    """Each plugin mode (text2img / img_edit / ...) must have a formula
    or the UI mode-switcher renders an empty panel."""
    expected_modes = {"text2img", "img_edit", "style_repaint", "background",
                      "outpaint", "sketch", "ecommerce"}
    assert expected_modes <= set(MODE_FORMULAS)
    for mode, formula in MODE_FORMULAS.items():
        assert "basic" in formula, f"mode {mode} missing 'basic'"
        assert formula["basic"]


def test_level_instructions_match_documented_levels() -> None:
    """The optimizer's ``level=`` argument routes to one of these keys."""
    assert set(LEVEL_INSTRUCTIONS) == {"light", "professional", "creative"}
    for level, instruction in LEVEL_INSTRUCTIONS.items():
        assert instruction, f"empty instruction for level {level}"


def test_static_keyword_libraries_are_non_empty() -> None:
    """Smoke guard — these power the prompt-guide chip lists; empty would
    silently render an empty page."""
    assert STYLE_KEYWORDS and all(STYLE_KEYWORDS.values())
    assert LIGHTING_KEYWORDS and all(LIGHTING_KEYWORDS.values())
    assert COMPOSITION_KEYWORDS and all(COMPOSITION_KEYWORDS.values())


# ── PromptOptimizeError shape ────────────────────────────────────────


def test_prompt_optimize_error_is_an_exception() -> None:
    """Callers do ``raise PromptOptimizeError(...)`` and ``except`` it —
    must subclass Exception, not BaseException."""
    err = PromptOptimizeError("boom")
    assert isinstance(err, Exception)
    assert str(err) == "boom"


def test_prompt_optimize_error_can_be_caught_specifically() -> None:
    """Importer code does ``except PromptOptimizeError`` — make sure the
    name binding is exported and reachable."""
    try:
        raise PromptOptimizeError("X")
    except PromptOptimizeError as e:
        assert str(e) == "X"
    else:  # pragma: no cover
        pytest.fail("PromptOptimizeError did not propagate")
