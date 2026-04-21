"""Unit tests for ``workflow_presets`` — preset registry + override application."""

from __future__ import annotations

import pytest


def _wp():
    import workflow_presets
    return workflow_presets


# ── registry ──────────────────────────────────────────────────────────


def test_list_presets_returns_all_three_ids() -> None:
    wp = _wp()
    ids = wp.list_presets()
    assert set(ids) == {"sd15_basic", "sdxl_basic", "flux_basic"}


def test_describe_preset_for_sd15() -> None:
    wp = _wp()
    spec = wp.describe_preset("sd15_basic")
    assert spec.family == "sd"
    assert spec.checkpoint_default.endswith(".safetensors")
    assert spec.width_default == 512
    assert spec.height_default == 512
    assert spec.steps_default == 20


def test_describe_preset_for_sdxl_uses_1024() -> None:
    wp = _wp()
    spec = wp.describe_preset("sdxl_basic")
    assert spec.family == "sdxl"
    assert spec.width_default == 1024
    assert spec.height_default == 1024
    assert spec.steps_default == 28


def test_describe_preset_for_flux_locks_cfg_at_1() -> None:
    wp = _wp()
    spec = wp.describe_preset("flux_basic")
    assert spec.family == "flux"
    # FLUX preset spec field stores the default explicitly so plugin
    # callers can warn users who override it away from 1.0.
    assert float(spec.get("cfg_default", 0)) == 1.0


def test_describe_preset_unknown_raises_keyerror() -> None:
    wp = _wp()
    with pytest.raises(KeyError):
        wp.describe_preset("nope")


def test_build_preset_workflow_returns_fresh_copy() -> None:
    """Mutating one returned graph must not bleed into the next call."""
    wp = _wp()
    a = wp.build_preset_workflow("sd15_basic")
    a["1"]["inputs"]["ckpt_name"] = "tampered.safetensors"
    b = wp.build_preset_workflow("sd15_basic")
    assert b["1"]["inputs"]["ckpt_name"] != "tampered.safetensors"


def test_build_preset_workflow_includes_save_image_node() -> None:
    wp = _wp()
    for pid in wp.list_presets():
        wf = wp.build_preset_workflow(pid)
        ctypes = {n.get("class_type") for n in wf.values()}
        assert "SaveImage" in ctypes, f"{pid} missing SaveImage"


def test_build_preset_workflow_unknown_raises() -> None:
    wp = _wp()
    with pytest.raises(KeyError):
        wp.build_preset_workflow("doesnt-exist")


def test_preset_default_overrides_includes_all_user_keys() -> None:
    wp = _wp()
    overrides = wp.preset_default_overrides("sdxl_basic")
    for key in ["prompt", "negative", "seed", "steps", "cfg",
                "sampler", "scheduler", "width", "height",
                "checkpoint", "filename_prefix"]:
        assert key in overrides, f"missing default override: {key}"


def test_preset_default_overrides_seeds_zero() -> None:
    wp = _wp()
    assert wp.preset_default_overrides("sd15_basic")["seed"] == 0


# ── apply_overrides ────────────────────────────────────────────────────


def test_apply_overrides_substitutes_prompt_placeholder() -> None:
    wp = _wp()
    wf = wp.build_preset_workflow("sd15_basic")
    wp.apply_overrides(wf, {"prompt": "a red cat"})
    # The positive CLIPTextEncode node ("2") has the {prompt} placeholder.
    assert "a red cat" in wf["2"]["inputs"]["text"]
    assert "{prompt}" not in wf["2"]["inputs"]["text"]


def test_apply_overrides_substitutes_negative_placeholder() -> None:
    wp = _wp()
    wf = wp.build_preset_workflow("sd15_basic")
    wp.apply_overrides(wf, {"negative": "blurry, low quality"})
    assert "blurry, low quality" in wf["3"]["inputs"]["text"]
    assert "{negative}" not in wf["3"]["inputs"]["text"]


def test_apply_overrides_sets_size() -> None:
    wp = _wp()
    wf = wp.build_preset_workflow("sd15_basic")
    wp.apply_overrides(wf, {"width": 768, "height": 384})
    assert wf["4"]["inputs"]["width"] == 768
    assert wf["4"]["inputs"]["height"] == 384


def test_apply_overrides_sets_ksampler_params() -> None:
    wp = _wp()
    wf = wp.build_preset_workflow("sdxl_basic")
    wp.apply_overrides(wf, {
        "seed": 42, "steps": 35, "cfg": 8.5,
        "sampler": "dpmpp_3m_sde", "scheduler": "exponential",
    })
    inputs = wf["5"]["inputs"]
    assert inputs["seed"] == 42
    assert inputs["steps"] == 35
    assert inputs["cfg"] == 8.5
    assert inputs["sampler_name"] == "dpmpp_3m_sde"
    assert inputs["scheduler"] == "exponential"


def test_apply_overrides_sets_checkpoint_for_sd_family() -> None:
    wp = _wp()
    wf = wp.build_preset_workflow("sd15_basic")
    wp.apply_overrides(wf, {"checkpoint": "my_custom.safetensors"})
    assert wf["1"]["inputs"]["ckpt_name"] == "my_custom.safetensors"


def test_apply_overrides_sets_unet_name_for_flux_family() -> None:
    wp = _wp()
    wf = wp.build_preset_workflow("flux_basic")
    wp.apply_overrides(wf, {"checkpoint": "flux1-schnell.safetensors"})
    assert wf["1"]["inputs"]["unet_name"] == "flux1-schnell.safetensors"


def test_apply_overrides_sets_filename_prefix() -> None:
    wp = _wp()
    wf = wp.build_preset_workflow("sd15_basic")
    wp.apply_overrides(wf, {"filename_prefix": "my_test"})
    assert wf["7"]["inputs"]["filename_prefix"] == "my_test"


def test_apply_overrides_skips_unknown_keys() -> None:
    """Unknown override keys must not crash — they're silently dropped."""
    wp = _wp()
    wf = wp.build_preset_workflow("sd15_basic")
    wp.apply_overrides(wf, {"nonexistent_key": "value"})  # should not raise
