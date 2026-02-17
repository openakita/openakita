"""
单元测试 - 协作式取消机制 (Cooperative Cancellation)

测试内容:
1. UserCancelledError 异常类
2. TaskState.cancel_event 信号
3. AgentState.cancel_task() 联动
4. _cancellable_llm_call 竞速取消
5. _execute_tool_calls_batch 竞速取消
6. _handle_cancel_farewell 收尾逻辑
7. _persist_cancel_to_context 持久化
8. reasoning_engine._reason_with_heartbeat cancel_event 竞速
9. reasoning_engine._stream_cancel_farewell 流式收尾
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ─────────────────────────────────────────────
# 1. UserCancelledError 异常类
# ─────────────────────────────────────────────


class TestUserCancelledError:
    """UserCancelledError 异常类测试"""

    def test_basic_creation(self):
        """基本创建"""
        from openakita.core.errors import UserCancelledError

        err = UserCancelledError(reason="停止", source="llm_call")
        assert err.reason == "停止"
        assert err.source == "llm_call"
        assert "User cancelled" in str(err)
        assert "llm_call" in str(err)

    def test_default_values(self):
        """默认值"""
        from openakita.core.errors import UserCancelledError

        err = UserCancelledError()
        assert err.reason == ""
        assert err.source == ""

    def test_is_exception(self):
        """是 Exception 子类"""
        from openakita.core.errors import UserCancelledError

        assert issubclass(UserCancelledError, Exception)


# ─────────────────────────────────────────────
# 2. TaskState.cancel_event 信号
# ─────────────────────────────────────────────


class TestTaskStateCancelEvent:
    """TaskState cancel_event 测试"""

    def test_cancel_event_exists(self):
        """TaskState 包含 cancel_event 字段"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="test-1")
        assert hasattr(ts, "cancel_event")
        assert isinstance(ts.cancel_event, asyncio.Event)

    def test_cancel_event_initially_not_set(self):
        """cancel_event 初始未触发"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="test-2")
        assert not ts.cancel_event.is_set()

    def test_cancel_sets_event(self):
        """cancel() 触发 cancel_event"""
        from openakita.core.agent_state import TaskState, TaskStatus

        ts = TaskState(task_id="test-3", status=TaskStatus.REASONING)
        ts.cancel("测试取消")
        assert ts.cancel_event.is_set()
        assert ts.cancelled is True
        assert ts.cancel_reason == "测试取消"

    def test_cancel_from_acting(self):
        """从 ACTING 状态取消"""
        from openakita.core.agent_state import TaskState, TaskStatus

        ts = TaskState(task_id="test-4", status=TaskStatus.ACTING)
        ts.cancel("工具执行中取消")
        assert ts.cancel_event.is_set()
        assert ts.status == TaskStatus.CANCELLED

    def test_each_task_gets_own_event(self):
        """每个 TaskState 有独立的 cancel_event"""
        from openakita.core.agent_state import TaskState

        ts1 = TaskState(task_id="a")
        ts2 = TaskState(task_id="b")
        ts1.cancel("取消 a")
        assert ts1.cancel_event.is_set()
        assert not ts2.cancel_event.is_set()


# ─────────────────────────────────────────────
# 3. AgentState.cancel_task() 联动
# ─────────────────────────────────────────────


class TestAgentStateCancelTask:
    """AgentState cancel_task 与 TaskState cancel_event 联动"""

    def test_cancel_task_sets_event(self):
        """AgentState.cancel_task() 设置 TaskState.cancel_event"""
        from openakita.core.agent_state import AgentState

        agent_state = AgentState()
        task = agent_state.begin_task(session_id="s1")
        assert not task.cancel_event.is_set()

        agent_state.cancel_task("外部取消")
        assert task.cancel_event.is_set()
        assert task.cancelled is True
        assert task.cancel_reason == "外部取消"

    def test_cancel_task_no_current_task(self):
        """没有活跃任务时 cancel_task 不报错"""
        from openakita.core.agent_state import AgentState

        agent_state = AgentState()
        agent_state.cancel_task("无任务")  # 不应抛出异常


# ─────────────────────────────────────────────
# 4. _cancellable_llm_call 竞速取消
# ─────────────────────────────────────────────


class TestCancellableLlmCall:
    """_cancellable_llm_call 测试"""

    @pytest.mark.asyncio
    async def test_normal_completion(self):
        """LLM 正常返回时 _cancellable_llm_call 返回结果"""
        from openakita.core.errors import UserCancelledError

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="回复内容")]

        mock_brain = MagicMock()
        mock_brain.messages_create = MagicMock(return_value=mock_response)

        agent = MagicMock()
        agent.brain = mock_brain
        agent._cancel_reason = ""

        cancel_event = asyncio.Event()

        # 直接测试辅助逻辑
        from openakita.core.agent import Agent

        result = await Agent._cancellable_llm_call(
            agent, cancel_event, model="test", max_tokens=100, system="", tools=[], messages=[]
        )
        assert result == mock_response

    @pytest.mark.asyncio
    async def test_cancel_interrupts(self):
        """cancel_event 触发时 _cancellable_llm_call 抛出 UserCancelledError"""
        from openakita.core.errors import UserCancelledError

        # 模拟一个耗时很长的 LLM 调用
        def slow_llm(**kwargs):
            import time
            time.sleep(10)  # 模拟长时间调用
            return MagicMock()

        mock_brain = MagicMock()
        mock_brain.messages_create = slow_llm

        agent = MagicMock()
        agent.brain = mock_brain
        agent._cancel_reason = "用户停止"

        cancel_event = asyncio.Event()

        from openakita.core.agent import Agent

        async def trigger_cancel():
            await asyncio.sleep(0.1)
            cancel_event.set()

        cancel_task = asyncio.create_task(trigger_cancel())

        with pytest.raises(UserCancelledError) as exc_info:
            await Agent._cancellable_llm_call(
                agent, cancel_event, model="test", max_tokens=100, system="", tools=[], messages=[]
            )

        assert exc_info.value.source == "llm_call"
        assert "用户停止" in exc_info.value.reason
        await cancel_task


# ─────────────────────────────────────────────
# 5. _handle_cancel_farewell 收尾逻辑
# ─────────────────────────────────────────────


class TestHandleCancelFarewell:
    """_handle_cancel_farewell 测试"""

    @pytest.mark.asyncio
    async def test_farewell_calls_llm(self):
        """收尾时调用 LLM 生成自然文本"""
        from openakita.core.agent import Agent

        farewell_response = MagicMock()
        farewell_block = MagicMock()
        farewell_block.type = "text"
        farewell_block.text = "好的，我已经停止了当前任务。"
        farewell_response.content = [farewell_block]

        mock_brain = MagicMock()
        mock_brain.messages_create = MagicMock(return_value=farewell_response)

        agent = MagicMock(spec=Agent)
        agent.brain = mock_brain
        agent._cancel_reason = "停止"
        agent._context = MagicMock()
        agent._context.messages = []
        agent._handle_cancel_farewell = Agent._handle_cancel_farewell.__get__(agent, Agent)
        agent._persist_cancel_to_context = Agent._persist_cancel_to_context.__get__(agent, Agent)

        result = await agent._handle_cancel_farewell([], "system_prompt", "gpt-4")
        assert "停止" in result
        assert mock_brain.messages_create.called

    @pytest.mark.asyncio
    async def test_farewell_timeout_fallback(self):
        """LLM 超时时返回默认文本"""
        from openakita.core.agent import Agent

        def slow_llm(**kwargs):
            import time
            time.sleep(20)  # 会被 wait_for 超时

        mock_brain = MagicMock()
        mock_brain.messages_create = slow_llm

        agent = MagicMock(spec=Agent)
        agent.brain = mock_brain
        agent._cancel_reason = "停止"
        agent._context = MagicMock()
        agent._context.messages = []
        agent._handle_cancel_farewell = Agent._handle_cancel_farewell.__get__(agent, Agent)
        agent._persist_cancel_to_context = Agent._persist_cancel_to_context.__get__(agent, Agent)

        # 减小超时使测试快速
        with patch("openakita.core.agent.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = await agent._handle_cancel_farewell([], "system_prompt", "gpt-4")

        assert result == "✅ 任务已停止。"


# ─────────────────────────────────────────────
# 6. _persist_cancel_to_context 持久化
# ─────────────────────────────────────────────


class TestPersistCancelToContext:
    """_persist_cancel_to_context 持久化测试"""

    def test_persists_to_context_messages(self):
        """中断事件正确记录到 context.messages"""
        from openakita.core.agent import Agent

        agent = MagicMock(spec=Agent)
        agent._context = MagicMock()
        agent._context.messages = []
        agent._persist_cancel_to_context = Agent._persist_cancel_to_context.__get__(agent, Agent)

        agent._persist_cancel_to_context("用户说停止", "好的，已停止。")

        assert len(agent._context.messages) == 2
        assert agent._context.messages[0]["role"] == "user"
        assert "用户中断" in agent._context.messages[0]["content"]
        assert agent._context.messages[1]["role"] == "assistant"
        assert "已停止" in agent._context.messages[1]["content"]

    def test_no_context_no_error(self):
        """没有 _context 属性时不报错"""
        from openakita.core.agent import Agent

        agent = MagicMock(spec=Agent)
        agent._context = None
        agent._persist_cancel_to_context = Agent._persist_cancel_to_context.__get__(agent, Agent)

        agent._persist_cancel_to_context("test", "test")


# ─────────────────────────────────────────────
# 7. cancel_current_task 联动 _state
# ─────────────────────────────────────────────


class TestCancelCurrentTaskIntegration:
    """cancel_current_task 同时触发 _state.cancel_task"""

    def test_cancel_triggers_state_event(self):
        """cancel_current_task 设置 _state.current_task.cancel_event"""
        from openakita.core.agent_state import AgentState

        agent_state = AgentState()
        task = agent_state.begin_task()

        # 模拟 Agent 实例
        agent = MagicMock()
        agent._task_cancelled = False
        agent._cancel_reason = ""
        agent._state = agent_state

        from openakita.core.agent import Agent

        Agent.cancel_current_task(agent, reason="IM 停止指令")

        assert agent._task_cancelled is True
        assert task.cancelled is True
        assert task.cancel_event.is_set()


# ─────────────────────────────────────────────
# 8. _reason_with_heartbeat cancel_event 竞速
# ─────────────────────────────────────────────


class TestReasonWithHeartbeatCancel:
    """_reason_with_heartbeat 中 cancel_event 竞速测试"""

    @pytest.mark.asyncio
    async def test_cancel_during_reason(self):
        """LLM 推理期间取消应抛出 UserCancelledError"""
        from openakita.core.errors import UserCancelledError
        from openakita.core.agent_state import AgentState, TaskStatus

        agent_state = AgentState()
        task = agent_state.begin_task()
        task.transition(TaskStatus.REASONING)

        # 创建 ReasoningEngine mock
        engine = MagicMock()
        engine._state = agent_state
        engine._brain = MagicMock()
        engine._HEARTBEAT_INTERVAL = 15
        engine._browser_page_read_tools = frozenset()

        # 模拟一个永远不返回的 _reason 方法
        async def slow_reason(*args, **kwargs):
            await asyncio.sleep(100)

        engine._reason = slow_reason

        from openakita.core.reasoning_engine import ReasoningEngine

        engine._reason_with_heartbeat = ReasoningEngine._reason_with_heartbeat.__get__(
            engine, ReasoningEngine
        )

        # 延迟触发取消
        async def trigger_cancel():
            await asyncio.sleep(0.1)
            task.cancel("测试取消")

        cancel_task = asyncio.create_task(trigger_cancel())

        with pytest.raises(UserCancelledError) as exc_info:
            async for _ in engine._reason_with_heartbeat(
                [],
                system_prompt="",
                tools=[],
                current_model="test",
            ):
                pass

        assert exc_info.value.source == "llm_call_stream"
        await cancel_task


# ─────────────────────────────────────────────
# 9. _stream_cancel_farewell 流式收尾
# ─────────────────────────────────────────────


class TestStreamCancelFarewell:
    """_stream_cancel_farewell 流式收尾测试"""

    @pytest.mark.asyncio
    async def test_farewell_yields_text_deltas(self):
        """流式收尾产出 text_delta 事件"""
        from openakita.core.agent_state import TaskState, TaskStatus
        from openakita.core.reasoning_engine import ReasoningEngine

        farewell_response = MagicMock()
        farewell_block = MagicMock()
        farewell_block.type = "text"
        farewell_block.text = "好的，已停止当前任务。"
        farewell_response.content = [farewell_block]

        mock_brain = MagicMock()
        mock_brain.messages_create = MagicMock(return_value=farewell_response)

        engine = MagicMock()
        engine._brain = mock_brain
        engine._stream_cancel_farewell = ReasoningEngine._stream_cancel_farewell.__get__(
            engine, ReasoningEngine
        )

        state = TaskState(task_id="test", status=TaskStatus.CANCELLED)
        state.cancel("停止")

        events = []
        async for ev in engine._stream_cancel_farewell([], "", "test-model", state):
            events.append(ev)

        assert len(events) > 0
        assert all(e["type"] == "text_delta" for e in events)
        full_text = "".join(e["content"] for e in events)
        assert "停止" in full_text

    @pytest.mark.asyncio
    async def test_farewell_timeout_yields_default(self):
        """LLM 超时时产出默认文本"""
        from openakita.core.agent_state import TaskState, TaskStatus
        from openakita.core.reasoning_engine import ReasoningEngine

        def slow_llm(**kwargs):
            import time
            time.sleep(20)

        mock_brain = MagicMock()
        mock_brain.messages_create = slow_llm

        engine = MagicMock()
        engine._brain = mock_brain
        engine._stream_cancel_farewell = ReasoningEngine._stream_cancel_farewell.__get__(
            engine, ReasoningEngine
        )

        state = TaskState(task_id="test", status=TaskStatus.CANCELLED)
        state.cancel("停止")

        with patch(
            "openakita.core.reasoning_engine.asyncio.wait_for",
            side_effect=asyncio.TimeoutError(),
        ):
            events = []
            async for ev in engine._stream_cancel_farewell([], "", "test-model", state):
                events.append(ev)

        full_text = "".join(e["content"] for e in events)
        assert "✅ 任务已停止" in full_text


# ─────────────────────────────────────────────
# 10. asyncio.Event 竞速正确性
# ─────────────────────────────────────────────


class TestAsyncioEventRace:
    """验证 asyncio.Event + asyncio.wait 竞速模式的正确性"""

    @pytest.mark.asyncio
    async def test_event_wins_race(self):
        """cancel_event 比 work task 先完成时，work task 被取消"""
        cancel_event = asyncio.Event()

        async def slow_work():
            await asyncio.sleep(10)
            return "done"

        work_task = asyncio.create_task(slow_work())
        cancel_waiter = asyncio.create_task(cancel_event.wait())

        # 0.05s 后触发 cancel
        async def trigger():
            await asyncio.sleep(0.05)
            cancel_event.set()

        asyncio.create_task(trigger())

        done, pending = await asyncio.wait(
            {work_task, cancel_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert cancel_waiter in done
        assert work_task not in done

    @pytest.mark.asyncio
    async def test_work_wins_race(self):
        """work task 比 cancel_event 先完成时，正常返回结果"""
        cancel_event = asyncio.Event()

        async def fast_work():
            await asyncio.sleep(0.01)
            return "done"

        work_task = asyncio.create_task(fast_work())
        cancel_waiter = asyncio.create_task(cancel_event.wait())

        done, pending = await asyncio.wait(
            {work_task, cancel_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert work_task in done
        assert work_task.result() == "done"


# ─────────────────────────────────────────────
# 11. TaskState skip_event 机制
# ─────────────────────────────────────────────


class TestTaskStateSkipEvent:
    """TaskState skip_event 测试"""

    def test_skip_event_exists(self):
        """TaskState 包含 skip_event 字段"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="skip-1")
        assert hasattr(ts, "skip_event")
        assert isinstance(ts.skip_event, asyncio.Event)

    def test_skip_event_initially_not_set(self):
        """skip_event 初始未触发"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="skip-2")
        assert not ts.skip_event.is_set()

    def test_request_skip_sets_event(self):
        """request_skip() 触发 skip_event"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="skip-3")
        ts.request_skip("太慢了")
        assert ts.skip_event.is_set()
        assert ts.skip_reason == "太慢了"

    def test_clear_skip_resets_event(self):
        """clear_skip() 重置 skip_event 和 skip_reason"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="skip-4")
        ts.request_skip("太慢了")
        ts.clear_skip()
        assert not ts.skip_event.is_set()
        assert ts.skip_reason == ""

    def test_skip_does_not_cancel(self):
        """skip 不会触发 cancel"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="skip-5")
        ts.request_skip("换个方法")
        assert not ts.cancelled
        assert not ts.cancel_event.is_set()


