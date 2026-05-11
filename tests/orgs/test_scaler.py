"""Tests for OrgScaler — clone, recruit, dismiss, approval workflow."""

from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock

import pytest

from openakita.orgs.scaler import OrgScaler
from openakita.orgs.inbox import OrgInbox


@pytest.fixture()
def scaler(mock_runtime, persisted_org) -> OrgScaler:
    inbox = OrgInbox(mock_runtime)
    mock_runtime.get_inbox = MagicMock(return_value=inbox)
    return OrgScaler(mock_runtime)


class TestAutoCloneThreshold:
    async def test_auto_clone_triggers_when_pending_reaches_threshold(
        self, scaler: OrgScaler, persisted_org, mock_runtime,
    ):
        source = persisted_org.get_node("node_dev")
        source.auto_clone_enabled = True
        source.auto_clone_threshold = 2
        source.auto_clone_max = 99
        source.max_concurrent_tasks = 99
        persisted_org.max_nodes = 200
        clone = await scaler.maybe_auto_clone(persisted_org.id, source.id, pending_count=2)

        assert clone is not None
        assert clone.clone_source == source.id
        assert clone.max_concurrent_tasks == 99
        assert clone.auto_clone_threshold == 3

    async def test_auto_clone_does_not_trigger_below_threshold(
        self, scaler: OrgScaler, persisted_org,
    ):
        source = persisted_org.get_node("node_dev")
        source.auto_clone_enabled = True
        source.auto_clone_threshold = 2
        source.auto_clone_max = 99
        before_count = len(persisted_org.nodes)
        clone = await scaler.maybe_auto_clone(persisted_org.id, source.id, pending_count=1)

        assert clone is None
        assert len(persisted_org.nodes) == before_count


class TestRequestClone:
    async def test_creates_pending_request(self, scaler: OrgScaler, persisted_org):
        req = await scaler.request_clone(
            persisted_org.id, "node_cto", "node_dev", reason="工作量过大",
        )
        assert req.request_type == "clone"
        assert req.status == "pending"
        assert req.requester_node_id == "node_cto"

        pending = scaler.get_pending_requests(persisted_org.id)
        assert len(pending) == 1

    async def test_clone_emits_event(self, scaler: OrgScaler, persisted_org, mock_runtime):
        es = mock_runtime.get_event_store()
        await scaler.request_clone(persisted_org.id, "node_cto", "node_dev", reason="人手不足")
        events = es.query(event_type="scaling_requested")
        assert len(events) >= 1


class TestRequestRecruit:
    def test_creates_recruit_request(self, scaler: OrgScaler, persisted_org):
        req = scaler.request_recruit(
            persisted_org.id, "node_ceo",
            role_title="安全专员", role_goal="负责安全审计",
            department="技术部", parent_node_id="node_cto",
            reason="缺少安全人才",
        )
        assert req.request_type == "recruit"
        assert req.role_title == "安全专员"


class TestApproveReject:
    async def test_approve_clone(self, scaler: OrgScaler, persisted_org, mock_runtime):
        mock_runtime._save_org = AsyncMock()
        req = await scaler.request_clone(persisted_org.id, "node_cto", "node_dev", reason="忙")
        result = await scaler.approve_request(persisted_org.id, req.id, "admin")
        assert result is not None
        assert result.status == "approved"
        assert result.result_node_id is not None

        all_reqs = scaler.get_pending_requests(persisted_org.id)
        pending_only = [r for r in all_reqs if r.status == "pending"]
        assert len(pending_only) == 0

    async def test_reject_request(self, scaler: OrgScaler, persisted_org):
        req = await scaler.request_clone(persisted_org.id, "node_cto", "node_dev", reason="忙")
        result = scaler.reject_request(persisted_org.id, req.id, "admin", reason="不需要")
        assert result is not None
        assert result.status == "rejected"

    async def test_approve_nonexistent_raises(self, scaler: OrgScaler, persisted_org):
        with pytest.raises(ValueError, match="not found"):
            await scaler.approve_request(persisted_org.id, "fake_id", "admin")


class TestDismiss:
    async def test_dismiss_ephemeral(self, scaler: OrgScaler, persisted_org, mock_runtime):
        persisted_org.nodes[2].ephemeral = True
        result = await scaler.dismiss_node(persisted_org.id, persisted_org.nodes[2].id, by="admin")
        assert result is True

    async def test_dismiss_non_ephemeral_returns_false(self, scaler: OrgScaler, persisted_org, mock_runtime):
        result = await scaler.dismiss_node(persisted_org.id, persisted_org.nodes[2].id, by="admin")
        assert result is False


class TestGetPendingRequests:
    def test_empty_org(self, scaler: OrgScaler):
        assert scaler.get_pending_requests("nonexistent") == []

    async def test_after_multiple_requests(self, scaler: OrgScaler, persisted_org):
        await scaler.request_clone(persisted_org.id, "node_cto", "node_dev", reason="忙")
        scaler.request_recruit(
            persisted_org.id, "node_ceo",
            role_title="安全", role_goal="审计", department="技术部",
            parent_node_id="node_cto", reason="需要",
        )
        reqs = scaler.get_pending_requests(persisted_org.id)
        assert len(reqs) == 2

