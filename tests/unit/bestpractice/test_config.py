"""BP config data model tests."""

import pytest
import yaml

from seeagent.bestpractice.config import (
    load_bp_config,
    load_bp_config_from_yaml,
    validate_bp_config,
)
from seeagent.bestpractice.models import (
    BPInstanceSnapshot,
    BPStatus,
    BestPracticeConfig,
    PendingContextSwitch,
    RunMode,
    SubtaskConfig,
    SubtaskStatus,
    TriggerConfig,
    TriggerType,
)


# ── Enums ──────────────────────────────────────────────────────


class TestEnums:
    def test_run_mode_values(self):
        assert RunMode.MANUAL.value == "manual"
        assert RunMode.AUTO.value == "auto"

    def test_bp_status_values(self):
        assert BPStatus.ACTIVE.value == "active"
        assert BPStatus.SUSPENDED.value == "suspended"
        assert BPStatus.COMPLETED.value == "completed"
        assert BPStatus.CANCELLED.value == "cancelled"

    def test_subtask_status_values(self):
        assert SubtaskStatus.PENDING.value == "pending"
        assert SubtaskStatus.DONE.value == "done"
        assert SubtaskStatus.STALE.value == "stale"
        assert SubtaskStatus.FAILED.value == "failed"

    def test_trigger_type_values(self):
        assert TriggerType.COMMAND.value == "command"
        assert TriggerType.CONTEXT.value == "context"
        assert TriggerType.CRON.value == "cron"


# ── SubtaskConfig ──────────────────────────────────────────────


class TestSubtaskConfig:
    def test_minimal(self):
        st = SubtaskConfig(id="research", name="调研", agent_profile="researcher")
        assert st.id == "research"
        assert st.agent_profile == "researcher"
        assert st.input_schema == {}
        assert st.description == ""
        assert st.depends_on == []
        assert st.input_mapping == {}
        assert st.timeout_seconds is None
        assert st.max_retries == 0

    def test_with_full_fields(self):
        schema = {"type": "object", "properties": {"topic": {"type": "string"}}}
        st = SubtaskConfig(
            id="a", name="A", agent_profile="x",
            input_schema=schema, depends_on=["b"],
            input_mapping={"topic": "b"}, timeout_seconds=300, max_retries=2,
        )
        assert st.input_schema == schema
        assert st.depends_on == ["b"]
        assert st.input_mapping == {"topic": "b"}
        assert st.timeout_seconds == 300
        assert st.max_retries == 2


# ── TriggerConfig ──────────────────────────────────────────────


class TestTriggerConfig:
    def test_command_trigger(self):
        t = TriggerConfig(type=TriggerType.COMMAND, pattern="执行调研")
        assert t.type == TriggerType.COMMAND
        assert t.pattern == "执行调研"

    def test_context_trigger(self):
        t = TriggerConfig(type=TriggerType.CONTEXT, conditions=["市场", "调研"])
        assert t.conditions == ["市场", "调研"]

    def test_cron_trigger(self):
        t = TriggerConfig(type=TriggerType.CRON, cron="0 9 * * 1")
        assert t.cron == "0 9 * * 1"

    def test_string_type_auto_converts(self):
        t = TriggerConfig(type="command", pattern="test")
        assert t.type == TriggerType.COMMAND


# ── BestPracticeConfig ─────────────────────────────────────────


class TestBestPracticeConfig:
    def test_minimal(self):
        bp = BestPracticeConfig(
            id="test-bp", name="测试",
            subtasks=[SubtaskConfig(id="s1", name="步骤1", agent_profile="default")],
        )
        assert bp.id == "test-bp"
        assert len(bp.subtasks) == 1
        assert bp.triggers == []
        assert bp.final_output_schema is None
        assert bp.description == ""
        assert bp.default_run_mode == RunMode.MANUAL

    def test_string_run_mode_converts(self):
        bp = BestPracticeConfig(
            id="x", name="X", subtasks=[], default_run_mode="auto",
        )
        assert bp.default_run_mode == RunMode.AUTO


# ── load_bp_config ─────────────────────────────────────────────


class TestLoadBpConfig:
    def test_load_from_dict(self):
        raw = {
            "id": "market-research",
            "name": "市场调研",
            "description": "流程",
            "subtasks": [
                {"id": "s1", "name": "调研", "agent_profile": "researcher",
                 "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}},
                                  "required": ["topic"]}},
                {"id": "s2", "name": "分析", "agent_profile": "analyst"},
            ],
            "triggers": [
                {"type": "command", "pattern": "执行调研"},
                {"type": "context", "conditions": ["市场", "调研"]},
            ],
            "final_output_schema": {"type": "object", "required": ["title"]},
            "default_run_mode": "manual",
        }
        bp = load_bp_config(raw)
        assert bp.id == "market-research"
        assert len(bp.subtasks) == 2
        assert bp.subtasks[0].input_schema["required"] == ["topic"]
        assert len(bp.triggers) == 2
        assert bp.triggers[0].type == TriggerType.COMMAND
        assert bp.final_output_schema is not None

    def test_unwraps_best_practice_key(self):
        raw = {
            "best_practice": {
                "id": "wrapped", "name": "包装",
                "subtasks": [{"id": "s1", "name": "S", "agent_profile": "a"}],
            }
        }
        bp = load_bp_config(raw)
        assert bp.id == "wrapped"

    def test_load_from_yaml_string(self):
        yaml_str = """
id: "test"
name: "Test"
subtasks:
  - id: "s1"
    name: "S1"
    agent_profile: "a"
"""
        bp = load_bp_config_from_yaml(yaml_str)
        assert bp.id == "test"

    def test_load_real_config(self):
        """验证真实 config.yaml 能被正确加载。"""
        import pathlib
        config_path = pathlib.Path(__file__).parents[3] / "best_practice" / "market-research-report" / "config.yaml"
        if config_path.exists():
            text = config_path.read_text()
            bp = load_bp_config_from_yaml(text)
            assert bp.id == "market-research-report"
            assert len(bp.subtasks) >= 3


