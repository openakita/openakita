"""Tests for OrgBlackboard — three-tier shared memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.orgs.blackboard import OrgBlackboard, MAX_ORG_MEMORIES
from openakita.orgs.models import MemoryScope, MemoryType


@pytest.fixture()
def blackboard(org_dir: Path, persisted_org) -> OrgBlackboard:
    return OrgBlackboard(org_dir, persisted_org.id)


class TestWriteRead:
    def test_write_and_read_org(self, blackboard: OrgBlackboard):
        entry = blackboard.write_org("决定使用Python", "node_ceo", MemoryType.DECISION, tags=["tech"])
        assert entry.id.startswith("mem_")
        assert entry.scope == MemoryScope.ORG

        entries = blackboard.read_org()
        assert len(entries) == 1
        assert entries[0].content == "决定使用Python"

    def test_write_and_read_department(self, blackboard: OrgBlackboard):
        blackboard.write_department("技术部", "采用微服务架构", "node_cto", MemoryType.DECISION)
        entries = blackboard.read_department("技术部")
        assert len(entries) == 1
        assert entries[0].scope_owner == "技术部"

    def test_write_and_read_node(self, blackboard: OrgBlackboard):
        blackboard.write_node("node_dev", "完成了模块A", MemoryType.PROGRESS)
        entries = blackboard.read_node("node_dev")
        assert len(entries) == 1
        assert entries[0].memory_type == MemoryType.PROGRESS

    def test_read_empty(self, blackboard: OrgBlackboard):
        assert blackboard.read_org() == []
        assert blackboard.read_department("不存在") == []
        assert blackboard.read_node("不存在") == []


class TestTagFilter:
    def test_read_with_tag(self, blackboard: OrgBlackboard):
        blackboard.write_org("A", "n1", tags=["alpha"])
        blackboard.write_org("B", "n1", tags=["beta"])
        result = blackboard.read_org(tag="alpha")
        assert len(result) == 1
        assert result[0].content == "A"


class TestSummaries:
    def test_org_summary_empty(self, blackboard: OrgBlackboard):
        assert "(暂无组织级记忆)" in blackboard.get_org_summary()

    def test_org_summary(self, blackboard: OrgBlackboard):
        blackboard.write_org("项目启动", "n1", MemoryType.FACT)
        s = blackboard.get_org_summary()
        assert "项目启动" in s
        assert "[fact]" in s

    def test_dept_summary(self, blackboard: OrgBlackboard):
        blackboard.write_department("技术部", "代码规范已确立", "n1")
        s = blackboard.get_dept_summary("技术部")
        assert "代码规范已确立" in s

    def test_node_summary(self, blackboard: OrgBlackboard):
        blackboard.write_node("node_dev", "调试完成", MemoryType.PROGRESS)
        s = blackboard.get_node_summary("node_dev")
        assert "调试完成" in s


class TestQuery:
    def test_query_all(self, blackboard: OrgBlackboard):
        blackboard.write_org("org_fact", "n1")
        blackboard.write_department("技术部", "dept_fact", "n2")
        blackboard.write_node("node_dev", "node_fact")
        results = blackboard.query()
        assert len(results) == 3

    def test_query_by_scope(self, blackboard: OrgBlackboard):
        blackboard.write_org("A", "n1")
        blackboard.write_node("node_dev", "B")
        results = blackboard.query(scope=MemoryScope.ORG)
        assert len(results) == 1
        assert results[0].content == "A"

    def test_query_by_type(self, blackboard: OrgBlackboard):
        blackboard.write_org("fact1", "n1", MemoryType.FACT)
        blackboard.write_org("decision1", "n1", MemoryType.DECISION)
        results = blackboard.query(memory_type=MemoryType.DECISION)
        assert len(results) == 1
        assert results[0].content == "decision1"


class TestDeleteEntry:
    def test_delete(self, blackboard: OrgBlackboard):
        entry = blackboard.write_org("要删除的", "n1")
        assert blackboard.delete_entry(entry.id) is True
        assert blackboard.read_org() == []

    def test_delete_nonexistent(self, blackboard: OrgBlackboard):
        assert blackboard.delete_entry("fake_id") is False


class TestEviction:
    def test_org_memory_eviction(self, blackboard: OrgBlackboard):
        for i in range(MAX_ORG_MEMORIES + 10):
            blackboard.write_org(f"mem_{i}", "n1", importance=i / (MAX_ORG_MEMORIES + 10))
        entries = blackboard.read_org(limit=999)
        assert len(entries) <= MAX_ORG_MEMORIES

