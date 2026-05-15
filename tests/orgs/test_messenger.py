"""Tests for OrgMessenger — message routing, deadlock detection, bandwidth."""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.orgs.messenger import NodeMailbox, OrgMessenger
from openakita.orgs.models import MsgType, OrgMessage
from .conftest import make_node


@pytest.fixture()
def org_with_messenger(org_dir: Path, persisted_org):
    return OrgMessenger(persisted_org, org_dir)


# ---------------------------------------------------------------------------
# NodeMailbox
# ---------------------------------------------------------------------------


class TestNodeMailbox:
    async def test_put_and_get(self):
        mb = NodeMailbox("n1")
        msg = OrgMessage(from_node="a", to_node="n1", content="hello")
        await mb.put(msg)
        assert mb.pending_count == 1
        got = await mb.get(timeout=1.0)
        assert got is not None
        assert got.content == "hello"

    async def test_get_timeout(self):
        mb = NodeMailbox("n1")
        got = await mb.get(timeout=0.1)
        assert got is None

    async def test_pause_buffers_messages(self):
        mb = NodeMailbox("n1")
        mb.pause()
        assert mb.is_paused is True
        msg = OrgMessage(from_node="a", to_node="n1", content="buffered")
        await mb.put(msg)
        assert mb.pending_count == 0
        assert mb.frozen_buffer_count == 1
        mb.resume()
        assert mb.frozen_buffer_count == 0
        assert mb.pending_count == 1
        got = await mb.get(timeout=1.0)
        assert got is not None
        assert got.content == "buffered"

    async def test_resume(self):
        mb = NodeMailbox("n1")
        mb.pause()
        mb.resume()
        assert mb.is_paused is False
        msg = OrgMessage(from_node="a", to_node="n1", content="ok")
        await mb.put(msg)
        assert mb.pending_count == 1

    async def test_priority_ordering(self):
        mb = NodeMailbox("n1")
        low = OrgMessage(from_node="a", to_node="n1", content="low", priority=0)
        high = OrgMessage(from_node="a", to_node="n1", content="high", priority=10)
        await mb.put(low)
        await mb.put(high)
        first = await mb.get(timeout=1.0)
        assert first.content == "high"


# ---------------------------------------------------------------------------
# OrgMessenger — Send
# ---------------------------------------------------------------------------


class TestMessengerSend:
    async def test_send_to_known_node(self, org_with_messenger: OrgMessenger):
        msg = OrgMessage(
            org_id="org_test", from_node="node_ceo", to_node="node_cto",
            msg_type=MsgType.TASK_ASSIGN, content="do X",
        )
        result = await org_with_messenger.send(msg)
        assert result is True
        mb = org_with_messenger.get_mailbox("node_cto")
        assert mb is not None
        assert mb.pending_count == 1

    async def test_send_to_unknown_node(self, org_with_messenger: OrgMessenger):
        msg = OrgMessage(
            org_id="org_test", from_node="node_ceo", to_node="no_such_node",
            msg_type=MsgType.TASK_ASSIGN, content="X",
        )
        result = await org_with_messenger.send(msg)
        assert result is False

    async def test_send_task_helper(self, org_with_messenger: OrgMessenger):
        msg = await org_with_messenger.send_task("node_ceo", "node_cto", "write tests")
        assert msg.msg_type == MsgType.TASK_ASSIGN
        assert msg.content == "write tests"

    async def test_send_result_clears_wait_graph(self, org_with_messenger: OrgMessenger):
        await org_with_messenger.send_task("node_ceo", "node_cto", "task")
        assert "node_cto" in org_with_messenger._wait_graph.get("node_ceo", set())

        await org_with_messenger.send_result("node_cto", "node_ceo", "done")
        assert "node_ceo" not in org_with_messenger._wait_graph.get("node_cto", set())

    async def test_escalate(self, org_with_messenger: OrgMessenger):
        msg = await org_with_messenger.escalate("node_cto", "需要帮助")
        assert msg is not None
        assert msg.to_node == "node_ceo"
        assert msg.msg_type == MsgType.ESCALATE

    async def test_escalate_root_returns_none(self, org_with_messenger: OrgMessenger):
        msg = await org_with_messenger.escalate("node_ceo", "我已是顶级")
        assert msg is None


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


