"""L1 Unit Tests: Session state machine, message management."""

from datetime import datetime, timedelta

import pytest

from openakita.sessions.session import (
    Session,
    SessionConfig,
    SessionContext,
    SessionState,
    TaskCheckpoint,
)


class TestSessionCreation:
    def test_default_state_is_active(self):
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        assert s.state == SessionState.ACTIVE

    def test_context_starts_empty(self):
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        assert s.context.messages == []
        assert s.context.current_task is None

    def test_custom_channel(self):
        s = Session(id="s1", channel="telegram", chat_id="tg-123", user_id="u1")
        assert s.channel == "telegram"
        assert s.chat_id == "tg-123"


class TestSessionState:
    def test_all_states_exist(self):
        assert SessionState.ACTIVE.value == "active"
        assert SessionState.IDLE.value == "idle"
        assert SessionState.EXPIRED.value == "expired"
        assert SessionState.CLOSED.value == "closed"

    def test_state_is_settable(self):
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        s.state = SessionState.IDLE
        assert s.state == SessionState.IDLE
        s.state = SessionState.EXPIRED
        assert s.state == SessionState.EXPIRED


class TestSessionContext:
    def test_add_messages(self):
        ctx = SessionContext()
        ctx.messages.append({"role": "user", "content": "hello"})
        ctx.messages.append({"role": "assistant", "content": "hi"})
        assert len(ctx.messages) == 2

    def test_variables_dict(self):
        ctx = SessionContext()
        ctx.variables["key"] = "value"
        assert ctx.variables["key"] == "value"

    def test_task_lifecycle(self):
        ctx = SessionContext()
        assert ctx.current_task is None
        ctx.current_task = "Write a poem"
        assert ctx.current_task == "Write a poem"
        ctx.current_task = None
        assert ctx.current_task is None

    def test_summary_field(self):
        ctx = SessionContext()
        assert ctx.summary is None
        ctx.summary = "User asked about Python"
        assert ctx.summary == "User asked about Python"


class TestSessionConfig:
    def test_default_config(self):
        config = SessionConfig()
        assert isinstance(config, SessionConfig)


class TestMetadataTrimming:
    """_trim_old_metadata: 日常体积控制，裁剪重型元数据但不删消息 (#309)."""

    def _make_session(self, max_history: int = 2000) -> Session:
        return Session(
            id="s1", channel="desktop", chat_id="c1", user_id="u1",
            config=SessionConfig(max_history=max_history),
        )

    def test_default_max_history_is_2000(self):
        config = SessionConfig()
        assert config.max_history == 2000

    def test_from_dict_no_config_defaults_to_2000(self):
        s = Session.from_dict({
            "id": "s1", "channel": "desktop", "chat_id": "c1", "user_id": "u1",
            "state": "active", "created_at": "2026-01-01T00:00:00",
            "last_active": "2026-01-01T00:00:00",
        })
        assert s.config.max_history == 2000

    def test_from_dict_upgrades_old_small_values(self):
        """旧 session 序列化值 100/500 应被迁移至 >= 500."""
        s = Session.from_dict({
            "id": "s1", "channel": "desktop", "chat_id": "c1", "user_id": "u1",
            "state": "active", "created_at": "2026-01-01T00:00:00",
            "last_active": "2026-01-01T00:00:00",
            "config": {"max_history": 100},
        })
        assert s.config.max_history >= 500

    def test_messages_never_deleted_below_hard_cap(self):
        """低于 hard cap 时，add_message 只 trim 元数据，不删除消息。"""
        s = self._make_session(max_history=2000)
        for i in range(200):
            s.add_message(
                "user" if i % 2 == 0 else "assistant",
                f"msg-{i}",
                chain_summary=f"chain-{i}",
                tool_summary=f"tool-{i}",
            )
        assert len(s.context.messages) == 200

    def test_trim_strips_heavy_metadata_from_old_messages(self):
        """超过 preserve window 的旧消息应丢失 chain_summary 等重型字段。"""
        s = self._make_session()
        for i in range(80):
            s.add_message(
                "user" if i % 2 == 0 else "assistant",
                f"msg-{i}",
                chain_summary=f"chain-{i}",
                tool_summary=f"tool-{i}",
                artifacts=[f"artifact-{i}"],
            )
        window = Session._METADATA_PRESERVE_WINDOW
        old_msg = s.context.messages[0]
        assert "chain_summary" not in old_msg
        assert "tool_summary" not in old_msg
        assert "artifacts" not in old_msg
        recent_msg = s.context.messages[-1]
        assert recent_msg.get("chain_summary") == f"chain-{80 - 1}"

    def test_trim_preserves_base_content(self):
        """trim 后旧消息的 role/content/timestamp 必须完好。"""
        s = self._make_session()
        for i in range(80):
            s.add_message(
                "user" if i % 2 == 0 else "assistant",
                f"msg-{i}",
                chain_summary="big data",
            )
        for i, msg in enumerate(s.context.messages):
            assert "content" in msg
            assert "role" in msg

    def test_recent_messages_keep_full_metadata(self):
        """最近 _METADATA_PRESERVE_WINDOW 条消息保留全部元数据。"""
        s = self._make_session()
        window = Session._METADATA_PRESERVE_WINDOW
        for i in range(window + 20):
            s.add_message(
                "user" if i % 2 == 0 else "assistant",
                f"msg-{i}",
                chain_summary=f"chain-{i}",
            )
        for msg in s.context.messages[-window:]:
            assert "chain_summary" in msg


