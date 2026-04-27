from openakita.core.agent import Agent


def test_tool_trace_summary_omits_rate_limit_control_messages():
    agent = object.__new__(Agent)
    agent._last_finalized_trace = [
        {
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "web_search",
                    "input": {"query": "AI Agent platform comparison"},
                }
            ],
            "tool_results": [
                {
                    "tool_use_id": "call_1",
                    "result_content": "[系统] 工具 web_search 已在本任务中调用 5 次，已达上限。请整合操作或继续下一步。",
                    "is_error": False,
                }
            ],
        }
    ]

    summary = agent.build_tool_trace_summary()

    assert "web_search" in summary
    assert "已达上限" not in summary
    assert "[系统] 工具" not in summary


def test_tool_trace_summary_omits_system_prompt_injections():
    agent = object.__new__(Agent)
    agent._last_finalized_trace = [
        {
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "read_file",
                    "input": {"path": "notes.txt"},
                }
            ],
            "tool_results": [
                {
                    "tool_use_id": "call_1",
                    "result_content": "[系统提示] 检测到重复调用，请直接结束。",
                    "is_error": False,
                }
            ],
        }
    ]

    summary = agent.build_tool_trace_summary()

    assert "read_file" in summary
    assert "[系统提示]" not in summary
    assert "请直接结束" not in summary
