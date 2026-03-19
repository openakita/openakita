"""BPStateManager tests."""

import pytest

from seeagent.bestpractice.models import (
    BPInstanceSnapshot,
    BPStatus,
    BestPracticeConfig,
    PendingContextSwitch,
    RunMode,
    SubtaskConfig,
    SubtaskStatus,
)
from seeagent.bestpractice.state_manager import BPStateManager


@pytest.fixture
def bp_config():
    return BestPracticeConfig(
        id="test-bp", name="测试", description="test",
        subtasks=[
            SubtaskConfig(id="s1", name="调研", agent_profile="researcher"),
            SubtaskConfig(id="s2", name="分析", agent_profile="analyst"),
            SubtaskConfig(id="s3", name="报告", agent_profile="writer"),
        ],
    )


@pytest.fixture
def mgr():
    return BPStateManager()


class TestCreateInstance:
    def test_creates_instance(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1", {"topic": "AI"})
        snap = mgr.get(inst_id)
        assert snap is not None
        assert snap.bp_id == "test-bp"
        assert snap.session_id == "sess-1"
        assert snap.status == BPStatus.ACTIVE
        assert snap.initial_input == {"topic": "AI"}
        assert len(snap.subtask_statuses) == 3
        assert all(v == SubtaskStatus.PENDING.value for v in snap.subtask_statuses.values())

    def test_creates_with_run_mode(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1", run_mode=RunMode.AUTO)
        snap = mgr.get(inst_id)
        assert snap.run_mode == RunMode.AUTO


class TestLifecycle:
    def test_suspend_and_resume(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        mgr.suspend(inst_id)
        assert mgr.get(inst_id).status == BPStatus.SUSPENDED
        assert mgr.get(inst_id).suspended_at is not None

        mgr.resume(inst_id)
        assert mgr.get(inst_id).status == BPStatus.ACTIVE
        assert mgr.get(inst_id).suspended_at is None

    def test_complete(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        mgr.complete(inst_id)
        assert mgr.get(inst_id).status == BPStatus.COMPLETED
        assert mgr.get(inst_id).completed_at is not None

    def test_cancel(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        mgr.cancel(inst_id)
        assert mgr.get(inst_id).status == BPStatus.CANCELLED


class TestSubtaskOperations:
    def test_advance_subtask(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        assert mgr.get(inst_id).current_subtask_index == 0
        mgr.advance_subtask(inst_id)
        assert mgr.get(inst_id).current_subtask_index == 1

    def test_update_subtask_status(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        mgr.update_subtask_status(inst_id, "s1", SubtaskStatus.CURRENT)
        assert mgr.get(inst_id).subtask_statuses["s1"] == "current"

    def test_update_subtask_output(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        mgr.update_subtask_output(inst_id, "s1", {"findings": ["data1"]})
        assert mgr.get(inst_id).subtask_outputs["s1"] == {"findings": ["data1"]}

    def test_merge_subtask_output(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        mgr.update_subtask_output(inst_id, "s1", {"a": 1, "b": {"x": 10}})
        merged = mgr.merge_subtask_output(inst_id, "s1", {"b": {"y": 20}, "c": 3})
        assert merged == {"a": 1, "b": {"x": 10, "y": 20}, "c": 3}

    def test_merge_replaces_arrays(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        mgr.update_subtask_output(inst_id, "s1", {"items": [1, 2]})
        merged = mgr.merge_subtask_output(inst_id, "s1", {"items": [3, 4, 5]})
        assert merged["items"] == [3, 4, 5]

    def test_mark_downstream_stale(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        mgr.update_subtask_status(inst_id, "s1", SubtaskStatus.DONE)
        mgr.update_subtask_status(inst_id, "s2", SubtaskStatus.DONE)
        mgr.update_subtask_status(inst_id, "s3", SubtaskStatus.DONE)

        stale = mgr.mark_downstream_stale(inst_id, "s1", bp_config)
        assert stale == ["s2", "s3"]
        snap = mgr.get(inst_id)
        assert snap.subtask_statuses["s1"] == SubtaskStatus.DONE.value
        assert snap.subtask_statuses["s2"] == SubtaskStatus.STALE.value
        assert snap.subtask_statuses["s3"] == SubtaskStatus.STALE.value


class TestQueries:
    def test_get_active(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        active = mgr.get_active("sess-1")
        assert active.instance_id == inst_id

    def test_get_active_returns_none_when_empty(self, mgr):
        assert mgr.get_active("nonexist") is None

    def test_get_all_for_session(self, mgr, bp_config):
        mgr.create_instance(bp_config, "sess-1")
        mgr.create_instance(bp_config, "sess-1")
        mgr.create_instance(bp_config, "sess-2")
        assert len(mgr.get_all_for_session("sess-1")) == 2
        assert len(mgr.get_all_for_session("sess-2")) == 1

    def test_get_status_table(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1")
        mgr.update_subtask_status(inst_id, "s1", SubtaskStatus.DONE)
        table = mgr.get_status_table("sess-1")
        assert "测试" in table
        assert "1/3" in table
        assert "active" in table

    def test_get_status_table_empty(self, mgr):
        assert mgr.get_status_table("empty") == ""


class TestPendingSwitch:
    def test_set_consume_cycle(self, mgr):
        switch = PendingContextSwitch(suspended_instance_id="a", target_instance_id="b")
        mgr.set_pending_switch("sess-1", switch)
        assert mgr.has_pending_switch("sess-1")

        consumed = mgr.consume_pending_switch("sess-1")
        assert consumed.target_instance_id == "b"
        assert not mgr.has_pending_switch("sess-1")

    def test_consume_when_empty(self, mgr):
        assert mgr.consume_pending_switch("none") is None


class TestCooldown:
    def test_cooldown_lifecycle(self, mgr):
        mgr.set_cooldown("sess-1", 3)
        assert mgr.get_cooldown("sess-1") == 3

        remaining = mgr.tick_cooldown("sess-1")
        assert remaining == 2

        mgr.tick_cooldown("sess-1")
        mgr.tick_cooldown("sess-1")
        assert mgr.get_cooldown("sess-1") == 0

    def test_tick_from_zero(self, mgr):
        assert mgr.tick_cooldown("nonexist") == 0


class TestPersistence:
    def test_serialize_roundtrip(self, mgr, bp_config):
        inst_id = mgr.create_instance(bp_config, "sess-1", {"x": 1})
        mgr.update_subtask_output(inst_id, "s1", {"result": "done"})
        mgr.set_cooldown("sess-1", 3)

        data = mgr.serialize_for_session("sess-1")
        assert data["version"] == 1
        assert len(data["instances"]) == 1

        mgr2 = BPStateManager()
        count = mgr2.restore_from_dict("sess-1", data, config_map={"test-bp": bp_config})
        assert count == 1

        snap = mgr2.get(inst_id)
        assert snap is not None
        assert snap.initial_input == {"x": 1}
        assert snap.subtask_outputs["s1"] == {"result": "done"}
        assert snap.bp_config == bp_config
        assert mgr2.get_cooldown("sess-1") == 3

    def test_restore_empty_data(self, mgr):
        assert mgr.restore_from_dict("sess-1", {}) == 0
        assert mgr.restore_from_dict("sess-1", None) == 0

    def test_uses_bp_state_key_not_underscore(self, mgr, bp_config):
        """验证使用 'bp_state' 而非 '_bp_state'（后者会被 Session 序列化过滤）。"""
        inst_id = mgr.create_instance(bp_config, "sess-1")
        metadata = {}
        metadata["bp_state"] = mgr.serialize_for_session("sess-1")

        # 模拟 Session 过滤 _开头的键
        filtered = {k: v for k, v in metadata.items() if not k.startswith("_")}
        assert "bp_state" in filtered

        mgr2 = BPStateManager()
        count = mgr2.restore_from_dict("sess-1", filtered["bp_state"], {"test-bp": bp_config})
        assert count == 1
