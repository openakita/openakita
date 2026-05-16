"""Plugin entry — verifies tool registration + workbench schema."""

from __future__ import annotations

from _plugin_loader import load_happyhorse_plugin

_HH = load_happyhorse_plugin()
HappyhorsePlugin = _HH.Plugin


EXPECTED_TOOLS = {
    "hh_t2v",
    "hh_image_create",
    "hh_image_edit",
    "hh_image_style_repaint",
    "hh_image_background",
    "hh_image_outpaint",
    "hh_image_sketch",
    "hh_image_ecommerce",
    "hh_i2v",
    "hh_r2v",
    "hh_video_edit",
    "hh_photo_speak",
    "hh_video_relip",
    "hh_video_reface",
    "hh_pose_drive",
    "hh_avatar_compose",
    "hh_status",
    "hh_list",
    "hh_cost_preview",
    "hh_long_video_create",
}


def test_plugin_registers_video_and_image_tools():
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    tools = plugin._tool_definitions()
    names = {t["name"] for t in tools}
    assert names == EXPECTED_TOOLS


def test_video_tools_advertise_from_asset_ids_in_schema():
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    tools = plugin._tool_definitions()
    by_name = {t["name"]: t for t in tools}
    for tool_name in (
        "hh_t2v",
        "hh_i2v",
        "hh_r2v",
        "hh_video_edit",
        "hh_photo_speak",
        "hh_avatar_compose",
    ):
        schema = by_name[tool_name]["input_schema"]
        assert "from_asset_ids" in schema["properties"], (
            f"{tool_name} must accept from_asset_ids for workbench chaining"
        )


def test_video_tools_accept_documented_alias_fields():
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    tools = plugin._tool_definitions()
    by_name = {t["name"]: t for t in tools}
    props = by_name["hh_video_edit"]["input_schema"]["properties"]
    assert "video_url" in props
    assert "source_video_url" in props
    assert "task_type" in props
    assert "mode_pro" in props
    assert "ref_images_url" in props


def test_status_tool_requires_task_id():
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    tools = plugin._tool_definitions()
    by_name = {t["name"]: t for t in tools}
    schema = by_name["hh_status"]["input_schema"]
    assert "task_id" in schema["required"]


def test_long_video_tool_requires_segments():
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    tools = plugin._tool_definitions()
    by_name = {t["name"]: t for t in tools}
    schema = by_name["hh_long_video_create"]["input_schema"]
    assert "segments" in schema["required"]


def test_module_exposes_pydantic_bodies():
    """plugin.py must export its request bodies so on_load can use them."""
    assert hasattr(_HH, "CreateTaskBody")
    assert hasattr(_HH, "ImageCreateTaskBody")
    assert hasattr(_HH, "LongVideoCreateBody")
    assert hasattr(_HH, "PromptOptimizeBody")


def test_image_tools_publish_asset_ids_contract():
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    tools = plugin._tool_definitions()
    by_name = {t["name"]: t for t in tools}
    schema = by_name["hh_image_create"]["input_schema"]
    assert "from_asset_ids" in schema["properties"]
    assert "model_id" in schema["properties"]
    assert "size" in schema["properties"]


# ─── Bug 6 regression — hh_image_ecommerce schema accepts product_name ─


def test_image_ecommerce_schema_accepts_either_prompt_or_product_name():
    """hh_image_ecommerce historically required ``prompt`` even though
    the backend builds prompts from ``product_name`` alone. The schema
    now exposes ``anyOf`` so LLMs can call the tool with whichever
    field they have.
    """
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    tools = plugin._tool_definitions()
    by_name = {t["name"]: t for t in tools}
    schema = by_name["hh_image_ecommerce"]["input_schema"]
    assert "required" not in schema or "prompt" not in schema.get("required", [])
    assert "anyOf" in schema
    required_groups = [grp.get("required", []) for grp in schema["anyOf"]]
    assert ["prompt"] in required_groups
    assert ["product_name"] in required_groups


def test_image_text2img_schema_still_requires_prompt():
    """The relaxation for hh_image_ecommerce must not leak into other
    image tools — text-to-image still needs a prompt."""
    plugin = HappyhorsePlugin.__new__(HappyhorsePlugin)
    tools = plugin._tool_definitions()
    by_name = {t["name"]: t for t in tools}
    schema = by_name["hh_image_create"]["input_schema"]
    assert schema.get("required") == ["prompt"]
