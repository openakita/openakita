"""SchemaChain tests."""

from seeagent.bestpractice.models import BestPracticeConfig, SubtaskConfig
from seeagent.bestpractice.schema_chain import SchemaChain


def _make_config(subtask_schemas, final_schema=None):
    subtasks = []
    for i, schema in enumerate(subtask_schemas):
        subtasks.append(SubtaskConfig(
            id=f"s{i}", name=f"S{i}", agent_profile=f"agent-{i}",
            input_schema=schema,
        ))
    return BestPracticeConfig(
        id="test", name="Test", subtasks=subtasks,
        final_output_schema=final_schema,
    )


class TestSchemaChain:
    def setup_method(self):
        self.chain = SchemaChain()

    def test_first_subtask_uses_next_input_schema(self):
        config = _make_config([
            {"type": "object", "properties": {"topic": {"type": "string"}}},
            {"type": "object", "properties": {"findings": {"type": "array"}}},
            {"type": "object", "properties": {"insights": {"type": "array"}}},
        ])
        schema = self.chain.derive_output_schema(config, 0)
        assert schema == {"type": "object", "properties": {"findings": {"type": "array"}}}

    def test_last_subtask_uses_final_output_schema(self):
        final = {"type": "object", "required": ["report"]}
        config = _make_config([{"type": "object"}, {}, {}], final_schema=final)
        schema = self.chain.derive_output_schema(config, 2)
        assert schema == final

    def test_last_subtask_no_final_schema_returns_none(self):
        config = _make_config([{}, {}])
        schema = self.chain.derive_output_schema(config, 1)
        assert schema is None

    def test_empty_next_schema_returns_none(self):
        config = _make_config([{"type": "object"}, {}])
        schema = self.chain.derive_output_schema(config, 0)
        assert schema is None

    def test_out_of_bounds_returns_none(self):
        config = _make_config([{}])
        assert self.chain.derive_output_schema(config, -1) is None
        assert self.chain.derive_output_schema(config, 5) is None

    def test_empty_subtasks_returns_none(self):
        config = BestPracticeConfig(id="x", name="X", subtasks=[])
        assert self.chain.derive_output_schema(config, 0) is None
