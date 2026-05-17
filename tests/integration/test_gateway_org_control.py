"""IM 端"组织指挥台"控制命令的单元/集成测试。

覆盖三条新增 fast-path 命令：``/org cancel``、``/org running``、``/org last``。

这些命令的核心约束是「**绕过消息队列与 per-session 串行**」——即在
``_try_handle_org_command`` 还在 ``await queue.get()`` 阻塞等待 ``org_command_done``
时，用户用上面三条指令仍然能立刻得到响应。这里直接通过 ``_on_message``
和 ``_handle_org_control_command`` 验证这条契约。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import openakita.channels.gateway as gateway_module
from openakita.channels.gateway import MessageGateway
from tests.fixtures.factories import create_channel_message, create_test_session


@pytest.fixture()
def session_manager():
    """提供一个最小可用的 SessionManager mock。"""
    sm = MagicMock()
    sm.mark_dirty = MagicMock()
    return sm


@pytest.fixture()
def gateway(session_manager):
    """构造一个尽量真实的 MessageGateway，把外发响应替换成可断言的 AsyncMock。"""
    gw = MessageGateway(session_manager=session_manager, agent_handler=None)
    gw._send_response = AsyncMock()
    return gw


@pytest.fixture()
def bound_session(session_manager):
    """一个已经存在、已绑定组织、并写入了 current_org_command 的会话。"""
    session = create_test_session(
        chat_id="chat-org-1",
        channel="telegram",
        user_id="user-org-1",
    )
    # 让 session_manager.get_session(...) 返回这个 session
    session_manager.get_session = MagicMock(return_value=session)
    session.set_metadata("bound_org_id", "org_abcdef123456")
    session.set_metadata(
        "current_org_command",
        {
            "org_id": "org_abcdef123456",
            "org_name": "内容工作室",
            "command_id": "cmd_xyz789",
            "task_preview": "写一段产品文案",
            "started_at": 1717000000.0,
        },
    )
    return session


class TestOrgControlCommandDetection:
    """``_is_org_control_command`` 与 ``_on_message`` fast-path 命中规则。"""

    @pytest.mark.parametrize(
        "text",
        [
            "/org cancel",
            "/org running",
            "/org last",
            "/组织 取消",
            "/组织 在跑",
            "/组织 上次",
            "  /Org Cancel  ",  # 前后空白 / 大小写
            "/ORG RUNNING",
        ],
    )
    def test_recognizes_control_commands(self, gateway, text):
        assert gateway._is_org_control_command(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "你好",
            "/org bind 内容工作室",  # 这是 bind，不是 control
            "/org list",
            "/cancel",  # 普通中断
            "/org cancel extra",  # 多余参数 → 不算 fast-path
            "@组织 干点活",
        ],
    )
    def test_does_not_misfire_on_other_text(self, gateway, text):
        assert gateway._is_org_control_command(text) is None


class TestOrgCancelCommand:
    async def test_cancel_calls_service_and_replies(self, gateway, bound_session):
        msg = create_channel_message(text="/org cancel", chat_id="chat-org-1")

        fake_svc = MagicMock()
        fake_svc.cancel = AsyncMock(return_value={
            "ok": True,
            "command_id": "cmd_xyz789",
            "cancelled_roots": ["root_a"],
        })
        with patch.object(
            gateway_module,
            "MessageGateway",  # placeholder to ensure module imported
        ):
            pass
        from openakita.orgs import command_service as cs_module
        with patch.object(cs_module, "get_command_service", return_value=fake_svc):
            handled = await gateway._handle_org_control_command(msg, "/org cancel")

        assert handled is True
        fake_svc.cancel.assert_awaited_once_with("org_abcdef123456", "cmd_xyz789")
        gateway._send_response.assert_awaited()
        reply = gateway._send_response.await_args.args[1]
        assert "已发起取消" in reply
        assert "cmd_xyz789" in reply

    async def test_cancel_with_no_running_command_replies_friendly(
        self, gateway, session_manager
    ):
        session = create_test_session(chat_id="chat-empty", channel="telegram")
        session_manager.get_session = MagicMock(return_value=session)
        msg = create_channel_message(text="/org cancel", chat_id="chat-empty")
        handled = await gateway._handle_org_control_command(msg, "/org cancel")
        assert handled is True
        reply = gateway._send_response.await_args.args[1]
        assert "没有正在跑的组织命令" in reply

    async def test_cancel_handles_already_done(self, gateway, bound_session):
        msg = create_channel_message(text="/org cancel", chat_id="chat-org-1")
        fake_svc = MagicMock()
        fake_svc.cancel = AsyncMock(return_value={"ok": True, "already_done": True})
        from openakita.orgs import command_service as cs_module
        with patch.object(cs_module, "get_command_service", return_value=fake_svc):
            await gateway._handle_org_control_command(msg, "/org cancel")
        reply = gateway._send_response.await_args.args[1]
        assert "已经结束" in reply

    async def test_cancel_when_service_unavailable(self, gateway, bound_session):
        msg = create_channel_message(text="/org cancel", chat_id="chat-org-1")
        from openakita.orgs import command_service as cs_module
        with patch.object(cs_module, "get_command_service", return_value=None):
            await gateway._handle_org_control_command(msg, "/org cancel")
        reply = gateway._send_response.await_args.args[1]
        assert "尚未初始化" in reply

    async def test_cancel_when_session_missing(self, gateway, session_manager):
        session_manager.get_session = MagicMock(return_value=None)
        msg = create_channel_message(text="/org cancel", chat_id="chat-unknown")
        handled = await gateway._handle_org_control_command(msg, "/org cancel")
        assert handled is True
        reply = gateway._send_response.await_args.args[1]
        assert "会话不存在" in reply


class TestOrgRunningCommand:
    async def test_running_shows_live_status(self, gateway, bound_session):
        msg = create_channel_message(text="/org running", chat_id="chat-org-1")
        fake_svc = MagicMock()
        fake_svc.get_status = MagicMock(return_value={
            "status": "running",
            "phase": "dispatching",
            "elapsed_s": 12.4,
            "busy_nodes": ["node_writer", "node_designer"],
            "blockers": [],
            "warning": None,
        })
        from openakita.orgs import command_service as cs_module
        with patch.object(cs_module, "get_command_service", return_value=fake_svc):
            await gateway._handle_org_control_command(msg, "/org running")
        reply = gateway._send_response.await_args.args[1]
        assert "正在跑" in reply
        assert "内容工作室" in reply
        assert "cmd_xyz789" in reply
        assert "dispatching" in reply
        assert "12" in reply  # elapsed_s rounding
        assert "node_writer" in reply

    async def test_running_without_current_command(self, gateway, session_manager):
        session = create_test_session(chat_id="chat-empty2", channel="telegram")
        session_manager.get_session = MagicMock(return_value=session)
        msg = create_channel_message(text="/org running", chat_id="chat-empty2")
        await gateway._handle_org_control_command(msg, "/org running")
        reply = gateway._send_response.await_args.args[1]
        assert "没有正在跑" in reply

    async def test_running_tolerates_service_error(self, gateway, bound_session):
        """get_status 抛错时，应当退化为只展示 metadata 中的快照，不应崩。"""
        msg = create_channel_message(text="/org running", chat_id="chat-org-1")
        fake_svc = MagicMock()
        fake_svc.get_status = MagicMock(side_effect=RuntimeError("boom"))
        from openakita.orgs import command_service as cs_module
        with patch.object(cs_module, "get_command_service", return_value=fake_svc):
            await gateway._handle_org_control_command(msg, "/org running")
        reply = gateway._send_response.await_args.args[1]
        assert "内容工作室" in reply
        assert "cmd_xyz789" in reply


class TestOrgLastCommand:
    async def test_last_after_finish(self, gateway, bound_session):
        # 模拟 _try_handle_org_command 命令结束时的收尾
        gateway._finish_current_org_command(
            bound_session,
            result_text="这是上一条组织命令的最终结果文本。",
        )
        msg = create_channel_message(text="/org last", chat_id="chat-org-1")
        await gateway._handle_org_control_command(msg, "/org last")
        reply = gateway._send_response.await_args.args[1]
        assert "上次组织命令" in reply
        assert "内容工作室" in reply
        assert "这是上一条组织命令的最终结果文本" in reply

    async def test_last_when_no_history(self, gateway, session_manager):
        session = create_test_session(chat_id="chat-empty3", channel="telegram")
        session_manager.get_session = MagicMock(return_value=session)
        msg = create_channel_message(text="/org last", chat_id="chat-empty3")
        await gateway._handle_org_control_command(msg, "/org last")
        reply = gateway._send_response.await_args.args[1]
        assert "没有任何已完成的组织命令" in reply


class TestOrgCommandsDoNotStartTyping:
    async def test_org_list_returns_before_typing_placeholder(self, gateway, session_manager):
        """短组织命令应直接回复，不创建“思考中”占位卡片。"""
        session = create_test_session(chat_id="chat-list", channel="telegram")
        session_manager.get_session = MagicMock(return_value=session)
        gateway.bot_config.is_enabled = MagicMock(return_value=True)
        fake_adapter = SimpleNamespace(
            send_typing=AsyncMock(),
            clear_typing=AsyncMock(),
        )
        gateway._adapters["telegram"] = fake_adapter
        fake_mgr = MagicMock()
        fake_mgr.list_orgs = MagicMock(
            return_value=[
                {
                    "name": "内容工作室",
                    "status": "active",
                    "id": "org_content",
                }
            ]
        )
        msg = create_channel_message(text="/org list", chat_id="chat-list")

        with patch.object(gateway, "_get_org_manager", return_value=fake_mgr):
            await gateway._handle_message(msg)

        gateway._send_response.assert_awaited()
        reply = gateway._send_response.await_args.args[1]
        assert "当前共 1 个组织" in reply
        fake_adapter.send_typing.assert_not_awaited()


class TestPatchOrgStatusCard:
    """``_patch_org_status_card`` 在不同通道上的就地更新行为。"""

    async def test_telegram_uses_edit_message(self, gateway):
        """带 ``edit_message`` 能力的通道（Telegram 等）应该走 edit_message
        而不是退化为新发一条消息——这是消除"灰色进度条刷屏"的核心。"""
        msg = create_channel_message(text="/org noop", chat_id="chat-tg-1")
        msg.channel = "telegram"
        fake_adapter = MagicMock()
        fake_adapter.has_capability = MagicMock(return_value=True)
        fake_adapter.edit_message = AsyncMock(return_value=True)
        gateway._adapters["telegram"] = fake_adapter

        ok = await gateway._patch_org_status_card(msg, "msg_42", "新进度内容")

        assert ok is True
        fake_adapter.edit_message.assert_awaited_once()
        call_args, call_kwargs = fake_adapter.edit_message.await_args
        assert call_args[0] == "chat-tg-1"
        assert call_args[1] == "msg_42"
        assert call_args[2] == "新进度内容"
        assert call_kwargs.get("parse_mode") == "markdown"

    async def test_feishu_uses_patch_card_content(self, gateway):
        """飞书继续走 CardKit/PatchMessage，签名兼容 ``final`` 关键字。"""
        msg = create_channel_message(text="/org noop", chat_id="chat-fs-1")
        msg.channel = "feishu"
        fake_adapter = MagicMock()
        fake_adapter._patch_card_content = AsyncMock(return_value=True)
        fake_adapter._make_session_key = MagicMock(return_value="chat-fs-1")
        gateway._adapters["feishu"] = fake_adapter

        ok = await gateway._patch_org_status_card(msg, "card_x", "进度", done=True)

        assert ok is True
        fake_adapter._patch_card_content.assert_awaited_once()
        _, kwargs = fake_adapter._patch_card_content.await_args
        assert kwargs.get("final") is True

    async def test_no_capability_returns_false(self, gateway):
        """未具备 ``edit_message`` 能力的非飞书通道应返回 False，由调用方决定
        要不要退化为新发一条消息。"""
        msg = create_channel_message(text="/org noop", chat_id="chat-x")
        msg.channel = "wework_bot"
        fake_adapter = MagicMock()
        fake_adapter.has_capability = MagicMock(return_value=False)
        gateway._adapters["wework_bot"] = fake_adapter

        ok = await gateway._patch_org_status_card(msg, "msg_1", "content")
        assert ok is False


class TestSessionMetadataLifecycle:
    """current_org_command / last_org_command 两个 metadata 槽位的迁移正确性。"""

    def test_record_then_finish_moves_slot(self, gateway, bound_session):
        assert isinstance(bound_session.get_metadata("current_org_command"), dict)
        assert bound_session.get_metadata("last_org_command") is None

        gateway._finish_current_org_command(bound_session, result_text="DONE")

        assert bound_session.get_metadata("current_org_command") is None
        last = bound_session.get_metadata("last_org_command")
        assert isinstance(last, dict)
        assert last.get("result_text") == "DONE"
        assert last.get("command_id") == "cmd_xyz789"
        assert last.get("finished_at") is not None

    def test_finish_with_no_current_is_noop_on_last(self, gateway, session_manager):
        """没有 current_org_command 时 finish 不应错误地写出 last_org_command。"""
        session = create_test_session(chat_id="chat-clean", channel="telegram")
        gateway._finish_current_org_command(session, result_text="X")
        assert session.get_metadata("current_org_command") is None
        assert session.get_metadata("last_org_command") is None

    def test_record_overwrites_previous_current(self, gateway, bound_session):
        """同会话连续提交两条命令时，current_org_command 应被新值覆盖。"""
        gateway._record_current_org_command(
            bound_session,
            org_id="org_new",
            org_name="新组织",
            command_id="cmd_new",
            task_preview="another task",
        )
        cur = bound_session.get_metadata("current_org_command")
        assert cur["command_id"] == "cmd_new"
        assert cur["org_name"] == "新组织"