# ─────────────────────────────────────────────
# 12. TaskState pending_user_inserts 机制
# ─────────────────────────────────────────────


class TestTaskStateUserInserts:
    """TaskState pending_user_inserts 测试"""

    @pytest.mark.asyncio
    async def test_add_and_drain(self):
        """add_user_insert -> drain_user_inserts 正常工作"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="ins-1")
        await ts.add_user_insert("消息1")
        await ts.add_user_insert("消息2")
        assert len(ts.pending_user_inserts) == 2

        drained = await ts.drain_user_inserts()
        assert drained == ["消息1", "消息2"]
        assert len(ts.pending_user_inserts) == 0

    @pytest.mark.asyncio
    async def test_drain_empty(self):
        """drain_user_inserts 空队列返回空列表"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="ins-2")
        drained = await ts.drain_user_inserts()
        assert drained == []

    @pytest.mark.asyncio
    async def test_multiple_drain_idempotent(self):
        """多次 drain 只返回一次"""
        from openakita.core.agent_state import TaskState

        ts = TaskState(task_id="ins-3")
        await ts.add_user_insert("hello")
        d1 = await ts.drain_user_inserts()
        d2 = await ts.drain_user_inserts()
        assert d1 == ["hello"]
        assert d2 == []


# ─────────────────────────────────────────────
# 13. 三路竞速 (skip_event) asyncio 行为测试
# ─────────────────────────────────────────────


