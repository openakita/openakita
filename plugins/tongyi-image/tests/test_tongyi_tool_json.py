"""Workbench-orchestration regression: tongyi-image tools must return JSON.

OrgRuntime's ``_record_plugin_asset_output`` only kicks in when the
plugin tool emits a structured JSON payload exposing ``image_urls /
local_paths / asset_ids``. Plain "Task created: <id>" strings would
bypass it and the produced images would never be attached to the org
task — leading to ``expects_artifact`` verification failures.

Here we exercise the pure projection layer (``_task_to_tool_payload``)
so the contract is locked down without spinning up the full plugin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from plugin import Plugin as TongyiImagePlugin  # noqa: E402


def test_task_to_tool_payload_succeeded_includes_workbench_fields():
    task = {
        "id": "tk_abc",
        "status": "succeeded",
        "mode": "text2img",
        "image_urls": ["https://example.com/a.png", "https://example.com/b.png"],
        "local_image_paths": ["/tmp/a.png", "/tmp/b.png"],
        "asset_ids": ["asset1", "asset2"],
        "prompt": "a quiet forest at dawn",
    }
    payload = TongyiImagePlugin._task_to_tool_payload(task)
    assert payload["ok"] is True
    assert payload["task_id"] == "tk_abc"
    assert payload["status"] == "succeeded"
    # OrgRuntime hook looks for these EXACT keys — do not rename
    assert payload["image_urls"] == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]
    assert payload["local_paths"] == ["/tmp/a.png", "/tmp/b.png"]
    assert payload["asset_ids"] == ["asset1", "asset2"]


def test_task_to_tool_payload_failed_sets_ok_false():
    task = {
        "id": "tk_x",
        "status": "failed",
        "mode": "text2img",
        "image_urls": [],
        "local_image_paths": [],
        "asset_ids": [],
        "error_message": "API rate limited",
    }
    payload = TongyiImagePlugin._task_to_tool_payload(task)
    assert payload["ok"] is False
    assert payload["error_message"] == "API rate limited"


def test_task_to_tool_payload_brief_truncates_prompt():
    long_prompt = "a" * 500
    task = {
        "id": "tk", "status": "succeeded", "mode": "t2i",
        "prompt": long_prompt, "created_at": "2025-01-01T00:00:00Z",
        "image_urls": [], "local_image_paths": [], "asset_ids": [],
    }
    payload = TongyiImagePlugin._task_to_tool_payload(task, brief=True)
    assert payload["prompt"] == "a" * 200
    assert payload["created_at"] == "2025-01-01T00:00:00Z"


def test_task_to_tool_payload_is_json_serialisable():
    task = {
        "id": "tk", "status": "succeeded", "mode": "t2i",
        "image_urls": ["x"], "local_image_paths": ["/tmp/x"], "asset_ids": ["a"],
    }
    payload = TongyiImagePlugin._task_to_tool_payload(task)
    # Must round-trip cleanly: ReAct executor stores the tool result as a
    # plain str, so non-JSON-serialisable types would crash downstream.
    blob = json.dumps(payload, ensure_ascii=False)
    assert json.loads(blob) == payload
