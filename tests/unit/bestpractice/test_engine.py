"""BPEngine core execution tests."""

import asyncio
import json

import pytest

from seeagent.bestpractice.config import BestPracticeConfig, load_bp_config
from seeagent.bestpractice.engine import BPEngine
from seeagent.bestpractice.models import BPStatus, RunMode, SubtaskConfig, SubtaskStatus
from seeagent.bestpractice.schema_chain import SchemaChain
from seeagent.bestpractice.state_manager import BPStateManager


class MockOrchestrator:
    def __init__(self, responses: dict[str, str] | None = None):
        self.calls: list[dict] = []
        self.responses = responses or {}

    async def delegate(self, *, session, from_agent, to_agent, message, reason=""):
        self.calls.append({
            "from_agent": from_agent, "to_agent": to_agent,
            "message": message, "reason": reason,
        })
        return self.responses.get(to_agent, '{"result": "ok"}')


class MockSession:
    def __init__(self, session_id="test-session"):
        self.id = session_id
        self.metadata = {}

        class MockContext:
            _sse_event_bus = None
        self.context = MockContext()


@pytest.fixture
def bp_config():
    return BestPracticeConfig(
        id="test-bp", name="测试BP", description="test",
        subtasks=[
            SubtaskConfig(
                id="s1", name="调研", agent_profile="researcher",
                input_schema={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "调研主题"},
                    },
                    "required": ["topic"],
                },
            ),
            SubtaskConfig(
                id="s2", name="分析", agent_profile="analyst",
                input_schema={
                    "type": "object",
                    "properties": {
                        "findings": {"type": "array", "description": "调研发现"},
                    },
                    "required": ["findings"],
                },
            ),
            SubtaskConfig(id="s3", name="报告", agent_profile="writer"),
        ],
    )


@pytest.fixture
def engine():
    return BPEngine(state_manager=BPStateManager(), schema_chain=SchemaChain())


# ── Execute subtask ────────────────────────────────────────────


class TestExecuteSubtask:
    @pytest.mark.asyncio
    async def test_executes_single_subtask(self, engine, bp_config):
        orch = MockOrchestrator(responses={
            "researcher": json.dumps({"findings": ["data1"]}),
        })
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "AI"})

        result = await engine.execute_subtask(inst_id, bp_config, orch, session)

        assert len(orch.calls) == 1
        assert orch.calls[0]["to_agent"] == "researcher"
        snap = engine.state_manager.get(inst_id)
        assert snap.subtask_statuses["s1"] == SubtaskStatus.DONE.value
        assert snap.current_subtask_index == 1

    @pytest.mark.asyncio
    async def test_auto_mode_returns_continue_instruction(self, engine, bp_config):
        orch = MockOrchestrator(responses={
            "researcher": json.dumps({"findings": ["data1"]}),
        })
        session = MockSession()
        inst_id = engine.state_manager.create_instance(
            bp_config, session.id, {"topic": "AI"}, run_mode=RunMode.AUTO,
        )

        result = await engine.execute_subtask(inst_id, bp_config, orch, session)
        assert "bp_continue" in result
        assert inst_id in result

    @pytest.mark.asyncio
    async def test_manual_mode_returns_user_choice(self, engine, bp_config):
        orch = MockOrchestrator(responses={
            "researcher": json.dumps({"result": "done"}),
        })
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "AI"})

        result = await engine.execute_subtask(inst_id, bp_config, orch, session)
        assert "ask_user" in result or "下一步" in result

    @pytest.mark.asyncio
    async def test_last_subtask_completes_instance(self, engine, bp_config):
        orch = MockOrchestrator(responses={"writer": '{"report": "final"}'})
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "x"})
        snap = engine.state_manager.get(inst_id)
        snap.current_subtask_index = 2
        snap.subtask_statuses["s1"] = SubtaskStatus.DONE.value
        snap.subtask_statuses["s2"] = SubtaskStatus.DONE.value

        result = await engine.execute_subtask(inst_id, bp_config, orch, session)
        snap = engine.state_manager.get(inst_id)
        assert snap.status == BPStatus.COMPLETED
        assert "完成" in result

    @pytest.mark.asyncio
    async def test_delegation_failure_resets_status(self, engine, bp_config):
        class FailOrchestrator:
            async def delegate(self, **kw):
                raise RuntimeError("delegation error")

        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "x"})

        result = await engine.execute_subtask(inst_id, bp_config, FailOrchestrator(), session)
        snap = engine.state_manager.get(inst_id)
        assert snap.subtask_statuses["s1"] == SubtaskStatus.PENDING.value
        assert "失败" in result

    @pytest.mark.asyncio
    async def test_nonexistent_instance_returns_error(self, engine, bp_config):
        result = await engine.execute_subtask("nonexist", bp_config, MockOrchestrator(), MockSession())
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_all_done_returns_error(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "x"})
        snap = engine.state_manager.get(inst_id)
        snap.current_subtask_index = 3  # beyond all subtasks

        result = await engine.execute_subtask(inst_id, bp_config, MockOrchestrator(), session)
        assert "已完成" in result


# ── Input completeness check ──────────────────────────────────


