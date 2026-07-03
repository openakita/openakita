from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openakita.core.tool_executor import ToolExecutor
from openakita.tools.file import FileTool
from openakita.tools.handlers import SystemHandlerRegistry
from openakita.tools.handlers.filesystem import (
    FilesystemHandler,
)
from openakita.tools.handlers.filesystem import (
    create_handler as create_filesystem_handler,
)
from openakita.tools.handlers.memory import create_handler as create_memory_handler
from openakita.tools.tool_result import (
    ToolResultPayload,
    mutation_effect,
    split_tool_result_payload,
    tool_result_payload,
    visible_tool_content,
)


def test_tool_result_payload_is_not_a_string_carrier():
    payload = tool_result_payload("done", metadata={"effects": [{"action": "write"}]})

    assert not isinstance(payload, str)
    assert split_tool_result_payload(payload) == (
        "done",
        {"effects": [{"action": "write"}]},
    )
    assert visible_tool_content(payload) == "done"
    assert split_tool_result_payload("done") == ("done", {})


@pytest.mark.asyncio
async def test_org_submit_deliverable_receipts_are_captured(monkeypatch):
    """org_submit_deliverable 的附件回执必须进入 TaskVerify 的证据链。"""

    payload = {
        "ok": True,
        "submitted_to": "chief-editor",
        "chain_id": "chain-1",
        "receipts": [
            {
                "status": "submitted",
                "filename": "coffee-plan.md",
                "file_path": "/tmp/coffee-plan.md",
                "file_size": 128,
                "source_node": "writer-a",
                "submitted_to": "chief-editor",
            }
        ],
        "message": "交付物已提交给 chief-editor（附带 1 个文件附件），等待验收。",
    }

    async def handler(tool_name: str, params: dict) -> str:
        assert tool_name == "org_submit_deliverable"
        assert params == {"deliverable": "咖啡策划案"}
        return json.dumps(payload, ensure_ascii=False)

    registry = SystemHandlerRegistry()
    registry.register("org", handler, ["org_submit_deliverable"])
    executor = ToolExecutor(registry)
    monkeypatch.setattr(
        executor,
        "check_permission",
        lambda _tool_name, _tool_input: SimpleNamespace(behavior="allow"),
    )

    _results, executed, receipts = await executor.execute_batch(
        [
            {
                "id": "call-1",
                "name": "org_submit_deliverable",
                "input": {"deliverable": "咖啡策划案"},
            }
        ],
        capture_delivery_receipts=True,
    )

    assert executed == ["org_submit_deliverable"]
    assert receipts == payload["receipts"]


@pytest.mark.asyncio
async def test_tool_result_payload_metadata_is_preserved(monkeypatch):
    effect = mutation_effect(action="write", target="file", path="/tmp/report.md")

    async def handler(tool_name: str, params: dict):
        assert tool_name == "write_file"
        assert params == {"path": "/tmp/report.md", "content": "ok"}
        return tool_result_payload(
            "文件已写入: /tmp/report.md",
            metadata={"effects": [effect]},
        )

    registry = SystemHandlerRegistry()
    registry.register("fs", handler, ["write_file"])
    executor = ToolExecutor(registry)
    monkeypatch.setattr(
        executor,
        "check_permission",
        lambda _tool_name, _tool_input: SimpleNamespace(behavior="allow"),
    )

    results, executed, _receipts = await executor.execute_batch(
        [
            {
                "id": "call-write",
                "name": "write_file",
                "input": {"path": "/tmp/report.md", "content": "ok"},
            }
        ]
    )

    assert executed == ["write_file"]
    assert results[0]["metadata"]["effects"] == [effect]


@pytest.mark.asyncio
async def test_public_filesystem_handler_returns_visible_text(tmp_path):
    agent = SimpleNamespace(file_tool=FileTool(str(tmp_path)))
    handler = FilesystemHandler(agent)

    result = await handler.handle(
        "write_file",
        {"path": "report.md", "content": "ok"},
    )

    assert not isinstance(result, ToolResultPayload)
    assert "文件已写入" in result


@pytest.mark.asyncio
async def test_filesystem_create_handler_preserves_metadata_for_executor(tmp_path, monkeypatch):
    agent = SimpleNamespace(file_tool=FileTool(str(tmp_path)))
    registry = SystemHandlerRegistry()
    registry.register("filesystem", create_filesystem_handler(agent))
    executor = ToolExecutor(registry)
    monkeypatch.setattr(
        executor,
        "check_permission",
        lambda _tool_name, _tool_input: SimpleNamespace(behavior="allow"),
    )

    results, executed, _receipts = await executor.execute_batch(
        [
            {
                "id": "call-write",
                "name": "write_file",
                "input": {"path": "report.md", "content": "ok"},
            }
        ]
    )

    assert executed == ["write_file"]
    assert results[0]["metadata"]["effects"][0]["action"] == "write"
    assert results[0]["metadata"]["receipts"][0]["action"] == "write"


@pytest.mark.asyncio
async def test_memory_create_handler_preserves_metadata_for_executor(monkeypatch):
    memory_manager = SimpleNamespace(
        store=SimpleNamespace(search_semantic=lambda *args, **kwargs: []),
        add_memory=lambda *args, **kwargs: "mem-1",
    )
    agent = SimpleNamespace(memory_manager=memory_manager, profile_manager=None)
    registry = SystemHandlerRegistry()
    registry.register("memory", create_memory_handler(agent))
    executor = ToolExecutor(registry)
    monkeypatch.setattr(
        executor,
        "check_permission",
        lambda _tool_name, _tool_input: SimpleNamespace(behavior="allow"),
    )

    results, executed, _receipts = await executor.execute_batch(
        [
            {
                "id": "call-memory",
                "name": "add_memory",
                "input": {"content": "用户喜欢简洁回答", "scope": "global"},
            }
        ]
    )

    assert executed == ["add_memory"]
    assert results[0]["metadata"]["effects"][0]["action"] == "write"
    assert results[0]["metadata"]["receipts"][0]["action"] == "write"
