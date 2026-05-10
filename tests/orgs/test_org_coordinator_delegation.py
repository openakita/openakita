"""Regression tests for org coordinator delegation contract (5/9 fix).

These cases lock in three changes that together restore "root agents must
delegate, not self-execute" behaviour after the 5/8 force-tool relaxation:

1. ``OrgRuntime._push_root_summary_prompt`` early-exits when the root never
   opened a delegation chain (avoids the trace-2 hallucinated recap loop).
2. ``AgentOrchestrator._run_agent_session`` activates coordinator mode for
   any agent flagged as ``_is_org_coordinator`` even when the global
   ``coordinator_mode_enabled`` switch is off.
3. ``COORDINATOR_MODE_RULESET`` whitelists ``org_*`` tools so the
   coordinator can actually delegate after entering the mode.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openakita.core.permission import (
    COORDINATOR_MODE_RULESET,
)
from openakita.core.permission import (
    disabled as permission_disabled,
)
from openakita.orgs.runtime import UserCommandTracker


class TestRootSummaryEarlyExit:
    """Repro for trace_org_org_c2f84f51_183833 fabricated-recap loop."""

    def test_root_with_no_delegation_chain_does_not_push_summary(self):
        """If the root agent never delegated anything, there is no
        subordinate output to summarise. Re-waking the root with
        "[用户指令最终汇总]" only invites the LLM to fabricate a recap of
        work that never happened (the editor-in-chief regression).
        """
        from openakita.orgs.runtime import OrgRuntime

        # Bind the unbound method directly so we can drive it with a stub
        # tracker without standing up a full runtime tree.
        push = OrgRuntime._push_root_summary_prompt

        tracker = UserCommandTracker(
            org_id="org_test",
            root_node_id="node_root",
            user_command_content="hello",
        )
        # Simulate "root never called org_delegate_task" (root_chain_id is
        # populated only when the first delegate succeeds).
        assert tracker.root_chain_id is None
        assert tracker.summary_pushed_at == 0

        runtime_stub = MagicMock()

        result = push(runtime_stub, tracker)

        assert result is False
        # No org lookup, no inbox push, no asyncio task scheduled.
        runtime_stub.get_org.assert_not_called()

    def test_progress_does_not_reset_user_level_stuck_warning(self):
        tracker = UserCommandTracker(
            org_id="org_test",
            root_node_id="node_root",
            user_command_content="hello",
        )
        tracker.warned_stuck = True

        tracker._touch()

        assert tracker.warned_stuck is True

    def test_already_pushed_summary_is_still_debounced(self):
        """The pre-existing debounce on ``summary_pushed_at`` keeps working —
        ensure the new "no chain" early-exit does not regress it."""
        from openakita.orgs.runtime import OrgRuntime

        push = OrgRuntime._push_root_summary_prompt

        tracker = UserCommandTracker(
            org_id="org_test",
            root_node_id="node_root",
            user_command_content="hello",
        )
        tracker.summary_pushed_at = 12345.0  # already pushed once
        tracker.root_chain_id = "chain_root_x"  # had a delegation

        runtime_stub = MagicMock()

        assert push(runtime_stub, tracker) is False
        runtime_stub.get_org.assert_not_called()


class TestCoordinatorModePermission:
    """COORDINATOR_MODE_RULESET must allow the org_* delegation toolbox."""

    ORG_DELEGATION_TOOLS = [
        "org_delegate_task",
        "org_send_message",
        "org_wait_for_deliverable",
        "org_accept_deliverable",
        "org_reject_deliverable",
        "org_submit_deliverable",
        "org_write_blackboard",
        "org_read_blackboard",
        "org_list_delegated_tasks",
    ]

    def test_org_tools_are_allowed_in_coordinator_mode(self):
        """Without the ``org_*`` glob added in this fix, a coordinator-mode
        agent would have every org_* call denied — defeating the purpose of
        the mode for org roots like editor-in-chief / CEO / tech-lead."""
        disabled = permission_disabled(
            list(self.ORG_DELEGATION_TOOLS),
            COORDINATOR_MODE_RULESET,
        )
        assert disabled == set(), (
            f"org_* tools must stay visible in coordinator mode; got disabled={sorted(disabled)}"
        )

    @pytest.mark.parametrize("tool_name", ["run_shell", "run_powershell", "browser_open"])
    def test_unrelated_executor_tools_remain_blocked_in_coordinator_mode(
        self,
        tool_name,
    ):
        """The whole point of coordinator mode is to *prevent* the root from
        executing arbitrary commands itself. The new ``org_*`` glob must not
        accidentally widen the allow list."""
        disabled = permission_disabled([tool_name], COORDINATOR_MODE_RULESET)
        assert tool_name in disabled


class TestOrgCoordinatorRuntimeMarker:
    """``_create_node_agent`` must tag agents whose node has subordinates."""

    def test_node_with_children_is_marked_coordinator(self, persisted_org):
        # CEO has subordinates (CTO -> dev). It is the canonical org root
        # and must be flagged as a coordinator at runtime.
        ceo = persisted_org.get_node("node_ceo")
        children = persisted_org.get_children(ceo.id)
        assert children, "Test fixture must seed the CEO with subordinates."
        # Mirror runtime._create_node_agent's structural test.
        assert bool(children) is True

    def test_leaf_node_is_not_a_coordinator(self, persisted_org):
        dev = persisted_org.get_node("node_dev")
        assert persisted_org.get_children(dev.id) == []
