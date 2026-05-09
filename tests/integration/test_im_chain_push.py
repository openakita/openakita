"""IM 通道思维链推送集成测试。

覆盖新功能：IM 通道思维链推送开关。
1. 默认关闭 — emit_progress_event 不发送消息
2. 全局开启 im_chain_push — emit_progress_event 发送消息
3. 会话级覆盖 — session.set_metadata("chain_push", True) 覆盖全局设置
4. 会话级关闭 — session.set_metadata("chain_push", False) 即使全局开启也不发送
5. force=True 绕过开关检查
6. 节流合并 — 短时间内多条消息合并为一条
7. flush_progress — 立即发送缓冲区
8. /chain 命令解析
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.channels.gateway import MessageGateway, ThinkingCommandHandler
from tests.fixtures.factories import create_test_session


@pytest.fixture
def session():
    return create_test_session(channel="telegram", chat_id="c1", user_id="u1")


@pytest.fixture
def gateway():
    sm = MagicMock()
    gw = MessageGateway(session_manager=sm)
    # 进度链路最终走 ``gateway.send(channel=..., chat_id=...)``（内部按 channel
    # 找 adapter）。测试里没注册 telegram adapter，必须把 ``send`` mock 掉，否则
    # ``flush_progress`` / ``_flush`` 都会因为 “No adapter for channel: telegram”
    # 提前返回，``send_to_session`` 也根本不会被触发，从而让 “merge / flush” 类
    # 测试的 ``assert_awaited_once`` 永远不通过。``send_to_session`` 同时 mock
    # 是为了兜底（少数代码路径仍走 session 接口）。
    gw.send_to_session = AsyncMock(return_value="ok")
    gw.send = AsyncMock(return_value="ok")
    return gw


class TestDefaultOff:
    async def test_emit_skipped_when_default_off(self, gateway, session):
        with patch("openakita.config.settings") as mock_s:
            mock_s.im_chain_push = False
            await gateway.emit_progress_event(session, "thinking...")
        gateway.send_to_session.assert_not_awaited()

    async def test_buffer_empty_when_default_off(self, gateway, session):
        with patch("openakita.config.settings") as mock_s:
            mock_s.im_chain_push = False
            await gateway.emit_progress_event(session, "step 1")
        assert gateway._progress_buffers.get(session.session_key) is None


class TestGlobalOn:
    async def test_emit_buffers_when_global_on(self, gateway, session):
        with patch("openakita.config.settings") as mock_s:
            mock_s.im_chain_push = True
            await gateway.emit_progress_event(session, "thinking...")
        buf = gateway._progress_buffers.get(session.session_key, [])
        assert "thinking..." in buf


class TestSessionOverride:
    async def test_session_chain_push_true_overrides_global_off(self, gateway, session):
        session.set_metadata("chain_push", True)
        with patch("openakita.config.settings") as mock_s:
            mock_s.im_chain_push = False
            await gateway.emit_progress_event(session, "progress A")
        buf = gateway._progress_buffers.get(session.session_key, [])
        assert "progress A" in buf

    async def test_session_chain_push_false_overrides_global_on(self, gateway, session):
        session.set_metadata("chain_push", False)
        with patch("openakita.config.settings") as mock_s:
            mock_s.im_chain_push = True
            await gateway.emit_progress_event(session, "progress B")
        buf = gateway._progress_buffers.get(session.session_key, [])
        assert buf is None or "progress B" not in buf


class TestForceBypass:
    async def test_force_true_bypasses_switch(self, gateway, session):
        with patch("openakita.config.settings") as mock_s:
            mock_s.im_chain_push = False
            await gateway.emit_progress_event(session, "forced!", force=True)
        buf = gateway._progress_buffers.get(session.session_key, [])
        assert "forced!" in buf


class TestThrottleMerge:
    async def test_multiple_events_merged(self, gateway, session):
        session.set_metadata("chain_push", True)
        gateway._progress_throttle_seconds = 0.3

        await gateway.emit_progress_event(session, "line1")
        await gateway.emit_progress_event(session, "line2")
        await gateway.emit_progress_event(session, "line3")

        await asyncio.sleep(0.5)

        # 实际 flush 走 ``gateway.send(channel=..., chat_id=..., text=...)``，
        # ``text`` 是 keyword 参数，所以从 ``call_args.kwargs`` 取。
        gateway.send.assert_awaited_once()
        sent_text = gateway.send.call_args.kwargs.get("text", "")
        assert "line1" in sent_text
        assert "line2" in sent_text
        assert "line3" in sent_text


class TestFlushProgress:
    async def test_flush_sends_buffer_immediately(self, gateway, session):
        session.set_metadata("chain_push", True)
        gateway._progress_throttle_seconds = 10

        await gateway.emit_progress_event(session, "buffered1")
        await gateway.emit_progress_event(session, "buffered2")

        await gateway.flush_progress(session)

        gateway.send.assert_awaited_once()
        sent_text = gateway.send.call_args.kwargs.get("text", "")
        assert "buffered1" in sent_text
        assert "buffered2" in sent_text

    async def test_flush_clears_buffer(self, gateway, session):
        session.set_metadata("chain_push", True)
        gateway._progress_throttle_seconds = 10

        await gateway.emit_progress_event(session, "data")
        await gateway.flush_progress(session)

        remaining = gateway._progress_buffers.get(session.session_key, [])
        assert remaining == []

    async def test_flush_noop_when_empty(self, gateway, session):
        await gateway.flush_progress(session)
        gateway.send_to_session.assert_not_awaited()


class TestChainCommand:
    @pytest.fixture
    def handler(self):
        return ThinkingCommandHandler(session_manager=MagicMock())

    async def test_chain_status_query(self, handler, session):
        with patch("openakita.config.settings") as mock_s:
            mock_s.im_chain_push = False
            result = await handler.handle_command("ignored", "/chain", session)
        assert result is not None
        assert "思维链" in result

    async def test_chain_on(self, handler, session):
        result = await handler.handle_command("ignored", "/chain on", session)
        assert "开启" in result
        assert session.get_metadata("chain_push") is True

    async def test_chain_off(self, handler, session):
        session.set_metadata("chain_push", True)
        result = await handler.handle_command("ignored", "/chain off", session)
        assert "关闭" in result
        assert session.get_metadata("chain_push") is False

    async def test_chain_invalid_arg(self, handler, session):
        result = await handler.handle_command("ignored", "/chain maybe", session)
        assert "无效" in result

    async def test_thinking_depth_max(self, handler, session):
        result = await handler.handle_command("ignored", "/thinking_depth max", session)

        assert "最大" in result
        assert session.get_metadata("thinking_depth") == "max"

    async def test_thinking_depth_xhigh_alias(self, handler, session):
        result = await handler.handle_command("ignored", "/thinking_depth xhigh", session)

        assert "最大" in result
        assert session.get_metadata("thinking_depth") == "max"

