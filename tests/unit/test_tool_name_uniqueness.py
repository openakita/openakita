from types import SimpleNamespace

from openakita.core.agent import Agent
from openakita.core.brain import Brain
from openakita.llm.client import _friendly_error_hint
from openakita.llm.error_types import FailoverReason
from openakita.tools.definitions import AGENT_TOOLS, BASE_TOOLS, HUB_TOOLS, ORG_SETUP_TOOLS


def _duplicate_names(tools: list[dict]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for tool in tools:
        name = tool.get("name")
        if not name:
            continue
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return duplicates


def test_static_tool_definitions_have_unique_names():
    tools = list(BASE_TOOLS) + list(HUB_TOOLS) + list(AGENT_TOOLS) + list(ORG_SETUP_TOOLS)

    assert _duplicate_names(tools) == set()


def test_agent_effective_tools_dedupes_runtime_sources():
    tools = [
        {"name": "browser_click", "description": "first", "input_schema": {}},
        {"name": "browser_click", "description": "second", "input_schema": {}},
        {"name": "read_file", "description": "read", "input_schema": {}},
    ]

    deduped = Agent._dedupe_tools_by_name(tools, source="test")

    assert [tool["name"] for tool in deduped] == ["browser_click", "read_file"]
    assert deduped[0]["description"] == "first"


def test_brain_llm_tool_conversion_dedupes_before_provider_request():
    brain = Brain.__new__(Brain)
    converted = brain._convert_tools_to_llm(
        [
            {
                "name": "browser_click",
                "description": "first browser click schema",
                "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}}},
            },
            {
                "name": "browser_click",
                "description": "duplicate browser click schema",
                "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            },
            {
                "name": "read_file",
                "description": "read file schema",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        ]
    )

    assert converted is not None
    assert [tool.name for tool in converted] == ["browser_click", "read_file"]
    assert converted[0].description == "first browser click schema"


def test_tool_name_unique_error_hint_points_to_internal_duplicate_tools():
    provider = SimpleNamespace(
        error_category=FailoverReason.STRUCTURAL,
        _last_error="API error (400): Tool names must be unique.",
    )

    hint = _friendly_error_hint(
        [provider],
        last_error="API error (400): Tool names must be unique.",
    )

    assert "内部工具定义重复" in hint
    assert "切换其他模型" not in hint


def test_tool_name_unique_error_hint_uses_provider_last_error():
    provider = SimpleNamespace(
        error_category=FailoverReason.STRUCTURAL,
        _last_error="API error (400): Tool names must be unique.",
    )

    hint = _friendly_error_hint([provider])

    assert "内部工具定义重复" in hint
    assert "切换其他模型" not in hint