# ── validate_bp_config ─────────────────────────────────────────


class TestValidateBpConfig:
    def test_valid_config(self):
        bp = BestPracticeConfig(
            id="ok", name="OK",
            subtasks=[SubtaskConfig(id="s1", name="S1", agent_profile="a")],
            triggers=[TriggerConfig(type=TriggerType.COMMAND, pattern="go")],
        )
        assert validate_bp_config(bp) == []

    def test_missing_id(self):
        bp = BestPracticeConfig(
            id="", name="X", subtasks=[SubtaskConfig(id="s1", name="S1", agent_profile="a")],
        )
        errors = validate_bp_config(bp)
        assert any("id" in e for e in errors)

    def test_no_subtasks(self):
        bp = BestPracticeConfig(id="x", name="X", subtasks=[])
        errors = validate_bp_config(bp)
        assert any("subtask" in e for e in errors)

    def test_duplicate_subtask_ids(self):
        bp = BestPracticeConfig(
            id="x", name="X",
            subtasks=[
                SubtaskConfig(id="s1", name="A", agent_profile="a"),
                SubtaskConfig(id="s1", name="B", agent_profile="b"),
            ],
        )
        errors = validate_bp_config(bp)
        assert any("duplicate" in e for e in errors)

    def test_missing_agent_profile(self):
        bp = BestPracticeConfig(
            id="x", name="X",
            subtasks=[SubtaskConfig(id="s1", name="S1", agent_profile="")],
        )
        errors = validate_bp_config(bp)
        assert any("agent_profile" in e for e in errors)

    def test_depends_on_unknown_id(self):
        bp = BestPracticeConfig(
            id="x", name="X",
            subtasks=[SubtaskConfig(id="s1", name="S1", agent_profile="a", depends_on=["nonexist"])],
        )
        errors = validate_bp_config(bp)
        assert any("nonexist" in e for e in errors)

    def test_input_mapping_unknown_upstream(self):
        bp = BestPracticeConfig(
            id="x", name="X",
            subtasks=[
                SubtaskConfig(id="s1", name="S1", agent_profile="a",
                              input_mapping={"field": "ghost"}),
            ],
        )
        errors = validate_bp_config(bp)
        assert any("ghost" in e for e in errors)

    def test_command_trigger_without_pattern(self):
        bp = BestPracticeConfig(
            id="x", name="X",
            subtasks=[SubtaskConfig(id="s1", name="S1", agent_profile="a")],
            triggers=[TriggerConfig(type=TriggerType.COMMAND)],
        )
        errors = validate_bp_config(bp)
        assert any("pattern" in e for e in errors)


# ── BPInstanceSnapshot ─────────────────────────────────────────


class TestBPInstanceSnapshot:
    def test_new_instance_id_format(self):
        iid = BPInstanceSnapshot.new_instance_id()
        assert iid.startswith("bp-")
        assert len(iid) == 11  # "bp-" + 8 hex chars

    def test_serialize_roundtrip(self):
        snap = BPInstanceSnapshot(
            bp_id="test", instance_id="bp-12345678", session_id="sess-1",
            status=BPStatus.ACTIVE, run_mode=RunMode.AUTO,
            subtask_statuses={"s1": "done", "s2": "pending"},
            initial_input={"topic": "AI"},
            subtask_outputs={"s1": {"result": "ok"}},
        )
        data = snap.serialize()
        assert data["status"] == "active"
        assert data["run_mode"] == "auto"
        assert "bp_config" not in data

        restored = BPInstanceSnapshot.deserialize(data)
        assert restored.bp_id == "test"
        assert restored.status == BPStatus.ACTIVE
        assert restored.run_mode == RunMode.AUTO
        assert restored.initial_input == {"topic": "AI"}
        assert restored.subtask_outputs["s1"] == {"result": "ok"}
        assert restored.bp_config is None


# ── PendingContextSwitch ──────────────────────────────────────


class TestPendingContextSwitch:
    def test_creation(self):
        pcs = PendingContextSwitch(
            suspended_instance_id="bp-aaa", target_instance_id="bp-bbb",
        )
        assert pcs.suspended_instance_id == "bp-aaa"
        assert pcs.target_instance_id == "bp-bbb"
        assert pcs.created_at > 0
