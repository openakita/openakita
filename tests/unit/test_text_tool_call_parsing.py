"""Regression tests for text-based tool call parsing."""

from openakita.llm.converters.tools import (
    has_text_tool_calls,
    parse_text_tool_calls,
    register_tool_names,
)


def test_parse_minimax_kimi_hybrid_tool_call():
    text = (
        '<minimax:tool_call> browser_open:3 <|tool_call_argument_begin|> {"visible": true} '
        "<|tool_call_end|> <|tool_calls_section_end|>"
    )

    assert has_text_tool_calls(text) is True

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert clean_text == ""
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "browser_open"
    assert tool_calls[0].input == {"visible": True}


def test_parse_plain_minimax_kimi_hybrid_tool_call():
    text = (
        'minimax:tool_call functions.browser_open:3 <|tool_call_argument_begin|> {"visible": true} '
        "<|tool_call_end|> <|tool_calls_section_end|>"
    )

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert clean_text == ""
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "browser_open"
    assert tool_calls[0].input == {"visible": True}


def test_parse_plain_tool_params_json_calls():
    register_tool_names(["glob", "list_skills", "web_search"])
    text = (
        '{"tool": "glob", "params": {"pattern": "data/output/Agents/*.md"}} json '
        '{"tool": "list_skills", "params": {}} json '
        '{"tool": "web_search", "params": {"query": "CJ Dropshipping API documentation", '
        '"max_results": 5}}'
    )

    assert has_text_tool_calls(text) is True

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert len(tool_calls) == 3
    assert [(tc.name, tc.input) for tc in tool_calls] == [
        ("glob", {"pattern": "data/output/Agents/*.md"}),
        ("list_skills", {}),
        ("web_search", {"query": "CJ Dropshipping API documentation", "max_results": 5}),
    ]
    assert clean_text == ""


def test_parse_fenced_tool_params_json_call():
    register_tool_names(["glob"])
    text = """```json
{"tool": "glob", "params": {"pattern": "data/output/Agents/*.md"}}
```"""

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert clean_text == ""
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "glob"
    assert tool_calls[0].input == {"pattern": "data/output/Agents/*.md"}


def test_parse_issue_264_tool_call_tag_with_arrow_and_cli_args():
    register_tool_names(["setup_organization"])
    text = (
        '[TOOL_CALL] {tool => "setup_organization", "args": { '
        '--action "get_org", --org_id "org_ec164b96357f"}}[/TOOL_CALL]'
    )

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert clean_text == ""
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "setup_organization"
    assert tool_calls[0].input == {
        "action": "get_org",
        "org_id": "org_ec164b96357f",
    }


def test_plain_tool_params_json_ignores_unknown_tool():
    register_tool_names(["glob"])
    text = '{"tool": "unknown_tool", "params": {"pattern": "data/output/Agents/*.md"}}'

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert clean_text == text
    assert tool_calls == []


def test_parse_legacy_nested_function_tool_calls_from_issue_384():
    register_tool_names(["read_file", "list_directory"])
    text = """好的，我来验证修复效果！

<tool_call>
<function=read_file>
<function=file_path>
E:\\Data_Store\\OpenAkita\\workspaces\\default\\data\\plugins\\qiushi-plugin\\plugin.py
</function>
</function>
<function=read_file>
<function=file_path>
E:\\Data_Store\\OpenAkita\\workspaces\\default\\data\\plugins\\qiushi-plugin\\plugin.json
</function>
</function>
<function=list_directory>
<function=path>
E:\\Data_Store\\OpenAkita\\workspaces\\default\\data\\plugins\\qiushi-plugin
</function>
</function>"""

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert clean_text == "好的，我来验证修复效果！"
    assert [(tc.name, tc.input) for tc in tool_calls] == [
        (
            "read_file",
            {
                "file_path": (
                    "E:\\Data_Store\\OpenAkita\\workspaces\\default\\data\\plugins"
                    "\\qiushi-plugin\\plugin.py"
                )
            },
        ),
        (
            "read_file",
            {
                "file_path": (
                    "E:\\Data_Store\\OpenAkita\\workspaces\\default\\data\\plugins"
                    "\\qiushi-plugin\\plugin.json"
                )
            },
        ),
        (
            "list_directory",
            {
                "path": (
                    "E:\\Data_Store\\OpenAkita\\workspaces\\default\\data\\plugins"
                    "\\qiushi-plugin"
                )
            },
        ),
    ]
    assert "<tool_call>" not in clean_text
    assert "<function=" not in clean_text


def test_parse_legacy_function_does_not_emit_empty_arg_tool_call():
    register_tool_names(["read_file"])
    text = "<tool_call><function=read_file>missing structured args</function></tool_call>"

    clean_text, tool_calls = parse_text_tool_calls(text)

    assert clean_text == text
    assert tool_calls == []
