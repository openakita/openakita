"""Tests for openakita.orgs.plugin_workbench_templates.

The workbench template module bridges the plugin system and the org
editor: it inspects PluginManager.loaded_plugins and emits one template
per plugin that registered LLM tools. These templates seed a leaf
``OrgNode`` whose ``external_tools`` is pre-bound to the plugin's tool
names and ``plugin_origin`` marks it as workbench-backed.
"""

from __future__ import annotations

from types import SimpleNamespace

from openakita.orgs.plugin_workbench_templates import (
    build_workbench_templates,
    deprecated_tools_for_node,
)


def _make_plugin(
    plugin_id: str,
    *,
    display_zh: str = "",
    display_en: str = "",
    description: str = "",
    description_i18n: dict | None = None,
    version: str = "1.0.0",
    icon: str = "",
    category: str = "",
    tools: list[dict] | None = None,
) -> SimpleNamespace:
    manifest = SimpleNamespace(
        id=plugin_id,
        name=plugin_id,
        version=version,
        display_name_zh=display_zh,
        display_name_en=display_en,
        description=description,
        description_i18n=description_i18n or {},
        icon=icon,
        category=category,
    )
    api = SimpleNamespace(_registered_tools=list(tools or []))
    return SimpleNamespace(manifest=manifest, api=api)


def _make_pm(
    plugins: list[SimpleNamespace],
    *,
    tool_definitions: list[dict] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        loaded_plugins={p.manifest.id: p for p in plugins},
        _external_host_refs={"tool_definitions": list(tool_definitions or [])},
    )


def test_build_workbench_templates_empty_when_pm_is_none():
    assert build_workbench_templates(None) == []


def test_build_workbench_templates_skips_plugins_without_tools():
    pm = _make_pm([_make_plugin("ui-only", tools=[])])
    assert build_workbench_templates(pm) == []


def test_build_workbench_templates_emits_suggested_node():
    pm = _make_pm([
        _make_plugin(
            "tongyi-image",
            display_zh="通义生图",
            display_en="Tongyi Image",
            description="AI image generation",
            description_i18n={"zh": "AI 图片生成"},
            version="0.3.0",
            icon="icon.svg",
            category="creative",
            tools=[
                {
                    "name": "tongyi_image_create",
                    "description": "Create image",
                    "input_schema": {"type": "object"},
                },
                {
                    "name": "tongyi_image_status",
                    "description": "Status",
                    "input_schema": {"type": "object"},
                },
            ],
        ),
    ])
    out = build_workbench_templates(pm)
    assert len(out) == 1
    tpl = out[0]
    assert tpl["id"] == "workbench:tongyi-image"
    assert tpl["plugin_id"] == "tongyi-image"
    assert tpl["version"] == "0.3.0"
    assert tpl["name"] == "通义生图"
    assert tpl["name_i18n"] == {"zh": "通义生图", "en": "Tongyi Image"}
    assert tpl["tool_names"] == ["tongyi_image_create", "tongyi_image_status"]
    # full tool dicts are included for the UI's hover/preview
    assert {t["name"] for t in tpl["tools"]} == {
        "tongyi_image_create",
        "tongyi_image_status",
    }

    suggested = tpl["suggested_node"]
    # workbench nodes are leaves & cannot delegate further
    assert suggested["can_delegate"] is False
    # tool list matches the plugin's tools (no expansion to "creative" etc.)
    assert suggested["external_tools"] == [
        "tongyi_image_create",
        "tongyi_image_status",
    ]
    # workbench prompt explicitly disables basic filesystem tools to keep
    # the node tightly scoped to its plugin
    assert suggested["enable_file_tools"] is False
    # plugin_origin carries all three required keys (used by frontend banner
    # + runtime _build_workbench_prompt_section)
    assert suggested["plugin_origin"] == {
        "plugin_id": "tongyi-image",
        "template_id": "workbench:tongyi-image",
        "version": "0.3.0",
    }
    # custom_prompt lists the actual tool names so the LLM never has to
    # guess what it's allowed to call
    assert "tongyi_image_create" in suggested["custom_prompt"]


