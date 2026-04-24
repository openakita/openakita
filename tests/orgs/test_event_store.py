"""Tests for OrgEventStore — event sourcing, query, audit, reports."""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.orgs.event_store import OrgEventStore


@pytest.fixture()
def event_store(org_dir: Path, persisted_org) -> OrgEventStore:
    return OrgEventStore(org_dir, persisted_org.id)


class TestEmitAndQuery:
    def test_emit_returns_event(self, event_store: OrgEventStore):
        evt = event_store.emit("task_completed", "node_ceo", {"result": "ok"})
        assert evt["event_id"].startswith("evt_")
        assert evt["event_type"] == "task_completed"
        assert evt["actor"] == "node_ceo"

    def test_query_all(self, event_store: OrgEventStore):
        event_store.emit("task_completed", "n1", {"x": 1})
        event_store.emit("task_failed", "n2", {"err": "timeout"})
        events = event_store.query()
        assert len(events) == 2

    def test_query_by_type(self, event_store: OrgEventStore):
        event_store.emit("task_completed", "n1")
        event_store.emit("task_failed", "n2")
        events = event_store.query(event_type="task_failed")
        assert len(events) == 1
        assert events[0]["actor"] == "n2"

    def test_query_by_actor(self, event_store: OrgEventStore):
        event_store.emit("a", "node_A")
        event_store.emit("b", "node_B")
        events = event_store.query(actor="node_A")
        assert len(events) == 1

    def test_query_limit(self, event_store: OrgEventStore):
        for i in range(20):
            event_store.emit("evt", f"n{i}")
        events = event_store.query(limit=5)
        assert len(events) == 5

    def test_query_empty(self, event_store: OrgEventStore):
        assert event_store.query() == []


class TestGetLastPending:
    def test_finds_last_activated(self, event_store: OrgEventStore):
        event_store.emit("node_activated", "node_ceo", {"prompt": "test"})
        event_store.emit("task_completed", "node_ceo")
        pending = event_store.get_last_pending("node_ceo")
        assert pending is not None
        assert pending["event_type"] == "node_activated"

    def test_returns_none_when_empty(self, event_store: OrgEventStore):
        assert event_store.get_last_pending("nonexistent") is None


class TestAuditLog:
    def test_get_audit_log_filters(self, event_store: OrgEventStore):
        event_store.emit("task_completed", "n1")
        event_store.emit("random_event", "n2")
        event_store.emit("org_started", "system")
        log = event_store.get_audit_log(days=1)
        types = {e["event_type"] for e in log}
        assert "random_event" not in types
        assert "task_completed" in types

    def test_write_audit_log_file(self, event_store: OrgEventStore):
        event_store.emit("task_completed", "n1")
        path = event_store.write_audit_log(days=1)
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert "审计日志" in content


class TestReportGeneration:
    def test_summary_report(self, event_store: OrgEventStore):
        for i in range(5):
            event_store.emit("task_completed", f"n{i}")
        event_store.emit("task_failed", "n0", {"error": "timeout"})
        event_store.emit("message_sent", "n1")

        summary = event_store.generate_summary_report(days=1)
        assert summary["tasks_completed"] == 5
        assert summary["tasks_failed"] == 1
        assert summary["messages_sent"] == 1
        assert "task_completed" in summary["event_type_distribution"]

    def test_report_markdown_file(self, event_store: OrgEventStore):
        event_store.emit("task_completed", "n1")
        path = event_store.generate_report_markdown(days=1)
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert "组织运行报告" in content

    def test_empty_summary(self, event_store: OrgEventStore):
        summary = event_store.generate_summary_report(days=1)
        assert summary["total_events"] == 0
        assert summary["tasks_completed"] == 0