class TestBroadcast:
    async def test_broadcast(self, org_with_messenger: OrgMessenger):
        msg = OrgMessage(
            org_id="org_test", from_node="node_ceo",
            to_node=None, msg_type=MsgType.BROADCAST, content="全员通知",
        )
        result = await org_with_messenger.send(msg)
        assert result is True
        for nid in ("node_cto", "node_dev"):
            mb = org_with_messenger.get_mailbox(nid)
            assert mb.pending_count == 1


# ---------------------------------------------------------------------------
# Freeze / Unfreeze
# ---------------------------------------------------------------------------


class TestFreezeMailbox:
    async def test_freeze_and_unfreeze(self, org_with_messenger: OrgMessenger):
        org_with_messenger.freeze_mailbox("node_cto")
        mb = org_with_messenger.get_mailbox("node_cto")
        assert mb.is_paused is True

        org_with_messenger.unfreeze_mailbox("node_cto")
        assert mb.is_paused is False


# ---------------------------------------------------------------------------
# Deadlock detection
# ---------------------------------------------------------------------------


class TestDeadlockDetection:
    def test_no_deadlock(self, org_with_messenger: OrgMessenger):
        org_with_messenger._wait_graph["A"] = {"B"}
        org_with_messenger._wait_graph["B"] = {"C"}
        assert org_with_messenger.check_deadlock() is None

    def test_simple_cycle(self, org_with_messenger: OrgMessenger):
        org_with_messenger._wait_graph["A"] = {"B"}
        org_with_messenger._wait_graph["B"] = {"A"}
        cycles = org_with_messenger.check_deadlock()
        assert cycles is not None
        assert len(cycles) >= 1

    def test_larger_cycle(self, org_with_messenger: OrgMessenger):
        org_with_messenger._wait_graph["A"] = {"B"}
        org_with_messenger._wait_graph["B"] = {"C"}
        org_with_messenger._wait_graph["C"] = {"A"}
        cycles = org_with_messenger.check_deadlock()
        assert cycles is not None


# ---------------------------------------------------------------------------
# Bandwidth
# ---------------------------------------------------------------------------


class TestBandwidth:
    async def test_bandwidth_limit(self, org_with_messenger: OrgMessenger):
        org = org_with_messenger._org
        edge = org.edges[0]
        edge.bandwidth_limit = 3

        for i in range(3):
            msg = OrgMessage(
                org_id=org.id, from_node=edge.source, to_node=edge.target,
                msg_type=MsgType.TASK_ASSIGN, content=f"task {i}",
                edge_id=edge.id,
            )
            assert await org_with_messenger.send(msg) is True

        msg = OrgMessage(
            org_id=org.id, from_node=edge.source, to_node=edge.target,
            msg_type=MsgType.TASK_ASSIGN, content="over limit",
            edge_id=edge.id,
        )
        assert await org_with_messenger.send(msg) is False


# ---------------------------------------------------------------------------
# Mark processed
# ---------------------------------------------------------------------------


class TestMarkProcessed:
    async def test_mark_processed(self, org_with_messenger: OrgMessenger):
        msg = await org_with_messenger.send_task("node_ceo", "node_cto", "task")
        assert msg.id in org_with_messenger._pending_messages
        org_with_messenger.mark_processed(msg.id)
        assert msg.id not in org_with_messenger._pending_messages


# ---------------------------------------------------------------------------
# Background tasks lifecycle
# ---------------------------------------------------------------------------


class TestUpdateOrg:
    def test_update_org_adds_new_mailboxes(self, org_with_messenger: OrgMessenger):
        org = org_with_messenger._org
        new_node = make_node("node_new", "新人", 2, "技术部")
        org.nodes.append(new_node)
        org_with_messenger.update_org(org)
        mb = org_with_messenger.get_mailbox("node_new")
        assert mb is not None

    def test_update_org_preserves_existing(self, org_with_messenger: OrgMessenger):
        mb_before = org_with_messenger.get_mailbox("node_ceo")
        org = org_with_messenger._org
        org_with_messenger.update_org(org)
        mb_after = org_with_messenger.get_mailbox("node_ceo")
        assert mb_before is mb_after


class TestBackgroundTasks:
    async def test_start_and_stop(self, org_with_messenger: OrgMessenger):
        await org_with_messenger.start_background_tasks()
        assert org_with_messenger._deadlock_task is not None
        assert org_with_messenger._ttl_task is not None

        await org_with_messenger.stop_background_tasks()
        assert org_with_messenger._deadlock_task is None
        assert org_with_messenger._ttl_task is None