def test_build_workbench_templates_sorts_by_category_then_name():
    pm = _make_pm([
        _make_plugin(
            "p1", display_zh="zNode", category="dev",
            tools=[{"name": "t1", "description": "", "input_schema": {}}],
        ),
        _make_plugin(
            "p2", display_zh="aNode", category="creative",
            tools=[{"name": "t2", "description": "", "input_schema": {}}],
        ),
        _make_plugin(
            "p3", display_zh="bNode", category="creative",
            tools=[{"name": "t3", "description": "", "input_schema": {}}],
        ),
    ])
    out = build_workbench_templates(pm)
    assert [t["plugin_id"] for t in out] == ["p2", "p3", "p1"]


def test_deprecated_tools_for_node_flags_removed_tools():
    pm = _make_pm([
        _make_plugin(
            "p1",
            tools=[{"name": "p1_alpha", "description": "", "input_schema": {}}],
        ),
    ])
    # "research" is a category name → not deprecated even if no plugin
    # registers it (built-in category names are part of ALL_CATEGORY_NAMES)
    assert deprecated_tools_for_node(
        ["research", "p1_alpha", "p1_removed"], pm,
    ) == ["p1_removed"]


def test_deprecated_tools_for_node_empty_inputs():
    assert deprecated_tools_for_node([], None) == []
    assert deprecated_tools_for_node([], _make_pm([])) == []


def test_build_workbench_templates_handles_registered_tools_as_strings():
    """Regression: PluginAPI._registered_tools is list[str] in production.

    The workbench picker was crashing with ``AttributeError: 'str' object
    has no attribute 'get'`` because it assumed dict entries. The fix
    resolves the full tool schema from the host's ``tool_definitions``
    list when the plugin stored only names.
    """
    plugin = _make_plugin(
        "tongyi-image",
        display_zh="通义生图",
        tools=["tongyi_image_create", "tongyi_image_status"],
    )
    pm = _make_pm(
        [plugin],
        tool_definitions=[
            {
                "name": "tongyi_image_create",
                "description": "Create image",
                "input_schema": {"type": "object"},
            },
            {
                "name": "tongyi_image_status",
                "description": "Check status",
                "input_schema": {"type": "object"},
            },
            {"name": "unrelated_tool", "description": "noise", "input_schema": {}},
        ],
    )
    out = build_workbench_templates(pm)
    assert len(out) == 1
    tpl = out[0]
    assert tpl["tool_names"] == ["tongyi_image_create", "tongyi_image_status"]
    by_name = {t["name"]: t for t in tpl["tools"]}
    assert by_name["tongyi_image_create"]["description"] == "Create image"
    assert by_name["tongyi_image_create"]["input_schema"] == {"type": "object"}
    assert tpl["suggested_node"]["external_tools"] == [
        "tongyi_image_create",
        "tongyi_image_status",
    ]


def test_build_workbench_templates_handles_openai_function_envelope():
    """Tool definitions may use the OpenAI ``{"function": {...}}`` shape."""
    plugin = _make_plugin("foo", display_zh="Foo", tools=["foo_run"])
    pm = _make_pm(
        [plugin],
        tool_definitions=[
            {
                "type": "function",
                "function": {
                    "name": "foo_run",
                    "description": "Run foo",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
    )
    out = build_workbench_templates(pm)
    assert len(out) == 1
    tool = out[0]["tools"][0]
    assert tool["name"] == "foo_run"
    assert tool["description"] == "Run foo"
    assert tool["input_schema"] == {"type": "object", "properties": {}}


def test_build_workbench_templates_string_tools_without_host_defs():
    """If host tool_definitions is missing, names still surface (empty schema)."""
    plugin = _make_plugin("bare", display_zh="Bare", tools=["x", "y"])
    pm = SimpleNamespace(loaded_plugins={"bare": plugin})  # no _external_host_refs
    out = build_workbench_templates(pm)
    assert len(out) == 1
    assert out[0]["tool_names"] == ["x", "y"]
    for t in out[0]["tools"]:
        assert t["description"] == ""
        assert t["input_schema"] == {}


def test_deprecated_tools_for_node_with_string_registered_tools():
    """deprecated_tools_for_node must also accept list[str] entries."""
    plugin = _make_plugin("p1", tools=["p1_alpha"])
    pm = _make_pm([plugin])
    assert deprecated_tools_for_node(
        ["research", "p1_alpha", "p1_removed"], pm,
    ) == ["p1_removed"]
