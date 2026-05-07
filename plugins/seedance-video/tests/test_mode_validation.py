"""Tests for ``Plugin._create_task_internal`` mode validation
(Sprint 8 / V1 — addresses the user-reported "i2v / multimodal / edit /
extend 都生不了任务").

The bug was that the UI never wired uploaded assets into the create
body, so the backend silently fell back to text-only content.  These
guards now raise ``HTTPException(400)`` *before* spending money on the
Ark call so the user sees a clear "需要先上传 XX" red banner instead.

We instantiate the Plugin via ``__new__`` and inject only the deps
``_create_task_internal`` actually uses (``_ark`` mock + ``_tm``
mock with ``get_all_config``).  No FastAPI / no event loop scaffolding
required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from _plugin_loader import load_seedance_plugin
from fastapi import HTTPException

Plugin = load_seedance_plugin().Plugin


def _make_plugin() -> Plugin:
    """Build a Plugin without running ``on_load`` (which needs a real
    PluginAPI). We only need the bits ``_create_task_internal`` touches.
    """
    p = Plugin.__new__(Plugin)
    p._ark = AsyncMock()
    p._ark.create_task = AsyncMock(return_value={"id": "ark-stub"})
    p._tm = AsyncMock()
    p._tm.get_all_config = AsyncMock(return_value={})
    p._tm.get_task_by_client_request_id = AsyncMock(return_value=None)
    p._tm.create_task = AsyncMock(side_effect=lambda **kw: {"id": "t-1", **kw})
    p._pending_create_requests = {}
    return p


@pytest.mark.asyncio
async def test_t2v_passes_with_text_only() -> None:
    p = _make_plugin()
    await p._create_task_internal({
        "mode": "t2v",
        "model": "2.0",
        "prompt": "hello",
    })
    # Ark should have been invoked exactly once with text-only content.
    assert p._ark.create_task.await_count == 1


@pytest.mark.asyncio
async def test_i2v_without_image_returns_400() -> None:
    p = _make_plugin()
    with pytest.raises(HTTPException) as excinfo:
        await p._create_task_internal({
            "mode": "i2v",
            "model": "2.0",
            "prompt": "hello",
            # NOTE: no content with image_url — exactly the silent-fail
            # case the bug report described.
        })
    assert excinfo.value.status_code == 400
    assert "首帧" in excinfo.value.detail
    p._ark.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_i2v_with_image_passes() -> None:
    p = _make_plugin()
    await p._create_task_internal({
        "mode": "i2v",
        "model": "2.0",
        "prompt": "hello",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xxx"}, "role": "first_frame"},
        ],
    })
    assert p._ark.create_task.await_count == 1


@pytest.mark.asyncio
async def test_i2v_end_with_only_one_image_returns_400() -> None:
    p = _make_plugin()
    with pytest.raises(HTTPException) as excinfo:
        await p._create_task_internal({
            "mode": "i2v_end",
            "model": "2.0",
            "prompt": "hello",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xxx"}},
            ],
        })
    assert excinfo.value.status_code == 400
    assert "首帧" in excinfo.value.detail or "尾帧" in excinfo.value.detail


@pytest.mark.asyncio
async def test_i2v_end_with_two_images_passes() -> None:
    p = _make_plugin()
    await p._create_task_internal({
        "mode": "i2v_end",
        "model": "2.0",
        "prompt": "hello",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,a"}, "role": "first_frame"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,b"}, "role": "last_frame"},
        ],
    })
    assert p._ark.create_task.await_count == 1
    sent_content = p._ark.create_task.await_args.kwargs["content"]
    assert sent_content[1]["role"] == "first_frame"
    assert sent_content[1]["image_url"] == {"url": "data:image/jpeg;base64,a"}
    assert sent_content[2]["role"] == "last_frame"
    assert sent_content[2]["image_url"] == {"url": "data:image/jpeg;base64,b"}


@pytest.mark.asyncio
async def test_i2v_end_normalizes_legacy_nested_image_roles() -> None:
    p = _make_plugin()
    await p._create_task_internal({
        "mode": "i2v_end",
        "model": "2.0",
        "prompt": "hello",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,a", "role": "first_frame"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,b", "role": "last_frame"}},
        ],
    })

    sent_content = p._ark.create_task.await_args.kwargs["content"]
    assert sent_content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,a"},
        "role": "first_frame",
    }
    assert sent_content[2] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,b"},
        "role": "last_frame",
    }


@pytest.mark.asyncio
async def test_i2v_end_infers_roles_for_two_untagged_images() -> None:
    p = _make_plugin()
    await p._create_task_internal({
        "mode": "i2v_end",
        "model": "2.0",
        "prompt": "hello",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,a"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,b"}},
        ],
    })

    sent_content = p._ark.create_task.await_args.kwargs["content"]
    assert sent_content[1]["role"] == "first_frame"
    assert sent_content[2]["role"] == "last_frame"


@pytest.mark.asyncio
async def test_multimodal_without_media_returns_400() -> None:
    p = _make_plugin()
    with pytest.raises(HTTPException) as excinfo:
        await p._create_task_internal({
            "mode": "multimodal",
            "model": "2.0",
            "prompt": "hello",
            # text only — must reject
        })
    assert excinfo.value.status_code == 400
    assert "多模态" in excinfo.value.detail


@pytest.mark.asyncio
async def test_edit_without_video_returns_400() -> None:
    p = _make_plugin()
    with pytest.raises(HTTPException) as excinfo:
        await p._create_task_internal({
            "mode": "edit",
            "model": "2.0",
            "prompt": "hello",
        })
    assert excinfo.value.status_code == 400
    assert "编辑" in excinfo.value.detail


@pytest.mark.asyncio
async def test_extend_without_video_returns_400() -> None:
    p = _make_plugin()
    with pytest.raises(HTTPException) as excinfo:
        await p._create_task_internal({
            "mode": "extend",
            "model": "2.0",
            "prompt": "hello",
        })
    assert excinfo.value.status_code == 400
    assert "延长" in excinfo.value.detail


@pytest.mark.asyncio
async def test_edit_with_video_passes() -> None:
    p = _make_plugin()
    await p._create_task_internal({
        "mode": "edit",
        "model": "2.0",
        "prompt": "hello",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "video_url", "video_url": {"url": "https://example.com/source.mp4", "role": "edit"}},
        ],
    })
    assert p._ark.create_task.await_count == 1
    sent_content = p._ark.create_task.await_args.kwargs["content"]
    assert sent_content[1] == {
        "type": "video_url",
        "video_url": {"url": "https://example.com/source.mp4"},
        "role": "reference_video",
    }


@pytest.mark.asyncio
async def test_extend_video_role_is_reference_video() -> None:
    p = _make_plugin()
    await p._create_task_internal({
        "mode": "extend",
        "model": "2.0",
        "prompt": "hello",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "video_url", "video_url": {"url": "https://example.com/source.mp4"}, "role": "extend"},
        ],
    })
    sent_content = p._ark.create_task.await_args.kwargs["content"]
    assert sent_content[1]["role"] == "reference_video"


@pytest.mark.asyncio
async def test_multimodal_video_without_role_gets_reference_video() -> None:
    p = _make_plugin()
    await p._create_task_internal({
        "mode": "multimodal",
        "model": "2.0",
        "prompt": "hello",
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "video_url", "video_url": {"url": "https://example.com/source.mp4"}},
        ],
    })
    sent_content = p._ark.create_task.await_args.kwargs["content"]
    assert sent_content[1]["role"] == "reference_video"


@pytest.mark.asyncio
async def test_reference_video_rejects_base64_before_ark_call() -> None:
    p = _make_plugin()
    with pytest.raises(HTTPException) as excinfo:
        await p._create_task_internal({
            "mode": "edit",
            "model": "2.0",
            "prompt": "hello",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,xxx"}, "role": "edit"},
            ],
        })

    assert excinfo.value.status_code == 400
    assert "公网" in excinfo.value.detail
    assert p._ark.create_task.await_count == 0


@pytest.mark.asyncio
async def test_unknown_model_returns_400() -> None:
    p = _make_plugin()
    with pytest.raises(HTTPException) as excinfo:
        await p._create_task_internal({
            "mode": "t2v",
            "model": "totally-not-a-real-model",
            "prompt": "hello",
        })
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_mode_unsupported_by_model_returns_400() -> None:
    """The lite-t2v model only supports t2v — asking for i2v on it
    must surface a clear "模型不支持 X 模式" message, not a 502 from
    Ark."""
    p = _make_plugin()
    with pytest.raises(HTTPException) as excinfo:
        await p._create_task_internal({
            "mode": "i2v",
            "model": "1.0-lite-t2v",
            "prompt": "hello",
        })
    assert excinfo.value.status_code == 400
    assert "不支持" in excinfo.value.detail


@pytest.mark.asyncio
async def test_client_request_id_returns_persisted_task_without_ark_call() -> None:
    """A late retry with the same client_request_id must return the
    already-created task instead of charging for another Ark job."""
    p = _make_plugin()
    existing = {"id": "already-created", "params": {"client_request_id": "req-1"}}
    p._tm.get_task_by_client_request_id = AsyncMock(return_value=existing)

    result = await p._create_task_internal({
        "mode": "t2v",
        "model": "2.0",
        "prompt": "hello",
        "client_request_id": "req-1",
    })

    assert result == existing
    assert p._ark.create_task.await_count == 0
    assert p._tm.create_task.await_count == 0


@pytest.mark.asyncio
async def test_client_request_id_is_stored_in_task_params() -> None:
    p = _make_plugin()
    await p._create_task_internal({
        "mode": "t2v",
        "model": "2.0",
        "prompt": "hello",
        "client_request_id": "req-2",
    })

    kwargs = p._tm.create_task.await_args.kwargs
    assert kwargs["params"]["client_request_id"] == "req-2"