class TestInputCompleteness:
    @pytest.mark.asyncio
    async def test_missing_required_field_pauses(self, engine, bp_config):
        """auto 模式：缺少 required 字段时暂停"""
        session = MockSession()
        # 不提供 topic (required)
        inst_id = engine.state_manager.create_instance(
            bp_config, session.id, {}, run_mode=RunMode.AUTO,
        )

        result = await engine.execute_subtask(inst_id, bp_config, MockOrchestrator(), session)
        assert "不完整" in result or "缺少" in result
        assert "topic" in result
        assert "bp_supplement_input" in result
        assert "自动模式已暂停" in result

    @pytest.mark.asyncio
    async def test_manual_mode_missing_field(self, engine, bp_config):
        """手动模式：缺少字段时也提示"""
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {})

        result = await engine.execute_subtask(inst_id, bp_config, MockOrchestrator(), session)
        assert "topic" in result
        assert "ask_user" in result

    @pytest.mark.asyncio
    async def test_no_required_fields_continues(self, engine):
        """无 required 字段的 subtask 正常执行"""
        config = BestPracticeConfig(
            id="x", name="X",
            subtasks=[SubtaskConfig(
                id="s1", name="S1", agent_profile="a",
                input_schema={"type": "object", "properties": {"optional": {"type": "string"}}},
            )],
        )
        session = MockSession()
        inst_id = engine.state_manager.create_instance(config, session.id, {})
        orch = MockOrchestrator(responses={"a": '{"done": true}'})

        result = await engine.execute_subtask(inst_id, config, orch, session)
        assert "不完整" not in result


# ── Resolve input ──────────────────────────────────────────────


class TestResolveInput:
    def test_first_subtask_uses_initial_input(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "AI"})
        snap = engine.state_manager.get(inst_id)
        input_data = engine._resolve_input(snap, bp_config, 0)
        assert input_data == {"topic": "AI"}

    def test_subsequent_subtask_uses_prev_output(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {})
        engine.state_manager.update_subtask_output(inst_id, "s1", {"findings": ["data"]})
        snap = engine.state_manager.get(inst_id)
        snap.current_subtask_index = 1
        input_data = engine._resolve_input(snap, bp_config, 1)
        assert input_data == {"findings": ["data"]}


# ── Chat-to-Edit ──────────────────────────────────────────────


class TestChatToEdit:
    def test_edit_output_success(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "AI"})
        engine.state_manager.update_subtask_output(inst_id, "s1", {"a": 1, "b": 2})
        engine.state_manager.update_subtask_status(inst_id, "s1", SubtaskStatus.DONE)
        engine.state_manager.update_subtask_status(inst_id, "s2", SubtaskStatus.DONE)
        engine.state_manager.update_subtask_output(inst_id, "s2", {"x": 10})

        result = engine.handle_edit_output(inst_id, "s1", {"b": 99, "c": 3}, bp_config)
        assert result["success"]
        assert result["merged"]["b"] == 99
        assert result["merged"]["c"] == 3
        assert "s2" in result["stale_subtasks"]

    def test_edit_nonexistent_output(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {})
        result = engine.handle_edit_output(inst_id, "s1", {"x": 1}, bp_config)
        assert not result["success"]

    def test_edit_nonexistent_instance(self, engine, bp_config):
        result = engine.handle_edit_output("ghost", "s1", {}, bp_config)
        assert not result["success"]


# ── Supplement input ──────────────────────────────────────────


class TestSupplementInput:
    def test_supplement_first_subtask(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"partial": "data"})

        result = engine.supplement_input(inst_id, "s1", {"topic": "AI agents"})
        assert result["success"]
        snap = engine.state_manager.get(inst_id)
        assert snap.initial_input["topic"] == "AI agents"
        assert snap.initial_input["partial"] == "data"

    def test_supplement_subsequent_subtask(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "AI"})
        engine.state_manager.update_subtask_output(inst_id, "s1", {"some": "data"})

        result = engine.supplement_input(inst_id, "s2", {"findings": ["new finding"]})
        assert result["success"]
        snap = engine.state_manager.get(inst_id)
        assert snap.subtask_outputs["s1"]["findings"] == ["new finding"]

    def test_supplement_nonexistent_instance(self, engine):
        result = engine.supplement_input("ghost", "s1", {})
        assert not result["success"]


# ── Stale reset ───────────────────────────────────────────────


class TestResetStale:
    def test_resets_stale_subtasks(self, engine, bp_config):
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {})
        engine.state_manager.update_subtask_status(inst_id, "s2", SubtaskStatus.STALE)
        engine.state_manager.update_subtask_status(inst_id, "s3", SubtaskStatus.STALE)
        snap = engine.state_manager.get(inst_id)
        snap.current_subtask_index = 1

        reset = engine.reset_stale_if_needed(inst_id, bp_config)
        assert "s2" in reset
        assert "s3" in reset
        snap = engine.state_manager.get(inst_id)
        assert snap.subtask_statuses["s2"] == SubtaskStatus.PENDING.value


# ── Parse output ──────────────────────────────────────────────


class TestParseOutput:
    def test_parse_json(self):
        assert BPEngine._parse_output('{"a": 1}') == {"a": 1}

    def test_parse_json_block(self):
        text = "Some text\n```json\n{\"x\": 2}\n```\nMore text"
        assert BPEngine._parse_output(text) == {"x": 2}

    def test_fallback_raw(self):
        result = BPEngine._parse_output("plain text response")
        assert result["_raw_output"] == "plain text response"


# ── Persistence ───────────────────────────────────────────────


class TestPersistence:
    @pytest.mark.asyncio
    async def test_persists_after_subtask(self, engine, bp_config):
        orch = MockOrchestrator(responses={
            "researcher": json.dumps({"findings": ["x"]}),
        })
        session = MockSession()
        inst_id = engine.state_manager.create_instance(bp_config, session.id, {"topic": "AI"})

        await engine.execute_subtask(inst_id, bp_config, orch, session)

        # Session.metadata should have bp_state
        assert "bp_state" in session.metadata
        assert len(session.metadata["bp_state"]["instances"]) == 1
