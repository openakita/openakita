from openakita.events import StreamEventType, normalize_stream_event


def test_tool_event_normalization_adds_stable_aliases():
    event = normalize_stream_event(
        {"type": StreamEventType.TOOL_CALL_START.value, "tool": "run_shell", "id": "abc"}
    )
    assert event["protocol_version"] == 1
    assert event["tool_name"] == "run_shell"
    assert event["call_id"] == "abc"


def test_security_confirm_normalization_adds_confirm_aliases():
    event = normalize_stream_event(
        {"type": StreamEventType.SECURITY_CONFIRM.value, "tool": "run_powershell", "id": "confirm-1"}
    )
    assert event["tool_name"] == "run_powershell"
    assert event["confirm_id"] == "confirm-1"
    assert event["call_id"] == "confirm-1"


def test_todo_created_normalization_keeps_legacy_and_stable_fields():
    event = normalize_stream_event(
        {
            "type": StreamEventType.TODO_CREATED.value,
            "plan": {
                "id": "plan-1",
                "taskSummary": "整理任务",
                "steps": [{"id": "step-1", "description": "第一步"}],
            },
        }
    )
    assert event["plan"]["taskSummary"] == "整理任务"
    assert event["plan"]["task_summary"] == "整理任务"
    assert event["plan"]["steps"][0]["step_id"] == "step-1"


def test_source_used_normalization_adds_stable_fields():
    event = normalize_stream_event(
        {
            "type": StreamEventType.SOURCE_USED.value,
            "tool": "web_fetch",
            "id": "tool-1",
            "requested_url": "https://example.com/a",
        }
    )
    assert event["tool_name"] == "web_fetch"
    assert event["tool_use_id"] == "tool-1"
    assert event["final_url"] == "https://example.com/a"
    assert event["from_cache"] is False


def test_mcp_call_normalization_adds_stable_fields():
    event = normalize_stream_event(
        {
            "type": StreamEventType.MCP_CALL.value,
            "id": "tu-1",
            "server": "github",
            "tool": "list_repos",
            "status": "ok",
        }
    )
    assert event["tool_use_id"] == "tu-1"
    assert event["server"] == "github"
    assert event["tool"] == "list_repos"
    assert event["auto_connected"] is False
    assert event["reconnected"] is False
    assert event["error"] == ""


def test_python_stream_events_include_frontend_known_enrichments():
    assert StreamEventType.ENDPOINT_NOTICE.value == "endpoint_notice"
    assert StreamEventType.SOURCE_USED.value == "source_used"
    assert StreamEventType.MCP_CALL.value == "mcp_call"
