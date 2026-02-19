"""L1 Unit Tests: Memory data models (v2)."""

import pytest
from datetime import datetime

from openakita.memory.types import (
    ActionNode,
    Episode,
    Memory,
    MemoryPriority,
    MemoryType,
    Scratchpad,
    SemanticMemory,
)


class TestMemoryType:
    def test_all_types_exist(self):
        assert MemoryType.FACT.value == "fact"
        assert MemoryType.PREFERENCE.value == "preference"
        assert MemoryType.SKILL.value == "skill"
        assert MemoryType.CONTEXT.value == "context"
        assert MemoryType.RULE.value == "rule"
        assert MemoryType.ERROR.value == "error"
        assert MemoryType.PERSONA_TRAIT.value == "persona_trait"


class TestMemoryPriority:
    def test_all_priorities_exist(self):
        assert MemoryPriority.TRANSIENT.value == "transient"
        assert MemoryPriority.SHORT_TERM.value == "short_term"
        assert MemoryPriority.LONG_TERM.value == "long_term"
        assert MemoryPriority.PERMANENT.value == "permanent"


class TestMemoryCreation:
    def test_default_values(self):
        m = Memory(content="test fact")
        assert m.type == MemoryType.FACT
        assert m.priority == MemoryPriority.SHORT_TERM
        assert m.content == "test fact"
        assert m.importance_score == 0.5
        assert m.access_count == 0
        assert isinstance(m.id, str)
        assert len(m.id) > 0

    def test_custom_values(self):
        m = Memory(
            content="User likes Python",
            type=MemoryType.PREFERENCE,
            priority=MemoryPriority.LONG_TERM,
            importance_score=0.9,
            tags=["programming", "preference"],
            source="conversation",
        )
        assert m.type == MemoryType.PREFERENCE
        assert m.priority == MemoryPriority.LONG_TERM
        assert m.importance_score == 0.9
        assert "programming" in m.tags
        assert m.source == "conversation"

    def test_timestamps_set(self):
        before = datetime.now()
        m = Memory(content="test")
        after = datetime.now()
        assert before <= m.created_at <= after
        assert before <= m.updated_at <= after

    def test_unique_ids(self):
        m1 = Memory(content="a")
        m2 = Memory(content="b")
        assert m1.id != m2.id

    def test_tags_default_empty(self):
        m = Memory(content="test")
        assert m.tags == []

    def test_tags_are_independent(self):
        m1 = Memory(content="a")
        m2 = Memory(content="b")
        m1.tags.append("tag1")
        assert "tag1" not in m2.tags


class TestSemanticMemory:
    """v2: SemanticMemory 新增字段测试"""

    def test_backward_compat_alias(self):
        assert Memory is SemanticMemory

    def test_v2_fields_default(self):
        m = SemanticMemory(content="test")
        assert m.subject == ""
        assert m.predicate == ""
        assert m.confidence == 0.5
        assert m.decay_rate == 0.1
        assert m.last_accessed_at is None
        assert m.superseded_by is None
        assert m.source_episode_id is None

    def test_v2_fields_set(self):
        m = SemanticMemory(
            content="Python 版本 3.12",
            subject="Python",
            predicate="版本",
            confidence=0.9,
            source_episode_id="ep-123",
        )
        assert m.subject == "Python"
        assert m.predicate == "版本"
        assert m.confidence == 0.9
        assert m.source_episode_id == "ep-123"

    def test_to_dict_includes_v2(self):
        m = SemanticMemory(content="x", subject="user", predicate="偏好")
        d = m.to_dict()
        assert "subject" in d
        assert "predicate" in d
        assert "confidence" in d
        assert "decay_rate" in d
        assert "superseded_by" in d
        assert "source_episode_id" in d

    def test_from_dict_v2(self):
        d = {
            "content": "test",
            "subject": "user",
            "predicate": "语言",
            "confidence": 0.8,
            "decay_rate": 0.05,
        }
        m = SemanticMemory.from_dict(d)
        assert m.subject == "user"
        assert m.predicate == "语言"
        assert m.confidence == 0.8

    def test_from_dict_v1_compat(self):
        d = {"content": "old data", "type": "fact"}
        m = SemanticMemory.from_dict(d)
        assert m.content == "old data"
        assert m.subject == ""
        assert m.confidence == 0.5


class TestActionNode:
    def test_defaults(self):
        n = ActionNode(tool_name="read_file")
        assert n.tool_name == "read_file"
        assert n.success is True
        assert n.error_message is None

    def test_to_dict(self):
        n = ActionNode(
            tool_name="run_shell",
            key_params={"command": "ls -la"},
            success=False,
            error_message="permission denied",
        )
        d = n.to_dict()
        assert d["tool_name"] == "run_shell"
        assert d["success"] is False
        assert d["error_message"] == "permission denied"

    def test_from_dict(self):
        d = {"tool_name": "write_file", "key_params": {"path": "a.py"}, "success": True}
        n = ActionNode.from_dict(d)
        assert n.tool_name == "write_file"


class TestEpisode:
    def test_defaults(self):
        ep = Episode(session_id="s1", summary="did stuff")
        assert ep.session_id == "s1"
        assert ep.outcome == "completed"
        assert ep.action_nodes == []

    def test_to_from_dict(self):
        nodes = [ActionNode(tool_name="read_file")]
        ep = Episode(
            session_id="s1",
            summary="读取文件并修改",
            goal="修改配置文件",
            outcome="success",
            action_nodes=nodes,
            entities=["config.py"],
            tools_used=["read_file", "write_file"],
        )
        d = ep.to_dict()
        assert d["goal"] == "修改配置文件"
        assert len(d["action_nodes"]) == 1

        ep2 = Episode.from_dict(d)
        assert ep2.goal == "修改配置文件"
        assert len(ep2.action_nodes) == 1
        assert ep2.action_nodes[0].tool_name == "read_file"

    def test_to_markdown(self):
        ep = Episode(summary="fixed bug", goal="修复 bug", outcome="success")
        md = ep.to_markdown()
        assert "修复 bug" in md
        assert "success" in md


class TestScratchpad:
    def test_defaults(self):
        s = Scratchpad()
        assert s.user_id == "default"
        assert s.content == ""
        assert s.active_projects == []

    def test_to_from_dict(self):
        s = Scratchpad(
            content="正在重构记忆系统",
            active_projects=["memory-redesign"],
            current_focus="FTS5",
            open_questions=["性能如何?"],
            next_steps=["写测试"],
        )
        d = s.to_dict()
        s2 = Scratchpad.from_dict(d)
        assert s2.content == "正在重构记忆系统"
        assert s2.active_projects == ["memory-redesign"]
        assert s2.open_questions == ["性能如何?"]