class TestThreeWayRace:
    """skip_event 三路竞速 asyncio 行为测试"""

    @pytest.mark.asyncio
    async def test_skip_wins_race(self):
        """skip_event 先触发时，工具任务被跳过但不终止整体"""
        cancel_event = asyncio.Event()
        skip_event = asyncio.Event()

        async def slow_work():
            await asyncio.sleep(10)
            return "completed"

        tool_task = asyncio.create_task(slow_work())
        cancel_waiter = asyncio.create_task(cancel_event.wait())
        skip_waiter = asyncio.create_task(skip_event.wait())

        async def trigger_skip():
            await asyncio.sleep(0.05)
            skip_event.set()

        asyncio.create_task(trigger_skip())

        done, pending = await asyncio.wait(
            {tool_task, cancel_waiter, skip_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert skip_waiter in done
        assert tool_task not in done
        assert cancel_waiter not in done

    @pytest.mark.asyncio
    async def test_cancel_wins_over_skip(self):
        """cancel_event 先触发时优先于 skip"""
        cancel_event = asyncio.Event()
        skip_event = asyncio.Event()

        async def slow_work():
            await asyncio.sleep(10)
            return "completed"

        tool_task = asyncio.create_task(slow_work())
        cancel_waiter = asyncio.create_task(cancel_event.wait())
        skip_waiter = asyncio.create_task(skip_event.wait())

        async def trigger_cancel():
            await asyncio.sleep(0.05)
            cancel_event.set()

        asyncio.create_task(trigger_cancel())

        done, pending = await asyncio.wait(
            {tool_task, cancel_waiter, skip_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert cancel_waiter in done
        assert tool_task not in done

    @pytest.mark.asyncio
    async def test_work_wins_three_way(self):
        """工具任务先完成时，正常返回结果"""
        cancel_event = asyncio.Event()
        skip_event = asyncio.Event()

        async def fast_work():
            await asyncio.sleep(0.01)
            return "result"

        tool_task = asyncio.create_task(fast_work())
        cancel_waiter = asyncio.create_task(cancel_event.wait())
        skip_waiter = asyncio.create_task(skip_event.wait())

        done, pending = await asyncio.wait(
            {tool_task, cancel_waiter, skip_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        assert tool_task in done
        assert tool_task.result() == "result"


# ─────────────────────────────────────────────
# 14. Agent.classify_interrupt 分类测试
# ─────────────────────────────────────────────


class TestClassifyInterrupt:
    """Agent.classify_interrupt 分类测试"""

    def _make_agent(self):
        """创建最小化 Agent 实例（仅需 classify_interrupt 方法可用）"""
        from openakita.core.agent import Agent

        agent = object.__new__(Agent)
        agent._task_cancelled = False
        agent._cancel_reason = ""
        agent._interrupt_enabled = True
        return agent

    def test_stop_commands(self):
        """STOP_COMMANDS 被分类为 stop"""
        agent = self._make_agent()
        for cmd in ["停止", "stop", "取消", "cancel", "abort", "停下", "算了", "不用了"]:
            assert agent.classify_interrupt(cmd) == "stop", f"Expected 'stop' for '{cmd}'"

    def test_skip_commands(self):
        """SKIP_COMMANDS 被分类为 skip"""
        agent = self._make_agent()
        for cmd in ["跳过", "skip", "下一步", "next", "太慢了"]:
            assert agent.classify_interrupt(cmd) == "skip", f"Expected 'skip' for '{cmd}'"

    def test_insert_for_unknown(self):
        """非指令消息被分类为 insert"""
        agent = self._make_agent()
        assert agent.classify_interrupt("请帮我查一下天气") == "insert"
        assert agent.classify_interrupt("另外还有一个问题") == "insert"
        assert agent.classify_interrupt("补充一下刚才的需求") == "insert"

    def test_case_insensitive_stop(self):
        """停止指令大小写不敏感"""
        agent = self._make_agent()
        assert agent.classify_interrupt("STOP") == "stop"
        assert agent.classify_interrupt("Stop") == "stop"
        assert agent.classify_interrupt("CANCEL") == "stop"

    def test_case_insensitive_skip(self):
        """跳过指令大小写不敏感"""
        agent = self._make_agent()
        assert agent.classify_interrupt("SKIP") == "skip"
        assert agent.classify_interrupt("Skip") == "skip"
        assert agent.classify_interrupt("NEXT") == "skip"


# ─────────────────────────────────────────────
# 15. Gateway 中断路由测试
# ─────────────────────────────────────────────


class TestGatewayInterruptRouting:
    """Gateway 中断消息路由正确性（不入错误队列）"""

    def test_skip_command_not_in_stop_commands(self):
        """SKIP_COMMANDS 和 STOP_COMMANDS 不交叉"""
        from openakita.core.agent import Agent

        common = Agent.STOP_COMMANDS & Agent.SKIP_COMMANDS
        assert len(common) == 0, f"STOP_COMMANDS and SKIP_COMMANDS overlap: {common}"

    def test_agent_has_all_interrupt_methods(self):
        """Agent 具备所有中断控制方法"""
        from openakita.core.agent import Agent

        assert hasattr(Agent, "cancel_current_task")
        assert hasattr(Agent, "skip_current_step")
        assert hasattr(Agent, "insert_user_message")
        assert hasattr(Agent, "classify_interrupt")
        assert hasattr(Agent, "is_stop_command")
        assert hasattr(Agent, "is_skip_command")
