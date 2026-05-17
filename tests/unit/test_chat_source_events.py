"""L1 tests for source provenance extraction in chat SSE."""

from __future__ import annotations

from openakita.api.routes.chat import (
    _extract_mcp_call,
    _extract_source_used,
    _org_file_attachments_to_chat_attachments,
)


def test_extract_source_used_from_openakita_marker():
    event = {
        "type": "tool_call_end",
        "tool": "web_fetch",
        "id": "abc",
        "result": (
            '[OPENAKITA_SOURCE] {"requested_url":"https://example.com/a",'
            '"final_url":"https://example.com/b","hostname":"example.com",'
            '"redirected":true,"status":"ok"}\n'
            "Requested URL: https://example.com/a\n"
        ),
    }

    source = _extract_source_used(event)

    assert source is not None
    assert source["tool_name"] == "web_fetch"
    assert source["tool_use_id"] == "abc"
    assert source["requested_url"] == "https://example.com/a"
    assert source["final_url"] == "https://example.com/b"
    assert source["redirected"] is True


def test_extract_source_used_ignores_regular_tool_result():
    assert _extract_source_used({"type": "tool_call_end", "tool": "run_shell", "result": "ok"}) is None


def test_extract_mcp_call_returns_structured_payload():
    event = {
        "type": "tool_call_end",
        "tool": "call_mcp_tool",
        "id": "tu-1",
        "result": (
            "✅ MCP 工具调用成功:\n"
            "(some natural-language body)\n\n"
            '[OPENAKITA_MCP] {"status":"ok","server":"github","tool":"list_repos",'
            '"auto_connected":true,"reconnected":false}'
        ),
    }
    payload = _extract_mcp_call(event)
    assert payload is not None
    assert payload["server"] == "github"
    assert payload["tool"] == "list_repos"
    assert payload["status"] == "ok"
    assert payload["auto_connected"] is True
    assert payload["tool_use_id"] == "tu-1"


def test_extract_mcp_call_ignores_other_tools():
    assert _extract_mcp_call({"type": "tool_call_end", "tool": "web_fetch", "result": "[OPENAKITA_MCP] {}"}) is None
    assert _extract_mcp_call({"type": "tool_call_end", "tool": "call_mcp_tool", "result": "no marker"}) is None
    assert _extract_mcp_call({"type": "tool_call_end", "tool": "call_mcp_tool", "result": "[OPENAKITA_MCP] not-json"}) is None


def test_org_file_attachments_convert_to_chat_attachments():
    attachments = _org_file_attachments_to_chat_attachments([
        {
            "filename": "方案.md",
            "file_path": r"D:\OpenAkita\workspace\方案.md",
            "file_size": 128,
        },
        {
            "filename": "方案.md",
            "file_path": r"d:\openakita\workspace\方案.md",
            "file_size": 128,
        },
        {"filename": "ignored.md"},
    ])

    assert attachments == [
        {
            "type": "document",
            "name": "方案.md",
            "localPath": r"D:\OpenAkita\workspace\方案.md",
            "size": 128,
            "uploadStatus": "uploaded",
        }
    ]
