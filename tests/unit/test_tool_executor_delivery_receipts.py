from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openakita.core.tool_executor import ToolExecutor
from openakita.tools.handlers import SystemHandlerRegistry


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
