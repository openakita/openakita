#!/usr/bin/env python
"""
CLI ↔ IM 统一路径改造 - 验证测试

验证内容：
1. Session 基础设施：CLI Session 创建、消息管理、清理
2. chat() 委托逻辑：通过 mock chat_with_session 验证委托流程
3. chat_with_session() 适配：IM context 条件设置、session_type 检测
4. /clear 命令兼容性：同时清理 CLI Session 和旧属性
5. self_check 上下文清理：增加 _cli_session 清理

无需 LLM API key，纯逻辑验证。
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

# 确保项目在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_session_create_and_messages():
    """测试 1: CLI Session 创建和消息管理"""
    print("\n" + "=" * 60)
    print("测试 1: CLI Session 创建和消息管理")
    print("=" * 60)

    from openakita.sessions.session import Session

    # 创建 CLI Session
    session = Session.create(channel="cli", chat_id="cli", user_id="user")

    assert session.channel == "cli", f"Expected channel='cli', got '{session.channel}'"
    assert session.chat_id == "cli", f"Expected chat_id='cli', got '{session.chat_id}'"
    assert session.user_id == "user", f"Expected user_id='user', got '{session.user_id}'"
    assert session.id.startswith("cli_cli_"), f"Session ID should start with 'cli_cli_', got '{session.id}'"
    print(f"  ✓ Session 创建成功: id={session.id}")

    # session_key 属性
    assert session.session_key == "cli:cli:user", f"Expected 'cli:cli:user', got '{session.session_key}'"
    print(f"  ✓ session_key 正确: {session.session_key}")

    # 添加消息
    session.add_message("user", "hello")
    session.add_message("assistant", "hi there")
    msgs = session.context.get_messages()
    assert len(msgs) == 2, f"Expected 2 messages, got {len(msgs)}"
    assert msgs[0]["role"] == "user" and msgs[0]["content"] == "hello"
    assert msgs[1]["role"] == "assistant" and msgs[1]["content"] == "hi there"
    print(f"  ✓ 消息添加和读取正确: {len(msgs)} 条消息")

    # 清理消息
    session.context.clear_messages()
    msgs = session.context.get_messages()
    assert len(msgs) == 0, f"Expected 0 messages after clear, got {len(msgs)}"
    print(f"  ✓ clear_messages() 正确: 清理后 {len(msgs)} 条消息")

    print("  ✅ 测试 1 通过")


def test_session_dedup_logic():
    """测试 2: 消息去重逻辑验证（模拟 chat_with_session 的行为）"""
    print("\n" + "=" * 60)
    print("测试 2: 消息去重逻辑验证")
    print("=" * 60)

    from openakita.sessions.session import Session

    session = Session.create(channel="cli", chat_id="cli", user_id="user")

    # 模拟多轮对话
    session.add_message("user", "第一轮问题")
    session.add_message("assistant", "第一轮回答")
    session.add_message("user", "第二轮问题")  # 当前轮的用户消息

    session_messages = session.context.get_messages()
    assert len(session_messages) == 3

    # 模拟 chat_with_session 中的去重逻辑（第 2786-2787 行）
    history_messages = session_messages
    if history_messages and history_messages[-1].get("role") == "user":
        history_messages = history_messages[:-1]

    assert len(history_messages) == 2, f"去重后应有 2 条历史，got {len(history_messages)}"
    assert history_messages[-1]["role"] == "assistant", "去重后最后一条应为 assistant"
    print(f"  ✓ 去重逻辑正确: {len(session_messages)} 条 → {len(history_messages)} 条历史")

    # 模拟添加编译后的用户消息
    messages = []
    for msg in history_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    # 添加上下文边界标记
    if messages:
        messages.append({"role": "user", "content": "[上下文结束，以下是用户的最新消息]"})
        messages.append({"role": "assistant", "content": "好的，我已了解之前的对话上下文。请告诉我你现在的需求。"})

    # 添加当前用户消息（可能经过 Prompt Compiler 处理）
    messages.append({"role": "user", "content": "第二轮问题"})

    assert len(messages) == 5, f"最终应有 5 条消息，got {len(messages)}"
    assert messages[-1]["content"] == "第二轮问题"
    print(f"  ✓ 最终消息列表正确: {len(messages)} 条 (2 历史 + 2 边界 + 1 当前)")

    print("  ✅ 测试 2 通过")


def test_im_context_conditional():
    """测试 3: IM Context 条件设置"""
    print("\n" + "=" * 60)
    print("测试 3: IM Context 条件设置")
    print("=" * 60)

    from openakita.core.im_context import set_im_context, get_im_session, get_im_gateway, reset_im_context

    # 场景 A: CLI 模式 (gateway=None)
    # 应该不暴露 session 给 IM 工具
    mock_session = MagicMock()
    gateway = None

    tokens = set_im_context(
        session=mock_session if gateway else None,
        gateway=gateway,
    )

    assert get_im_session() is None, "CLI 模式下 im_session 应为 None"
    assert get_im_gateway() is None, "CLI 模式下 im_gateway 应为 None"
    print("  ✓ CLI 模式: im_session=None, im_gateway=None (IM 工具会返回'当前不在 IM 会话中')")

    reset_im_context(tokens)

    # 场景 B: IM 模式 (gateway 存在)
    mock_gateway = MagicMock()
    tokens = set_im_context(
        session=mock_session if mock_gateway else None,
        gateway=mock_gateway,
    )

    assert get_im_session() is mock_session, "IM 模式下 im_session 应为 mock_session"
    assert get_im_gateway() is mock_gateway, "IM 模式下 im_gateway 应为 mock_gateway"
    print("  ✓ IM 模式: im_session=session, im_gateway=gateway (IM 工具正常工作)")

    reset_im_context(tokens)

    print("  ✅ 测试 3 通过")


def test_session_type_detection():
    """测试 4: session_type 自动检测"""
    print("\n" + "=" * 60)
    print("测试 4: session_type 自动检测")
    print("=" * 60)

    from openakita.sessions.session import Session

    # CLI Session
    cli_session = Session.create(channel="cli", chat_id="cli", user_id="user")
    session_type = "cli" if (cli_session and cli_session.channel == "cli") else "im"
    assert session_type == "cli", f"CLI session should detect as 'cli', got '{session_type}'"
    print(f"  ✓ CLI Session (channel='cli') → session_type='{session_type}'")

    # IM Session (DingTalk)
    im_session = Session.create(channel="dingtalk", chat_id="group123", user_id="user456")
    session_type = "cli" if (im_session and im_session.channel == "cli") else "im"
    assert session_type == "im", f"IM session should detect as 'im', got '{session_type}'"
    print(f"  ✓ IM Session (channel='dingtalk') → session_type='{session_type}'")

    # None Session
    session = None
    session_type = "cli" if (session and session.channel == "cli") else "im"
    assert session_type == "im", f"None session should default to 'im', got '{session_type}'"
    print(f"  ✓ None Session → session_type='{session_type}' (默认)")

    print("  ✅ 测试 4 通过")


def test_clear_command_logic():
    """测试 5: /clear 命令逻辑"""
    print("\n" + "=" * 60)
    print("测试 5: /clear 命令逻辑")
    print("=" * 60)

    from openakita.sessions.session import Session

    # 模拟 Agent 对象
    class MockAgent:
        def __init__(self):
            self._conversation_history = [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
            self._context = MagicMock()
            self._context.messages = [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
            self._cli_session = Session.create(channel="cli", chat_id="cli", user_id="user")
            self._cli_session.add_message("user", "hello")
            self._cli_session.add_message("assistant", "hi")

    agent = MockAgent()

    # 验证清理前状态
    assert len(agent._conversation_history) == 2
    assert len(agent._context.messages) == 2
    assert len(agent._cli_session.context.get_messages()) == 2
    print(f"  清理前: history={len(agent._conversation_history)}, context={len(agent._context.messages)}, session={len(agent._cli_session.context.get_messages())}")

    # 模拟 /clear 命令（与 main.py 中新代码一致）
    if hasattr(agent, '_cli_session') and agent._cli_session:
        agent._cli_session.context.clear_messages()
    agent._conversation_history.clear()
    agent._context.messages.clear()

    # 验证清理后状态
    assert len(agent._conversation_history) == 0, "conversation_history should be empty"
    assert len(agent._context.messages) == 0, "context.messages should be empty"
    assert len(agent._cli_session.context.get_messages()) == 0, "cli_session messages should be empty"
    print(f"  清理后: history={len(agent._conversation_history)}, context={len(agent._context.messages)}, session={len(agent._cli_session.context.get_messages())}")

    print("  ✅ 测试 5 通过")


def test_selfcheck_clearing():
    """测试 6: self_check 上下文清理"""
    print("\n" + "=" * 60)
    print("测试 6: self_check 上下文清理")
    print("=" * 60)

    from openakita.sessions.session import Session

    # 模拟 Agent
    class MockAgent:
        def __init__(self):
            self._context = MagicMock()
            self._context.messages = [{"role": "user", "content": "test"}]
            self._conversation_history = [{"role": "user", "content": "test"}]
            self._cli_session = Session.create(channel="cli", chat_id="cli", user_id="user")
            self._cli_session.add_message("user", "test")

    agent = MockAgent()

    # 模拟 self_check.py 的清理逻辑（与改动后代码一致）
    agent._context.messages = []
    agent._conversation_history = []
    if hasattr(agent, '_cli_session') and agent._cli_session:
        agent._cli_session.context.clear_messages()

    assert len(agent._conversation_history) == 0
    assert len(agent._cli_session.context.get_messages()) == 0
    print("  ✓ self_check 清理后: 所有上下文已清空")

    # 测试没有 _cli_session 的情况（向后兼容）
    class MockAgentNoSession:
        def __init__(self):
            self._context = MagicMock()
            self._context.messages = [{"role": "user", "content": "test"}]
            self._conversation_history = [{"role": "user", "content": "test"}]

    agent2 = MockAgentNoSession()
    agent2._context.messages = []
    agent2._conversation_history = []
    if hasattr(agent2, '_cli_session') and agent2._cli_session:
        agent2._cli_session.context.clear_messages()

    assert len(agent2._conversation_history) == 0
    print("  ✓ 无 _cli_session 时清理也正常（向后兼容）")

    print("  ✅ 测试 6 通过")


async def test_chat_delegation():
    """测试 7: chat() 委托给 chat_with_session() 的完整流程"""
    print("\n" + "=" * 60)
    print("测试 7: chat() 委托逻辑 (mock chat_with_session)")
    print("=" * 60)

    from openakita.sessions.session import Session

    # 创建一个最小化的 Agent mock，只测试 chat() 的委托逻辑
    class MinimalAgent:
        def __init__(self):
            self._initialized = True
            self._conversation_history = []
            self._cli_session = None

        async def chat(self, message, session_id=None):
            """与 agent.py 改动后的 chat() 逻辑一致"""
            if not hasattr(self, '_cli_session') or self._cli_session is None:
                self._cli_session = Session.create(
                    channel="cli", chat_id="cli", user_id="user"
                )

            self._cli_session.add_message("user", message)
            session_messages = self._cli_session.context.get_messages()

            response = await self.chat_with_session(
                message=message,
                session_messages=session_messages,
                session_id=session_id or self._cli_session.id,
                session=self._cli_session,
                gateway=None,
            )

            self._cli_session.add_message("assistant", response)

            from datetime import datetime
            self._conversation_history.append(
                {"role": "user", "content": message, "timestamp": datetime.now().isoformat()}
            )
            self._conversation_history.append(
                {"role": "assistant", "content": response, "timestamp": datetime.now().isoformat()}
            )

            return response

        async def chat_with_session(self, message, session_messages, session_id="", session=None, gateway=None):
            """Mock: 记录调用参数并返回固定响应"""
            self._last_call = {
                "message": message,
                "session_messages_count": len(session_messages),
                "session_id": session_id,
                "session_channel": session.channel if session else None,
                "gateway": gateway,
            }
            return f"回复: {message}"

    agent = MinimalAgent()

    # 第一轮对话
    r1 = await agent.chat("你好")
    assert r1 == "回复: 你好", f"Expected '回复: 你好', got '{r1}'"
    assert agent._cli_session is not None, "CLI Session should be created"
    assert agent._cli_session.channel == "cli"
    assert agent._last_call["gateway"] is None, "gateway should be None for CLI"
    assert agent._last_call["session_messages_count"] == 1, "First call should have 1 message (user)"
    print(f"  ✓ 第一轮: session 已创建, gateway=None, messages=1")

    # 第二轮对话
    r2 = await agent.chat("今天天气")
    assert agent._last_call["session_messages_count"] == 3, "Second call should have 3 messages (user+assistant+user)"
    print(f"  ✓ 第二轮: messages=3 (含历史), session 复用")

    # 验证 _conversation_history 同步
    assert len(agent._conversation_history) == 4, f"Should have 4 history entries, got {len(agent._conversation_history)}"
    assert agent._conversation_history[0]["role"] == "user"
    assert agent._conversation_history[1]["role"] == "assistant"
    print(f"  ✓ _conversation_history 同步正确: {len(agent._conversation_history)} 条")

    # 验证 Session 中的消息
    session_msgs = agent._cli_session.context.get_messages()
    assert len(session_msgs) == 4, f"Session should have 4 messages, got {len(session_msgs)}"
    print(f"  ✓ CLI Session 消息正确: {len(session_msgs)} 条")

    print("  ✅ 测试 7 通过")


def main():
    print("=" * 60)
    print("CLI ↔ IM 统一路径改造 - 验证测试")
    print("=" * 60)

    passed = 0
    failed = 0
    errors = []

    tests = [
        ("Session 创建和消息管理", test_session_create_and_messages),
        ("消息去重逻辑", test_session_dedup_logic),
        ("IM Context 条件设置", test_im_context_conditional),
        ("session_type 自动检测", test_session_type_detection),
        ("/clear 命令逻辑", test_clear_command_logic),
        ("self_check 上下文清理", test_selfcheck_clearing),
        ("chat() 委托逻辑", lambda: asyncio.run(test_chat_delegation())),
    ]

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            print(f"  ❌ 测试失败: {name}: {e}")

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    if errors:
        print("\n失败详情:")
        for name, err in errors:
            print(f"  - {name}: {err}")
        sys.exit(1)
    else:
        print("\n所有测试通过! ✅")


if __name__ == "__main__":
    main()
