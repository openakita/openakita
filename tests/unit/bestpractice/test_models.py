"""Tests for BP models changes: WAITING_INPUT status + supplemented_inputs field."""
from seeagent.bestpractice.models import SubtaskStatus, BPInstanceSnapshot


class TestSubtaskStatusWaitingInput:
    def test_waiting_input_exists(self):
        assert hasattr(SubtaskStatus, "WAITING_INPUT")
        assert SubtaskStatus.WAITING_INPUT.value == "waiting_input"

    def test_waiting_input_roundtrip(self):
        status = SubtaskStatus("waiting_input")
        assert status == SubtaskStatus.WAITING_INPUT

    def test_all_statuses_are_strings(self):
        for s in SubtaskStatus:
            assert isinstance(s.value, str)


class TestSupplementedInputs:
    def test_snapshot_has_supplemented_inputs(self):
        snap = BPInstanceSnapshot(
            bp_id="test",
            instance_id="bp-test",
            session_id="sess-1",
            created_at=0.0,
            subtask_statuses={},
            initial_input={},
            subtask_outputs={},
            context_summary="",
        )
        assert hasattr(snap, "supplemented_inputs")
        assert snap.supplemented_inputs == {}

    def test_supplemented_inputs_independent_per_subtask(self):
        snap = BPInstanceSnapshot(
            bp_id="test",
            instance_id="bp-test",
            session_id="sess-1",
            created_at=0.0,
            subtask_statuses={},
            initial_input={},
            subtask_outputs={},
            context_summary="",
        )
        snap.supplemented_inputs["st1"] = {"field_a": "value"}
        snap.supplemented_inputs["st2"] = {"field_b": 42}
        assert snap.supplemented_inputs["st1"] == {"field_a": "value"}
        assert snap.supplemented_inputs["st2"] == {"field_b": 42}

    def test_supplemented_inputs_in_serialize(self):
        snap = BPInstanceSnapshot(
            bp_id="test",
            instance_id="bp-test",
            session_id="sess-1",
            created_at=0.0,
            subtask_statuses={},
            initial_input={},
            subtask_outputs={},
            context_summary="",
        )
        snap.supplemented_inputs["st1"] = {"field_a": "value"}
        data = snap.serialize()
        assert "supplemented_inputs" in data
        assert data["supplemented_inputs"] == {"st1": {"field_a": "value"}}

    def test_supplemented_inputs_in_deserialize(self):
        data = {
            "bp_id": "test",
            "instance_id": "bp-test",
            "session_id": "sess-1",
            "created_at": 0.0,
            "subtask_statuses": {},
            "initial_input": {},
            "subtask_outputs": {},
            "context_summary": "",
            "supplemented_inputs": {"st1": {"field_a": "value"}},
        }
        snap = BPInstanceSnapshot.deserialize(data)
        assert snap.supplemented_inputs == {"st1": {"field_a": "value"}}

    def test_supplemented_inputs_deserialize_missing_key(self):
        """Deserializing old data without supplemented_inputs should default to {}."""
        data = {
            "bp_id": "test",
            "instance_id": "bp-test",
            "session_id": "sess-1",
            "created_at": 0.0,
            "subtask_statuses": {},
            "initial_input": {},
            "subtask_outputs": {},
            "context_summary": "",
        }
        snap = BPInstanceSnapshot.deserialize(data)
        assert snap.supplemented_inputs == {}
