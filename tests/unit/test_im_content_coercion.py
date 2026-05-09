from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes.im import router as im_router
from openakita.memory.json_utils import coerce_text
from openakita.memory.relational.encoder import MemoryEncoder


def _plugin_event(status: str = "succeeded") -> dict:
    return {
        "type": "plugin:seedance-video:task_update",
        "data": {
            "task_id": "e53d8a5a-a76",
            "status": status,
        },
    }


def test_coerce_text_formats_plugin_events_and_multimodal_blocks():
    assert coerce_text(_plugin_event()) == (
        "插件 seedance-video 任务更新: succeeded (e53d8a5a-a76)"
    )
    assert coerce_text([{"type": "text", "text": "hello"}, {"type": "image_url"}]) == (
        "hello\n[图片]"
    )


def test_im_routes_preview_structured_content_without_slice_keyerror():
    app = FastAPI()
    app.include_router(im_router)

    session = SimpleNamespace(
        channel="wechat:main",
        chat_id="chat-1",
        user_id="user-1",
        chat_type="private",
        display_name="tester",
        chat_name="",
        state="active",
        last_active="2026-05-09T10:00:00",
        context=SimpleNamespace(
            messages=[
                {
                    "role": "assistant",
                    "content": _plugin_event(),
                    "timestamp": "2026-05-09T10:00:00",
                }
            ]
        ),
    )
    app.state.session_manager = SimpleNamespace(_sessions={"wechat-session": session})
    app.state.gateway = None

    client = TestClient(app)

    sessions_resp = client.get("/api/im/sessions")
    assert sessions_resp.status_code == 200
    assert "插件 seedance-video 任务更新" in sessions_resp.json()["sessions"][0]["lastMessage"]

    messages_resp = client.get("/api/im/sessions/wechat-session/messages")
    assert messages_resp.status_code == 200
    message = messages_resp.json()["messages"][0]
    assert message["content"] == "插件 seedance-video 任务更新: succeeded (e53d8a5a-a76)"


def test_memory_encoder_handles_structured_turn_content():
    encoder = MemoryEncoder(session_id="s1")
    turns = [
        {"role": "assistant", "content": _plugin_event()},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请继续处理这个跨 IM 会话内容，避免切片异常。"},
                {"type": "image_url"},
            ],
        },
    ]

    text = encoder._turns_to_text(turns)
    assert "插件 seedance-video 任务更新" in text
    assert "[图片]" in text

    result = encoder.encode_quick(turns)
    assert result.nodes
    assert all(isinstance(node.content, str) for node in result.nodes)
