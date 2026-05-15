"""Unit tests for the deadlock-early-stop watchdog path and the
``command_tracker`` module split.

Covers the P0-2 fix: when every node is IDLE, every mailbox is empty, and the
root node is IDLE, but the tracker still has open chains, the watchdog should
stop the command after ``org_command_deadlock_grace_secs`` instead of waiting
for the much longer ``org_command_stuck_autostop_secs`` backstop.

Also smoke-tests :class:`UserCommandTracker` after it was lifted into
``openakita.orgs.command_tracker`` to make sure the re-export from
``openakita.orgs.runtime`` still works for external callers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.command_tracker import (
    UserCommandTracker as TrackerFromModule,
)
from openakita.orgs.models import NodeStatus
from openakita.orgs.runtime import OrgRuntime, UserCommandTracker


class TestUserCommandTrackerModuleSplit:
    """The class lives in :mod:`openakita.orgs.command_tracker` now but the
    runtime re-export must keep external imports stable.
    """

    def test_runtime_reexport_is_same_class(self) -> None:
        assert UserCommandTracker is TrackerFromModule

    def test_new_tracker_initial_fields(self) -> None:
        t = UserCommandTracker("org_a", "root", command_id="cmd_x")
        assert t.org_id == "org_a"
        assert t.root_node_id == "root"
        assert t.command_id == "cmd_x"
        assert t.deadlock_stopped is False
        assert t._quiet_deadlock_since == 0.0
        assert t.completed.is_set() is False


class TestQuietDeadlockDetection:
    """``_is_tracker_quiet_deadlock`` must only fire on the silent-deadlock
    shape, not on any other "running" snapshot.
    """

    def _runtime_with_blockers(self, blockers: dict) -> OrgRuntime:
        rt = OrgRuntime(manager=MagicMock())
        rt._collect_tracker_blockers = MagicMock(return_value=blockers)
        return rt

    def _tracker(self) -> UserCommandTracker:
        t = UserCommandTracker("org_a", "root", command_id="cmd")
        t.register_chain("chain_root")
        return t

    def test_fires_when_everyone_idle_but_chain_open(self) -> None:
        rt = self._runtime_with_blockers({
            "open_subtree_chains": ["chain_root"],
            "busy_nodes": [],
            "pending_mailbox": [],
            "root_status": NodeStatus.IDLE.value,
        })
        assert rt._is_tracker_quiet_deadlock(self._tracker()) is True

    def test_quiet_but_no_open_chain_does_not_fire(self) -> None:
        rt = self._runtime_with_blockers({
            "open_subtree_chains": [],
            "busy_nodes": [],
            "pending_mailbox": [],
            "root_status": NodeStatus.IDLE.value,
        })
        assert rt._is_tracker_quiet_deadlock(self._tracker()) is False

    def test_busy_nodes_block_detection(self) -> None:
        rt = self._runtime_with_blockers({
            "open_subtree_chains": ["chain_root"],
            "busy_nodes": [{"node_id": "n1", "role_title": "x", "status": "busy"}],
            "pending_mailbox": [],
            "root_status": NodeStatus.IDLE.value,
        })
        assert rt._is_tracker_quiet_deadlock(self._tracker()) is False

    def test_pending_mailbox_blocks_detection(self) -> None:
        rt = self._runtime_with_blockers({
            "open_subtree_chains": ["chain_root"],
            "busy_nodes": [],
            "pending_mailbox": [{"node_id": "n1", "pending": 1}],
            "root_status": NodeStatus.IDLE.value,
        })
        assert rt._is_tracker_quiet_deadlock(self._tracker()) is False

    def test_root_busy_blocks_detection(self) -> None:
        rt = self._runtime_with_blockers({
            "open_subtree_chains": ["chain_root"],
            "busy_nodes": [],
            "pending_mailbox": [],
            "root_status": NodeStatus.BUSY.value,
        })
        assert rt._is_tracker_quiet_deadlock(self._tracker()) is False

    def test_awaiting_summary_state_is_excluded(self) -> None:
        # When the post-summary ReAct is in flight, the root will swing to
        # BUSY shortly. Don't trip the deadlock path during that window.
        rt = self._runtime_with_blockers({
            "open_subtree_chains": ["chain_root"],
            "busy_nodes": [],
            "pending_mailbox": [],
            "root_status": NodeStatus.IDLE.value,
        })
        t = self._tracker()
        t.state = "awaiting_summary"
        assert rt._is_tracker_quiet_deadlock(t) is False


class TestTriggerDeadlockStop:
    @pytest.mark.asyncio
    async def test_marks_tracker_and_emits_events(self) -> None:
        rt = OrgRuntime(manager=MagicMock())
        rt._soft_stop_org = AsyncMock()
        rt._broadcast_ws = AsyncMock()
        event_store = MagicMock()
        rt.get_event_store = MagicMock(return_value=event_store)

        tracker = UserCommandTracker("org_a", "root", command_id="cmd_dead")
        tracker.register_chain("chain_root")

        await rt._trigger_deadlock_stop(tracker, quiet_secs=120)

        assert tracker.completed.is_set() is True
        assert tracker.auto_stopped is True
        assert tracker.deadlock_stopped is True
        rt._soft_stop_org.assert_awaited_once_with("org_a")
        rt._broadcast_ws.assert_awaited_once()
        ws_event, ws_payload = rt._broadcast_ws.await_args.args
        assert ws_event == "org:command_deadlock_stopped"
        assert ws_payload["command_id"] == "cmd_dead"
        assert ws_payload["quiet_secs"] == 120
        # event store record (used by /api/orgs/.../events forensic replay)
        event_store.emit.assert_called_once()
        emit_args = event_store.emit.call_args.args
        assert emit_args[0] == "command_deadlock_stopped"
        assert emit_args[1] == "root"

    @pytest.mark.asyncio
    async def test_soft_stop_failure_still_completes_tracker(self) -> None:
        rt = OrgRuntime(manager=MagicMock())
        rt._soft_stop_org = AsyncMock(side_effect=RuntimeError("boom"))
        rt._broadcast_ws = AsyncMock()
        rt.get_event_store = MagicMock(return_value=MagicMock())

        tracker = UserCommandTracker("org_a", "root", command_id="cmd_dead")
        tracker.register_chain("chain_root")

        # Even when soft_stop blows up, the tracker MUST end so send_command
        # unblocks and the user does not hang forever on the indicator.
        await rt._trigger_deadlock_stop(tracker, quiet_secs=99)

        assert tracker.completed.is_set() is True
        assert tracker.auto_stopped is True
        assert tracker.deadlock_stopped is True


class TestPluginAssetHelperReExport:
    """The static helpers were moved into :mod:`plugin_assets`; the
    OrgRuntime methods are now thin wrappers but must still behave the same.
    """

    def test_safe_asset_filename_strips_traversal(self) -> None:
        rt = OrgRuntime(manager=MagicMock())
        out = rt._safe_asset_filename("../../etc/passwd")
        assert "/" not in out and "\\" not in out
        assert out == "passwd"

    def test_safe_asset_filename_caps_length(self) -> None:
        rt = OrgRuntime(manager=MagicMock())
        out = rt._safe_asset_filename("a" * 500 + ".png")
        assert len(out) <= 120
        assert out.endswith(".png")

    def test_ext_for_url_picks_extension(self) -> None:
        rt = OrgRuntime(manager=MagicMock())
        assert rt._ext_for_url("https://x.test/foo.MP4?token=abc") == ".mp4"

    def test_ext_for_url_falls_back(self) -> None:
        rt = OrgRuntime(manager=MagicMock())
        assert rt._ext_for_url("https://x.test/no-ext") == ".bin"
        assert rt._ext_for_url("https://x.test/no-ext", fallback=".png") == ".png"
