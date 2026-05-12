"""飞书 CardKit 流式更新接口契约测试。

回归保护以下三个核心 Bug 的修复：

1. ``PUT /cardkit/v1/cards/{card_id}/elements/{element_id}/content`` 必须传
   ``content`` 纯字符串 + 严格递增的 ``sequence`` + ``uuid`` 幂等键，原实现
   把 ``content`` 嵌套成 ``json.dumps({"tag": "markdown", ...})`` 且缺
   ``sequence``，飞书侧返回 400 Bad Request。
2. ``PATCH /cardkit/v1/cards/{card_id}/settings`` 的 ``settings`` 字段必须是
   JSON 字符串（非 dict），同样需要 ``sequence``。
3. ``finalize_stream`` 失败时优先调用全量覆盖卡片接口（不闪烁），而不是直接
   删除占位卡片导致用户看到「撤回 + 重新发文本」。

外加：``_feishu_ws_loop_exception_handler`` 必须吞掉 ``ConnectionResetError``，
避免 Windows ProactorEventLoop 在 lark-oapi WS 关闭时刷屏。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def adapter(tmp_path):
    """构造一个最小可用的 FeishuAdapter，跳过真实网络。"""
    from openakita.channels.adapters.feishu import FeishuAdapter

    a = FeishuAdapter(
        app_id="cli_test_app",
        app_secret="test_secret",
        media_dir=tmp_path / "media",
        streaming_enabled=True,
    )
    # 短路 token 获取，避免单测触网
    a._tenant_token = "test-token"
    a._tenant_token_expires = 9_999_999_999
    return a


# ---------------------------------------------------------------------------
# _next_cardkit_seq
# ---------------------------------------------------------------------------


def test_next_cardkit_seq_strictly_increasing_per_card(adapter):
    """同一 card_id 的 sequence 必须严格递增；不同 card_id 各自独立。"""
    assert adapter._next_cardkit_seq("card_a") == 1
    assert adapter._next_cardkit_seq("card_a") == 2
    assert adapter._next_cardkit_seq("card_a") == 3
    assert adapter._next_cardkit_seq("card_b") == 1
    assert adapter._next_cardkit_seq("card_a") == 4


# ---------------------------------------------------------------------------
# _build_streaming_card_json
# ---------------------------------------------------------------------------


def test_build_streaming_card_json_contains_streaming_config(adapter):
    """创建卡片时必须把 streaming_mode + streaming_config 内置到 config 字段，
    并保留空 summary 兜底（防止聊天列表停留在「[生成中...]」）。"""
    raw = adapter._build_streaming_card_json("hello", "streaming_content")
    payload = json.loads(raw)

    assert payload["schema"] == "2.0"
    config = payload["config"]
    assert config["streaming_mode"] is True
    assert config["summary"] == {"content": ""}
    sc = config["streaming_config"]
    assert sc["print_strategy"] == "fast"
    assert sc["print_frequency_ms"]["default"] > 0
    assert sc["print_step"]["default"] >= 1

    elements = payload["body"]["elements"]
    assert elements[0]["tag"] == "markdown"
    assert elements[0]["content"] == "hello"
    assert elements[0]["element_id"] == "streaming_content"


# ---------------------------------------------------------------------------
# _update_cardkit_element 请求体
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_cardkit_element_body_is_string_with_sequence(adapter):
    """content 必须是纯字符串，并带上严格递增的 sequence + uuid。"""
    captured: list[tuple[str, str, dict]] = []

    async def fake_api(method, path, body=None, *, validate=True):
        captured.append((method, path, body))
        return {"code": 0, "data": {}}

    adapter._cardkit_api = fake_api  # type: ignore[assignment]

    await adapter._update_cardkit_element("card_x", "el_y", "你好世界")
    await adapter._update_cardkit_element("card_x", "el_y", "你好世界, 第二段")

    assert len(captured) == 2
    method1, path1, body1 = captured[0]
    method2, _path2, body2 = captured[1]

    assert method1 == "PUT"
    assert path1 == "/open-apis/cardkit/v1/cards/card_x/elements/el_y/content"
    # 关键校验：content 是纯字符串，不是嵌套的 JSON
    assert body1["content"] == "你好世界"
    assert isinstance(body1["content"], str)
    assert "tag" not in body1["content"]  # 不应被错误地包成 element JSON
    # 必填字段
    assert isinstance(body1["sequence"], int) and body1["sequence"] >= 1
    assert isinstance(body1.get("uuid"), str) and len(body1["uuid"]) > 0
    # 第二次调用 sequence 必须严格递增
    assert body2["sequence"] > body1["sequence"]


# ---------------------------------------------------------------------------
# _finish_cardkit_card 请求体
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_cardkit_card_settings_is_json_string(adapter):
    """settings 字段必须是 JSON 字符串，含 streaming_mode=False + summary。"""
    captured: list[tuple[str, str, dict]] = []

    async def fake_api(method, path, body=None, *, validate=True):
        captured.append((method, path, body))
        return {"code": 0, "data": {}}

    adapter._cardkit_api = fake_api  # type: ignore[assignment]

    await adapter._finish_cardkit_card("card_z", summary_text="任务完成")

    assert len(captured) == 1
    method, path, body = captured[0]
    assert method == "PATCH"
    assert path == "/open-apis/cardkit/v1/cards/card_z/settings"
    # 关键校验：settings 必须是字符串而不是 dict
    assert isinstance(body["settings"], str)
    parsed = json.loads(body["settings"])
    assert parsed["config"]["streaming_mode"] is False
    assert parsed["config"]["summary"]["content"] == "任务完成"
    assert isinstance(body["sequence"], int) and body["sequence"] >= 1
    assert isinstance(body.get("uuid"), str)


@pytest.mark.asyncio
async def test_finish_cardkit_card_summary_truncated(adapter):
    """超长摘要应截断到合理长度（避免飞书聊天列表展示溢出）。"""
    captured: list[dict] = []

    async def fake_api(method, path, body=None, *, validate=True):
        captured.append(body)
        return {"code": 0}

    adapter._cardkit_api = fake_api  # type: ignore[assignment]

    long_text = "回复" * 200
    await adapter._finish_cardkit_card("card_q", summary_text=long_text)
    parsed = json.loads(captured[0]["settings"])
    assert len(parsed["config"]["summary"]["content"]) <= 60


# ---------------------------------------------------------------------------
# _overwrite_cardkit_card 请求体
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overwrite_cardkit_card_uses_card_json_envelope(adapter):
    """全量覆盖必须按 schema 2.0 + card.type=card_json + sequence 包装。"""
    captured: dict = {}

    async def fake_api(method, path, body=None, *, validate=True):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {"code": 0}

    adapter._cardkit_api = fake_api  # type: ignore[assignment]

    await adapter._overwrite_cardkit_card("card_o", "最终回复内容")

    assert captured["method"] == "PUT"
    assert captured["path"] == "/open-apis/cardkit/v1/cards/card_o"
    body = captured["body"]
    assert body["card"]["type"] == "card_json"
    inner = json.loads(body["card"]["data"])
    assert inner["schema"] == "2.0"
    assert inner["body"]["elements"][0]["content"] == "最终回复内容"
    assert isinstance(body["sequence"], int) and body["sequence"] >= 1
    assert isinstance(body.get("uuid"), str)


# ---------------------------------------------------------------------------
# finalize_stream fallback 优先级
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_stream_prefers_overwrite_over_delete(adapter):
    """当 _patch_card_content 失败但卡片是 CardKit 类型时，必须先尝试
    _overwrite_cardkit_card，避免直接删卡片造成"撤回 + 重发"闪烁。"""
    sk = adapter._make_session_key("chat_aaa", None)
    adapter._thinking_cards[sk] = "msg_id_001"
    adapter._cardkit_cards[sk] = ("card_id_001", "streaming_content")

    # patch 失败
    adapter._patch_card_content = AsyncMock(return_value=False)
    # overwrite 成功
    adapter._overwrite_cardkit_card = AsyncMock()
    # finish（关闭流式态）
    adapter._finish_cardkit_card = AsyncMock()
    # 删除应当不会被调用
    adapter._delete_feishu_message = AsyncMock()

    ok = await adapter.finalize_stream("chat_aaa", "最终回复")

    assert ok is True
    adapter._overwrite_cardkit_card.assert_awaited_once()
    adapter._finish_cardkit_card.assert_awaited()  # 至少一次（覆盖成功后）
    adapter._delete_feishu_message.assert_not_called()
    assert sk not in adapter._thinking_cards
    assert sk not in adapter._cardkit_cards


@pytest.mark.asyncio
async def test_finalize_stream_falls_back_to_delete_when_overwrite_fails(adapter):
    """patch 失败 + overwrite 也失败时，回退到删除占位卡片让 send_message 走文本。"""
    sk = adapter._make_session_key("chat_bbb", None)
    adapter._thinking_cards[sk] = "msg_id_002"
    adapter._cardkit_cards[sk] = ("card_id_002", "streaming_content")

    adapter._patch_card_content = AsyncMock(return_value=False)
    adapter._overwrite_cardkit_card = AsyncMock(side_effect=RuntimeError("403"))
    adapter._finish_cardkit_card = AsyncMock()
    adapter._delete_feishu_message = AsyncMock()

    ok = await adapter.finalize_stream("chat_bbb", "最终回复")

    assert ok is False
    adapter._overwrite_cardkit_card.assert_awaited_once()
    adapter._delete_feishu_message.assert_awaited_once_with("msg_id_002")
    assert sk not in adapter._thinking_cards


@pytest.mark.asyncio
async def test_finalize_stream_skips_overwrite_when_not_cardkit(adapter):
    """非 CardKit 卡片（schema v1 / PatchMessage）不应误调用 overwrite。"""
    sk = adapter._make_session_key("chat_ccc", None)
    adapter._thinking_cards[sk] = "msg_id_003"
    # 注意：没有 _cardkit_cards，模拟 PatchMessage 路径

    adapter._patch_card_content = AsyncMock(return_value=False)
    adapter._overwrite_cardkit_card = AsyncMock()
    adapter._finish_cardkit_card = AsyncMock()
    adapter._delete_feishu_message = AsyncMock()

    ok = await adapter.finalize_stream("chat_ccc", "最终回复")

    assert ok is False
    adapter._overwrite_cardkit_card.assert_not_called()
    adapter._delete_feishu_message.assert_awaited_once_with("msg_id_003")


# ---------------------------------------------------------------------------
# WS loop exception handler
# ---------------------------------------------------------------------------


def test_ws_loop_handler_swallows_connection_reset():
    """ConnectionResetError 必须被吞，避免 ProactorEventLoop 反复打印。"""
    from openakita.channels.adapters.feishu import _feishu_ws_loop_exception_handler

    loop = asyncio.new_event_loop()
    try:
        called: list[dict] = []
        loop.default_exception_handler = lambda ctx: called.append(ctx)  # type: ignore

        ctx = {
            "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
            "exception": ConnectionResetError(10054, "remote host closed"),
        }
        _feishu_ws_loop_exception_handler(loop, ctx)
        assert called == []  # default handler 不应被调用
    finally:
        loop.close()


def test_ws_loop_handler_passes_other_exceptions_through():
    """非 ConnectionResetError 的异常仍要走默认 handler。"""
    from openakita.channels.adapters.feishu import _feishu_ws_loop_exception_handler

    loop = asyncio.new_event_loop()
    try:
        called: list[dict] = []
        loop.default_exception_handler = lambda ctx: called.append(ctx)  # type: ignore

        ctx = {"message": "some unexpected error", "exception": RuntimeError("boom")}
        _feishu_ws_loop_exception_handler(loop, ctx)
        assert len(called) == 1
        assert called[0] is ctx
    finally:
        loop.close()