class TestHardCapTruncation:
    """_truncate_history: 仅 hard cap (2000) 极端兜底 (#309)."""

    def _make_session(self, max_history: int = 100) -> Session:
        return Session(
            id="s1", channel="desktop", chat_id="c1", user_id="u1",
            config=SessionConfig(max_history=max_history),
        )

    def test_hard_cap_triggers_above_max_history(self):
        s = self._make_session(max_history=100)
        for i in range(101):
            s.add_message("user" if i % 2 == 0 else "assistant", f"msg-{i}")
        assert len(s.context.messages) <= 100

    def test_hard_cap_keeps_95_percent(self):
        s = self._make_session(max_history=200)
        for i in range(201):
            s.add_message("user" if i % 2 == 0 else "assistant", f"msg-{i}")
        # 95% of 200 = 190, plus possible system summary = 191
        assert len(s.context.messages) >= 190
        assert len(s.context.messages) <= 192

    def test_hard_cap_preserves_recent_messages(self):
        s = self._make_session(max_history=100)
        for i in range(120):
            s.add_message("user" if i % 2 == 0 else "assistant", f"msg-{i}")
        contents = [m.get("content", "") for m in s.context.messages]
        assert "msg-119" in contents

    def test_hard_cap_produces_summary_on_truncation(self):
        s = self._make_session(max_history=100)
        for i in range(101):
            s.add_message("user" if i % 2 == 0 else "assistant", f"msg-{i}")
        system_msgs = [m for m in s.context.messages if m.get("role") == "system"]
        assert len(system_msgs) >= 1
        assert "[历史背景" in system_msgs[0].get("content", "")


class TestSessionTimestamps:
    def test_created_at_set(self):
        before = datetime.now()
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        after = datetime.now()
        assert before <= s.created_at <= after

    def test_last_active_set(self):
        s = Session(id="s1", channel="cli", chat_id="c1", user_id="u1")
        assert s.last_active is not None


class TestTaskCheckpoint:
    """task_checkpoints — 借鉴 claude-code 的任务连续性。"""

    def _ckpt(self, **overrides) -> TaskCheckpoint:
        base = {
            "checkpoint_id": "ckpt-001",
            "task_id": "task-1",
            "conversation_id": "conv-1",
            "iteration": 2,
            "created_at": 1234567890.0,
            "summary": "已读取 8 个文件",
            "next_step_hint": "运行测试验证",
            "exit_reason": "iteration_complete",
            "artifacts": ["a.py"],
            "messages_offset": 12,
        }
        base.update(overrides)
        return TaskCheckpoint(**base)

    def test_to_from_dict_roundtrip(self):
        original = self._ckpt()
        restored = TaskCheckpoint.from_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()

    def test_from_dict_handles_missing_fields(self):
        ckpt = TaskCheckpoint.from_dict({"checkpoint_id": "x"})
        assert ckpt.checkpoint_id == "x"
        assert ckpt.iteration == 0
        assert ckpt.exit_reason == "running"
        assert ckpt.artifacts == []

    def test_append_writes_to_context(self):
        ctx = SessionContext()
        result = ctx.append_task_checkpoint(self._ckpt())
        assert ctx.task_checkpoints == [result]
        assert result["task_id"] == "task-1"
        assert result["exit_reason"] == "iteration_complete"

    def test_append_accepts_dict(self):
        ctx = SessionContext()
        ctx.append_task_checkpoint({
            "checkpoint_id": "raw",
            "task_id": "t",
            "conversation_id": "c",
            "iteration": 1,
            "created_at": 1.0,
            "exit_reason": "completed",
        })
        assert len(ctx.task_checkpoints) == 1
        assert ctx.task_checkpoints[0]["checkpoint_id"] == "raw"

    def test_append_rejects_invalid_type(self):
        ctx = SessionContext()
        with pytest.raises(TypeError):
            ctx.append_task_checkpoint("not-a-checkpoint")  # type: ignore[arg-type]

    def test_append_caps_at_max_keep(self):
        ctx = SessionContext()
        for i in range(60):
            ctx.append_task_checkpoint(self._ckpt(checkpoint_id=f"c{i}"), max_keep=50)
        assert len(ctx.task_checkpoints) == 50
        assert ctx.task_checkpoints[0]["checkpoint_id"] == "c10"
        assert ctx.task_checkpoints[-1]["checkpoint_id"] == "c59"

    def test_latest_filters_by_task(self):
        ctx = SessionContext()
        ctx.append_task_checkpoint(self._ckpt(checkpoint_id="a", task_id="X"))
        ctx.append_task_checkpoint(self._ckpt(checkpoint_id="b", task_id="Y"))
        ctx.append_task_checkpoint(self._ckpt(checkpoint_id="c", task_id="X"))
        assert ctx.latest_task_checkpoint()["checkpoint_id"] == "c"
        assert ctx.latest_task_checkpoint("X")["checkpoint_id"] == "c"
        assert ctx.latest_task_checkpoint("Y")["checkpoint_id"] == "b"
        assert ctx.latest_task_checkpoint("Z") is None

    def test_serialization_roundtrip_via_session_context(self):
        ctx = SessionContext()
        ctx.append_task_checkpoint(self._ckpt())
        restored = SessionContext.from_dict(ctx.to_dict())
        assert restored.task_checkpoints == ctx.task_checkpoints

