"""Tests for avatar_comfy_client — mock comfykit, no real network."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from avatar_comfy_client import AvatarComfyClient, WorkflowError


def _fake_settings(**overrides: str) -> dict:
    base = {
        "backend": "runninghub",
        "rh_api_key": "rh-test-key",
        "rh_instance_type": "plus",
        "comfyui_url": "http://127.0.0.1:8188",
        "comfyui_api_key": "",
    }
    base.update(overrides)
    return base


def test_workflow_error_without_id() -> None:
    client = AvatarComfyClient(lambda: _fake_settings())
    with pytest.raises(WorkflowError, match="No workflow_id"):
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            client.submit_workflow("photo_speak", "", {})
        )


def test_config_hash_changes_trigger_rebuild() -> None:
    settings = _fake_settings()
    client = AvatarComfyClient(lambda: settings)
    h1 = client._hash_config(settings)
    settings["rh_api_key"] = "different-key"
    h2 = client._hash_config(settings)
    assert h1 != h2


@patch("avatar_comfy_client.AvatarComfyClient._get_or_create_kit")
def test_submit_workflow_checks_status(mock_kit: MagicMock) -> None:
    result = MagicMock()
    result.status = "failed"
    result.msg = "GPU timeout"
    result.videos = None

    kit_instance = MagicMock()
    kit_instance.execute.return_value = result
    mock_kit.return_value = kit_instance

    client = AvatarComfyClient(lambda: _fake_settings())
    with pytest.raises(WorkflowError, match="failed"):
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            client.submit_workflow("photo_speak", "wf-123", {})
        )


@patch("avatar_comfy_client.AvatarComfyClient._get_or_create_kit")
def test_submit_workflow_success(mock_kit: MagicMock) -> None:
    result = MagicMock()
    result.status = "completed"
    result.videos = ["https://example.com/output.mp4"]
    result.msg = None

    kit_instance = MagicMock()
    kit_instance.execute.return_value = result
    mock_kit.return_value = kit_instance

    client = AvatarComfyClient(lambda: _fake_settings())
    import asyncio
    out = asyncio.get_event_loop().run_until_complete(
        client.submit_workflow("photo_speak", "wf-123", {"image": "test.jpg"})
    )
    assert out["status"] == "completed"
    assert out["video_url"] == "https://example.com/output.mp4"
