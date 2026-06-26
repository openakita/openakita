"""L1 Unit Tests: RuntimeState persistence."""

import json
import pytest
from pathlib import Path

from openakita.config import RuntimeState


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "runtime_state.json"


class TestRuntimeState:
    def test_default_creation(self, state_file):
        state = RuntimeState(state_file=state_file)
        assert isinstance(state, RuntimeState)

    def test_save_and_load(self, state_file):
        state = RuntimeState(state_file=state_file)
        state.save()
        assert state_file.exists()

        state2 = RuntimeState(state_file=state_file)
        state2.load()

    def test_load_nonexistent_file(self, tmp_path):
        state = RuntimeState(state_file=tmp_path / "missing.json")
        state.load()  # Should not crash

    def test_state_file_is_json(self, state_file):
        state = RuntimeState(state_file=state_file)
        state.save()
        content = state_file.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)
