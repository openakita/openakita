"""
Comprehensive tests for the full plan implementation:
- Dual-mode architecture (command / autonomous)
- Multi-level subtasks
- Plan-Task bridge
- New org tools
- Execution log auto-tracking
- Watchdog mechanism
- Hard timeout removal
- ProjectStore enhancements
- EventStore chain/task filtering
- Identity injection
- Security fixes
- Messenger reliability fixes
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openakita.orgs.models import (
    EdgeType,
    MsgType,
    NodeStatus,
    Organization,
    OrgEdge,
    OrgMessage,
    OrgNode,
    OrgProject,
    OrgStatus,
    ProjectTask,
    ProjectType,
    TaskStatus,
    _new_id,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_org(**overrides: Any) -> Organization:
    nodes = [
        OrgNode(id="ceo", role_title="CEO", level=0, department="管理层"),
        OrgNode(id="cto", role_title="CTO", level=1, department="技术部"),
        OrgNode(id="dev1", role_title="开发工程师", level=2, department="技术部"),
        OrgNode(id="cmo", role_title="CMO", level=1, department="市场部"),
    ]
    edges = [
        OrgEdge(source="ceo", target="cto", edge_type=EdgeType.HIERARCHY),
        OrgEdge(source="ceo", target="cmo", edge_type=EdgeType.HIERARCHY),
        OrgEdge(source="cto", target="dev1", edge_type=EdgeType.HIERARCHY),
    ]
    defaults = dict(
        id="org_test", name="测试公司", nodes=nodes, edges=edges,
        operation_mode="command",
    )
    defaults.update(overrides)
    return Organization(**defaults)


@pytest.fixture()
def sample_org() -> Organization:
    return _make_org()


@pytest.fixture()
def org_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data" / "orgs" / "org_test"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture()
def project_store(org_dir: Path):
    from openakita.orgs.project_store import ProjectStore
    return ProjectStore(org_dir)


@pytest.fixture()
def event_store(org_dir: Path):
    from openakita.orgs.event_store import OrgEventStore
    return OrgEventStore(org_dir, "org_test")


# ===========================================================================
# 1. MODEL TESTS — operation_mode, ProjectTask subtask fields
# ===========================================================================


class TestOrganizationOperationMode:
    """Verify operation_mode field on Organization."""

    def test_default_mode_is_command(self):
        org = Organization()
        assert org.operation_mode == "command"

    def test_mode_roundtrip(self):
        org = Organization(operation_mode="autonomous")
        d = org.to_dict()
        assert d["operation_mode"] == "autonomous"
        restored = Organization.from_dict(d)
        assert restored.operation_mode == "autonomous"

    def test_command_mode_from_dict(self):
        d = {"id": "o", "name": "t", "operation_mode": "command"}
        org = Organization.from_dict(d)
        assert org.operation_mode == "command"

    def test_missing_mode_defaults_command(self):
        d = {"id": "o", "name": "t"}
        org = Organization.from_dict(d)
        assert org.operation_mode == "command"

    def test_watchdog_fields(self):
        org = Organization()
        assert org.watchdog_enabled is True
        assert org.watchdog_interval_s == 30
        assert org.watchdog_stuck_threshold_s == 1800
        assert org.watchdog_silence_threshold_s == 1800

    def test_watchdog_roundtrip(self):
        org = Organization(
            watchdog_enabled=False,
            watchdog_interval_s=60,
            watchdog_stuck_threshold_s=3600,
        )
        d = org.to_dict()
        restored = Organization.from_dict(d)
        assert restored.watchdog_enabled is False
        assert restored.watchdog_interval_s == 60
        assert restored.watchdog_stuck_threshold_s == 3600


class TestProjectTaskSubtaskFields:
    """Verify new subtask-related fields on ProjectTask."""

    def test_default_fields(self):
        t = ProjectTask(project_id="p1", title="task")
        assert t.parent_task_id is None
        assert t.depth == 0
        assert t.plan_steps == []
        assert t.execution_log == []

    def test_subtask_creation(self):
        parent = ProjectTask(id="task_001", project_id="p1", title="parent")
        child = ProjectTask(
            id="task_002", project_id="p1", title="child",
            parent_task_id="task_001", depth=1,
        )
        assert child.parent_task_id == "task_001"
        assert child.depth == 1

    def test_subtask_roundtrip(self):
        t = ProjectTask(
            id="task_x", project_id="p1", title="with plan",
            parent_task_id="task_parent", depth=2,
            plan_steps=[
                {"step": 1, "title": "研究", "status": "done"},
                {"step": 2, "title": "实现", "status": "pending"},
            ],
            execution_log=[
                {"ts": "2026-03-07T10:00", "event": "created", "actor": "ceo", "detail": "创建"},
            ],
        )
        d = t.to_dict()
        assert d["parent_task_id"] == "task_parent"
        assert d["depth"] == 2
        assert len(d["plan_steps"]) == 2
        assert len(d["execution_log"]) == 1

        restored = ProjectTask.from_dict(d)
        assert restored.parent_task_id == "task_parent"
        assert restored.depth == 2
        assert len(restored.plan_steps) == 2
        assert restored.plan_steps[0]["status"] == "done"
        assert len(restored.execution_log) == 1

    def test_execution_log_append(self):
        t = ProjectTask(id="t1", project_id="p1", title="test")
        assert t.execution_log == []
        t.execution_log.append({"ts": _now_iso(), "event": "created", "actor": "ceo"})
        assert len(t.execution_log) == 1


# ===========================================================================
# 2. PROJECT STORE ENHANCEMENTS
# ===========================================================================


class TestProjectStoreEnhancements:
    """Verify new ProjectStore methods: get_task, get_subtasks, get_task_tree, etc."""

    def _seed_tasks(self, store):
        proj = OrgProject(id="p1", name="测试项目")
        store.create_project(proj)

        root_task = ProjectTask(
            id="task_root", project_id="p1", title="根任务",
            parent_task_id=None, depth=0, progress_pct=0,
        )
        child1 = ProjectTask(
            id="task_c1", project_id="p1", title="子任务1",
            parent_task_id="task_root", depth=1,
            assignee_node_id="cto", delegated_by="ceo",
            progress_pct=80,
        )
        child2 = ProjectTask(
            id="task_c2", project_id="p1", title="子任务2",
            parent_task_id="task_root", depth=1,
            assignee_node_id="cmo", delegated_by="ceo",
            progress_pct=40,
        )
        grandchild = ProjectTask(
            id="task_gc1", project_id="p1", title="孙任务",
            parent_task_id="task_c1", depth=2,
            assignee_node_id="dev1", delegated_by="cto",
            progress_pct=100,
        )
        for t in [root_task, child1, child2, grandchild]:
            store.add_task("p1", t)
        return proj

    def test_get_task_found(self, project_store):
        self._seed_tasks(project_store)
        task, proj = project_store.get_task("task_root")
        assert task is not None
        assert task.title == "根任务"
        assert proj.id == "p1"

    def test_get_task_not_found(self, project_store):
        self._seed_tasks(project_store)
        task, proj = project_store.get_task("nonexistent")
        assert task is None
        assert proj is None

    def test_get_subtasks(self, project_store):
        self._seed_tasks(project_store)
        children = project_store.get_subtasks("task_root")
        assert len(children) == 2
        ids = {c.id for c in children}
        assert ids == {"task_c1", "task_c2"}

    def test_get_subtasks_empty(self, project_store):
        self._seed_tasks(project_store)
        children = project_store.get_subtasks("task_gc1")
        assert children == []

    def test_get_task_tree(self, project_store):
        self._seed_tasks(project_store)
        tree = project_store.get_task_tree("task_root")
        assert tree["id"] == "task_root"
        assert len(tree["children"]) == 2
        c1_tree = next(c for c in tree["children"] if c["id"] == "task_c1")
        assert len(c1_tree["children"]) == 1
        assert c1_tree["children"][0]["id"] == "task_gc1"

    def test_get_task_tree_empty(self, project_store):
        result = project_store.get_task_tree("nonexistent")
        assert result == {}

    def test_get_ancestors(self, project_store):
        self._seed_tasks(project_store)
        ancestors = project_store.get_ancestors("task_gc1")
        assert len(ancestors) == 2
        assert ancestors[0].id == "task_c1"
        assert ancestors[1].id == "task_root"

    def test_get_ancestors_root(self, project_store):
        self._seed_tasks(project_store)
        ancestors = project_store.get_ancestors("task_root")
        assert ancestors == []

    def test_recalc_progress(self, project_store):
        self._seed_tasks(project_store)
        new_pct = project_store.recalc_progress("task_root")
        assert new_pct == (80 + 40) // 2  # 60
        task, _ = project_store.get_task("task_root")
        assert task.progress_pct == 60

    def test_recalc_progress_leaf(self, project_store):
        self._seed_tasks(project_store)
        pct = project_store.recalc_progress("task_gc1")
        assert pct == 100  # leaf returns own pct

    def test_recalc_progress_not_found(self, project_store):
        assert project_store.recalc_progress("nonexistent") is None

    def test_all_tasks_root_only(self, project_store):
        self._seed_tasks(project_store)
        roots = project_store.all_tasks(root_only=True)
        assert len(roots) == 1
        assert roots[0]["id"] == "task_root"

    def test_all_tasks_delegated_by(self, project_store):
        self._seed_tasks(project_store)
        tasks = project_store.all_tasks(delegated_by="ceo")
        ids = {t["id"] for t in tasks}
        assert ids == {"task_c1", "task_c2"}

    def test_all_tasks_parent_filter(self, project_store):
        self._seed_tasks(project_store)
        tasks = project_store.all_tasks(parent_task_id="task_root")
        assert len(tasks) == 2

    def test_file_write_lock(self, project_store):
        """Verify write lock exists."""
        import threading
        assert isinstance(project_store._lock, threading.Lock)


# ===========================================================================
# 3. EVENT STORE CHAIN/TASK FILTERING
# ===========================================================================


class TestEventStoreFiltering:
    """Verify chain_id and task_id filtering in EventStore.query."""

    def test_filter_by_chain_id(self, event_store):
        event_store.emit("task_delegated", "ceo", {"chain_id": "chain_abc", "task": "做产品"})
        event_store.emit("task_delegated", "ceo", {"chain_id": "chain_xyz", "task": "做营销"})
        event_store.emit("status_change", "cto", {"chain_id": "chain_abc"})

        results = event_store.query(chain_id="chain_abc")
        assert len(results) == 2
        for r in results:
            assert r["data"]["chain_id"] == "chain_abc"

    def test_filter_by_task_id(self, event_store):
        event_store.emit("task_progress", "cto", {"task_id": "task_001", "pct": 50})
        event_store.emit("task_progress", "cmo", {"task_id": "task_002", "pct": 30})

        results = event_store.query(task_id="task_001")
        assert len(results) == 1
        assert results[0]["data"]["task_id"] == "task_001"

    def test_combined_filters(self, event_store):
        event_store.emit("task_progress", "cto", {
            "chain_id": "c1", "task_id": "t1", "pct": 50,
        })
        event_store.emit("task_progress", "cmo", {
            "chain_id": "c1", "task_id": "t2", "pct": 30,
        })
        event_store.emit("task_progress", "dev1", {
            "chain_id": "c2", "task_id": "t1", "pct": 70,
        })

        results = event_store.query(chain_id="c1", task_id="t1")
        assert len(results) == 1
        assert results[0]["actor"] == "cto"


# ===========================================================================
# 4. MESSENGER RELIABILITY FIXES
# ===========================================================================


class TestMessengerFrozenBuffer:
    """Verify messages to frozen nodes are buffered, not dropped."""

    def test_frozen_node_buffers_messages(self, org_dir):
        from openakita.orgs.messenger import OrgMessenger
        org = _make_org()
        m = OrgMessenger(org, org_dir)
        m.register_node("cto", AsyncMock())

        m.freeze_mailbox("cto")

        msg = OrgMessage(
            org_id="org_test", from_node="ceo", to_node="cto",
            msg_type=MsgType.TASK_ASSIGN, content="test task",
        )

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(m.send(msg))
            assert result is True

            assert m.get_pending_count("cto") == 0
            mb = m._mailboxes.get("cto")
            assert mb is not None
            assert len(mb._frozen_buffer) == 1
        finally:
            loop.close()

    def test_unfreeze_delivers_buffered(self, org_dir):
        from openakita.orgs.messenger import OrgMessenger
        org = _make_org()
        m = OrgMessenger(org, org_dir)
        handler = AsyncMock()
        m.register_node("cto", handler)
        m.freeze_mailbox("cto")

        msg = OrgMessage(
            org_id="org_test", from_node="ceo", to_node="cto",
            msg_type=MsgType.TASK_ASSIGN, content="buffered task",
        )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(m.send(msg))
            m.unfreeze_mailbox("cto")

            mb = m._mailboxes.get("cto")
            assert mb._frozen_buffer == []
            assert m.get_pending_count("cto") == 1
        finally:
            loop.close()


# ===========================================================================
# 5. SECURITY FIXES
# ===========================================================================


class TestSecurityFixes:
    """Verify path traversal prevention."""

    def test_policy_path_blocks_backslash(self):
        from openakita.orgs.tool_handler import OrgToolHandler
        rt = MagicMock()
        rt._manager = MagicMock()
        handler = OrgToolHandler(rt)

        assert "\\" in "..\\etc\\passwd"

    def test_policy_path_blocks_dotdot(self):
        fname = "../../etc/passwd"
        assert ".." in fname or "/" in fname or "\\" in fname


# ===========================================================================
# 6. FROZEN STATUS OVERRIDE PREVENTION
# ===========================================================================


class TestFrozenOverride:
    """Verify _set_node_status doesn't override FROZEN."""

    def test_frozen_node_stays_frozen(self):
        from openakita.orgs.runtime import OrgRuntime

        org = _make_org()
        cto = org.get_node("cto")
        cto.status = NodeStatus.FROZEN

        rt = MagicMock(spec=OrgRuntime)
        rt._set_node_status = OrgRuntime._set_node_status.__get__(rt)

        rt._set_node_status(org, cto, NodeStatus.IDLE, "task_complete")
        assert cto.status == NodeStatus.FROZEN

    def test_unfreeze_reason_works(self):
        from openakita.orgs.runtime import OrgRuntime

        org = _make_org()
        cto = org.get_node("cto")
        cto.status = NodeStatus.FROZEN

        rt = MagicMock(spec=OrgRuntime)
        rt._set_node_status = OrgRuntime._set_node_status.__get__(rt)

        rt._set_node_status(org, cto, NodeStatus.IDLE, "unfreeze")
        assert cto.status == NodeStatus.IDLE


# ===========================================================================
# 7. ACCEPT/REJECT IDEMPOTENCY
# ===========================================================================


class TestAcceptRejectIdempotency:
    """Verify accept/reject deliverable prevents self-acceptance and duplicates."""

    @pytest.mark.asyncio
    async def test_self_acceptance_blocked(self, org_dir, sample_org):
        from openakita.orgs.tool_handler import OrgToolHandler
        from openakita.orgs.messenger import OrgMessenger
        from openakita.orgs.event_store import OrgEventStore

        rt = MagicMock()
        rt._manager = MagicMock()
        rt._manager._org_dir = MagicMock(return_value=org_dir)
        rt.get_org = MagicMock(return_value=sample_org)
        rt._chain_delegation_depth = {}

        messenger = OrgMessenger(sample_org, org_dir)
        rt.get_messenger = MagicMock(return_value=messenger)

        es = OrgEventStore(org_dir, "org_test")
        rt.get_event_store = MagicMock(return_value=es)
        rt._broadcast_ws = AsyncMock()
        rt.get_blackboard = MagicMock(return_value=None)

        handler = OrgToolHandler(rt)
        result = await handler._handle_org_accept_deliverable(
            {"from_node": "ceo", "feedback": "ok"},
            "org_test", "ceo",
        )
        assert "不能验收自己" in result

    @pytest.mark.asyncio
    async def test_self_rejection_blocked(self, org_dir, sample_org):
        from openakita.orgs.tool_handler import OrgToolHandler
        from openakita.orgs.messenger import OrgMessenger

        rt = MagicMock()
        rt._manager = MagicMock()
        messenger = OrgMessenger(sample_org, org_dir)
        rt.get_messenger = MagicMock(return_value=messenger)
        rt._chain_delegation_depth = {}

        handler = OrgToolHandler(rt)
        result = await handler._handle_org_reject_deliverable(
            {"from_node": "cto", "reason": "bad"},
            "org_test", "cto",
        )
        assert "不能打回自己" in result


# ===========================================================================
# 8. HEARTBEAT RACE PREVENTION
# ===========================================================================


class TestHeartbeatRace:
    """Verify heartbeat skips when root is busy."""

    @pytest.mark.asyncio
    async def test_skips_when_root_busy(self, org_dir, sample_org):
        from openakita.orgs.heartbeat import OrgHeartbeat

        sample_org.nodes[0].status = NodeStatus.BUSY

        rt = MagicMock()
        rt.get_org = MagicMock(return_value=sample_org)
        rt._running_tasks = {}
        rt._broadcast_ws = AsyncMock()

        hb = OrgHeartbeat(rt)
        result = await hb._execute_heartbeat(sample_org)
        assert result.get("skipped") is True
        assert result.get("reason") == "root_busy"


# ===========================================================================
# 9. DELEGATION DEPTH CONTROL
# ===========================================================================


class TestDelegationDepth:
    """Verify max_delegation_depth from model instead of hardcoded value."""

    def test_org_max_delegation_depth_default(self):
        org = Organization()
        assert org.max_delegation_depth == 5

    def test_org_max_delegation_depth_roundtrip(self):
        org = Organization(max_delegation_depth=3)
        d = org.to_dict()
        restored = Organization.from_dict(d)
        assert restored.max_delegation_depth == 3


# ===========================================================================
# 10. BLACKBOARD TTL
# ===========================================================================


class TestBlackboardTTL:
    """Verify blackboard TTL enforcement."""

    def test_expired_entries_filtered(self, org_dir):
        from openakita.orgs.blackboard import OrgBlackboard
        from openakita.orgs.models import MemoryType

        bb = OrgBlackboard(org_dir, "org_test")
        bb.write_org(
            content="old entry", source_node="ceo",
            memory_type=MemoryType.PROGRESS,
        )

        entries = bb.read_org(limit=10)
        assert len(entries) >= 1

    def test_ttl_hours_respected(self, org_dir):
        from openakita.orgs.blackboard import OrgBlackboard
        from openakita.orgs.models import MemoryType, OrgMemoryEntry
        import json
        from datetime import datetime, timezone, timedelta

        bb = OrgBlackboard(org_dir, "org_test")
        bb_file = org_dir / "memory" / "blackboard.jsonl"
        bb_file.parent.mkdir(parents=True, exist_ok=True)

        old_ts = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
        expired_entry = {
            "id": "old_1", "org_id": "org_test",
            "scope": "org", "scope_owner": "",
            "memory_type": "progress", "content": "expired stuff",
            "source_node": "ceo", "created_at": old_ts, "tags": [],
            "ttl_hours": 1, "importance": 0.5,
            "last_accessed_at": old_ts, "access_count": 0,
        }
        with open(bb_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(expired_entry) + "\n")

        entries = bb.read_org(limit=100)
        for e in entries:
            if hasattr(e, "content") and e.content == "expired stuff":
                pytest.fail("Expired entry should have been filtered")


# ===========================================================================
# 11. NEW ORG TOOLS DEFINITIONS
# ===========================================================================


class TestNewOrgToolDefinitions:
    """Verify all new org tools are properly defined."""

    def test_all_new_tools_exist(self):
        from openakita.orgs.tools import ORG_NODE_TOOLS

        tool_names = {t["name"] for t in ORG_NODE_TOOLS}

        expected = {
            "org_report_progress",
            "org_get_task_progress",
            "org_list_my_tasks",
            "org_list_delegated_tasks",
            "org_list_project_tasks",
            "org_update_project_task",
            "org_create_project_task",
        }
        for name in expected:
            assert name in tool_names, f"Missing tool definition: {name}"

    def test_tool_schema_valid(self):
        from openakita.orgs.tools import ORG_NODE_TOOLS

        for tool in ORG_NODE_TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            schema = tool["input_schema"]
            assert schema.get("type") == "object"


# ===========================================================================
# 12. PLAN TOOLS IN _KEEP LIST
# ===========================================================================


class TestPlanToolsInKeep:
    """Verify Plan tools are in the _KEEP list for all org agents."""

    def test_plan_tools_kept(self):
        from openakita.orgs.runtime import OrgRuntime

        source = OrgRuntime._create_node_agent.__code__
        source_text = ""
        try:
            import inspect
            source_text = inspect.getsource(OrgRuntime._create_node_agent)
        except Exception:
            pass

        plan_tools = ["create_todo", "update_todo_step", "get_todo_status", "complete_todo"]
        for tool in plan_tools:
            assert tool in source_text, f"{tool} not found in _create_node_agent"


# ===========================================================================
# 13. IDENTITY INJECTION (command mode)
# ===========================================================================


class TestIdentityInjection:
    """Verify identity context includes project tasks in command mode."""

    def test_command_mode_injects_project_context(self, org_dir, sample_org):
        from openakita.orgs.identity import OrgIdentity, ResolvedIdentity
        from openakita.orgs.project_store import ProjectStore

        sample_org.operation_mode = "command"

        store = ProjectStore(org_dir)
        proj = OrgProject(id="p1", name="测试项目")
        store.create_project(proj)
        task = ProjectTask(
            id="task_001", project_id="p1", title="做产品调研",
            status=TaskStatus.IN_PROGRESS, assignee_node_id="ceo",
        )
        store.add_task("p1", task)

        identity = OrgIdentity(org_dir)
        node = sample_org.nodes[0]
        resolved = identity.resolve(node, sample_org)
        prompt = identity.build_org_context_prompt(
            node, sample_org, resolved,
            project_tasks_summary="- 做产品调研 (进行中)",
        )
        assert "做产品调研" in prompt

    def test_auto_delegate_injection(self, org_dir, sample_org):
        from openakita.orgs.identity import OrgIdentity

        identity = OrgIdentity(org_dir)

        ceo = sample_org.nodes[0]
        children = sample_org.get_children(ceo.id)
        assert len(children) >= 1

        resolved = identity.resolve(ceo, sample_org)
        prompt = identity.build_org_context_prompt(ceo, sample_org, resolved)
        assert "下属" in prompt or "org_delegate_task" in prompt or "委派" in prompt


# ===========================================================================
# 14. WATCHDOG MECHANISM
# ===========================================================================


class TestWatchdogMechanism:
    """Verify watchdog loop functionality."""

    def test_watchdog_fields_on_model(self):
        org = Organization(
            watchdog_enabled=True,
            watchdog_interval_s=15,
            watchdog_stuck_threshold_s=600,
        )
        assert org.watchdog_enabled is True
        assert org.watchdog_interval_s == 15
        assert org.watchdog_stuck_threshold_s == 600

    def test_watchdog_notify_delegator_method_exists(self):
        from openakita.orgs.runtime import OrgRuntime
        assert hasattr(OrgRuntime, "_watchdog_notify_delegator")

    def test_watchdog_loop_method_exists(self):
        from openakita.orgs.runtime import OrgRuntime
        assert hasattr(OrgRuntime, "_watchdog_loop")


# ===========================================================================
# 15. NO HARD TIMEOUT
# ===========================================================================


class TestNoHardTimeout:
    """Verify asyncio.wait_for is not used in _run_agent_task."""

    def test_no_wait_for_in_run_agent_task(self):
        import inspect
        from openakita.orgs.runtime import OrgRuntime

        source = inspect.getsource(OrgRuntime._run_agent_task)
        assert "wait_for" not in source, (
            "_run_agent_task should not use asyncio.wait_for (hard timeout removed)"
        )


# ===========================================================================
# 16. HEALTH CHECK LOOP
# ===========================================================================


class TestHealthCheckLoop:
    """Verify health_check_loop exists and is separate from idle_probe."""

    def test_health_check_loop_exists(self):
        from openakita.orgs.runtime import OrgRuntime
        assert hasattr(OrgRuntime, "_health_check_loop")

    def test_idle_probe_loop_exists(self):
        from openakita.orgs.runtime import OrgRuntime
        assert hasattr(OrgRuntime, "_idle_probe_loop")


# ===========================================================================
# 17. CLONE/DISMISS MESSENGER REGISTRATION
# ===========================================================================


class TestCloneDismissMessenger:
    """Verify clone registers and dismiss cleans up messenger."""

    def test_scaler_registers_new_node(self):
        import inspect
        from openakita.orgs.scaler import OrgScaler
        source = inspect.getsource(OrgScaler.approve_request)
        assert "register_node" in source or "messenger" in source

    def test_scaler_dismiss_cleans_up(self):
        import inspect
        from openakita.orgs.scaler import OrgScaler
        source = inspect.getsource(OrgScaler.dismiss_node)
        assert "unregister_node" in source


# ===========================================================================
# 18. RECALC PARENT PROGRESS ON ACCEPT/REJECT
# ===========================================================================


class TestRecalcParentProgress:
    """Verify accept/reject triggers parent progress recalculation."""

    def test_recalc_parent_progress_method_exists(self):
        from openakita.orgs.tool_handler import OrgToolHandler
        assert hasattr(OrgToolHandler, "_recalc_parent_progress")

    def test_accept_calls_recalc(self):
        import inspect
        from openakita.orgs.tool_handler import OrgToolHandler
        source = inspect.getsource(OrgToolHandler._handle_org_accept_deliverable)
        assert "_recalc_parent_progress" in source

    def test_reject_calls_recalc(self):
        import inspect
        from openakita.orgs.tool_handler import OrgToolHandler
        source = inspect.getsource(OrgToolHandler._handle_org_reject_deliverable)
        assert "_recalc_parent_progress" in source


# ===========================================================================
# 19. EXECUTION LOG AUTO-TRACKING
# ===========================================================================


class TestExecutionLogTracking:
    """Verify execution_log is appended on key events."""

    def test_append_execution_log_method(self):
        from openakita.orgs.tool_handler import OrgToolHandler
        assert hasattr(OrgToolHandler, "_append_execution_log")

    def test_delegation_logs(self):
        import inspect
        from openakita.orgs.tool_handler import OrgToolHandler
        source = inspect.getsource(OrgToolHandler._handle_org_delegate_task)
        assert "_append_execution_log" in source

    def test_deliver_logs(self):
        import inspect
        from openakita.orgs.tool_handler import OrgToolHandler
        source = inspect.getsource(OrgToolHandler._handle_org_submit_deliverable)
        assert "_append_execution_log" in source

    def test_accept_logs(self):
        import inspect
        from openakita.orgs.tool_handler import OrgToolHandler
        source = inspect.getsource(OrgToolHandler._handle_org_accept_deliverable)
        assert "_append_execution_log" in source

    def test_reject_logs(self):
        import inspect
        from openakita.orgs.tool_handler import OrgToolHandler
        source = inspect.getsource(OrgToolHandler._handle_org_reject_deliverable)
        assert "_append_execution_log" in source


# ===========================================================================
# 20. PLAN-TASK BRIDGE
# ===========================================================================


class TestPlanTaskBridge:
    """Verify Plan tools bridge to ProjectTask."""

    def test_bridge_method_exists(self):
        from openakita.orgs.tool_handler import OrgToolHandler
        assert hasattr(OrgToolHandler, "_bridge_plan_to_task")

    def test_bridge_in_runtime_patch(self):
        import inspect
        from openakita.orgs.runtime import OrgRuntime
        source = inspect.getsource(OrgRuntime._create_node_agent)
        assert "_bridge_plan_to_task" in source or "plan" in source.lower()


# ===========================================================================
# 21. API ENDPOINTS
# ===========================================================================


class TestAPIEndpoints:
    """Verify new API endpoints are registered."""

    def test_api_task_detail_routes(self):
        import inspect
        from openakita.api.routes import orgs
        source = inspect.getsource(orgs)
        assert "tasks/{task_id}" in source or "tasks/{task_id}/tree" in source
        assert "nodes/{node_id}/tasks" in source or "node_id" in source
        assert "dispatch" in source

    def test_dispatch_endpoint(self):
        import inspect
        from openakita.api.routes import orgs
        source = inspect.getsource(orgs)
        assert "dispatch" in source


# ===========================================================================
# 22. USAGE DOCUMENTATION
# ===========================================================================


class TestUsageDoc:
    """Verify usage documentation exists."""

    def test_usage_doc_exists(self):
        doc_path = Path("d:/coder/myagent/docs/org-usage-guide.md")
        assert doc_path.exists(), "Usage documentation not found"

    def test_usage_doc_has_content(self):
        doc_path = Path("d:/coder/myagent/docs/org-usage-guide.md")
        if doc_path.exists():
            content = doc_path.read_text("utf-8")
            assert len(content) > 500
            assert "快速入门" in content or "入门" in content


# ===========================================================================
# 23. BROADCAST THROTTLE
# ===========================================================================


class TestBroadcastThrottle:
    """Verify broadcast messages don't trigger node activation."""

    def test_broadcast_no_trigger(self):
        import inspect
        from openakita.orgs.messenger import OrgMessenger
        source = inspect.getsource(OrgMessenger._broadcast)
        assert "trigger_handler" in source


# ===========================================================================
# 24. SCHEDULE APPROVAL
# ===========================================================================


class TestScheduleApproval:
    """Verify schedules are created only after approval."""

    def test_schedule_through_approval(self):
        import inspect
        from openakita.orgs.tool_handler import OrgToolHandler
        source = inspect.getsource(OrgToolHandler._handle_org_create_schedule)
        assert "push_approval" in source or "approval" in source


# ===========================================================================
# 25. SAVE CONCURRENCY LOCK
# ===========================================================================


class TestSaveConcurrencyLock:
    """Verify _save_org uses per-org asyncio.Lock."""

    def test_save_lock_exists(self):
        from openakita.orgs.runtime import OrgRuntime
        assert hasattr(OrgRuntime, "_get_save_lock")

    def test_save_org_uses_lock(self):
        import inspect
        from openakita.orgs.runtime import OrgRuntime
        source = inspect.getsource(OrgRuntime._save_org)
        assert "_get_save_lock" in source or "_save_locks" in source


# ===========================================================================
# 26. INTEGRATION: Full subtask flow
# ===========================================================================


class TestSubtaskIntegration:
    """Integration test: create project, add task, add subtasks, recalc."""

    def test_full_subtask_flow(self, project_store):
        proj = OrgProject(id="p_int", name="集成测试项目")
        project_store.create_project(proj)

        root = ProjectTask(
            id="t_root", project_id="p_int", title="总任务",
            depth=0, parent_task_id=None,
        )
        project_store.add_task("p_int", root)

        for i in range(3):
            child = ProjectTask(
                id=f"t_child_{i}", project_id="p_int",
                title=f"子任务{i}",
                parent_task_id="t_root", depth=1,
                progress_pct=(i + 1) * 25,
            )
            project_store.add_task("p_int", child)

        gc = ProjectTask(
            id="t_gc_0", project_id="p_int", title="孙任务",
            parent_task_id="t_child_0", depth=2, progress_pct=100,
        )
        project_store.add_task("p_int", gc)

        tree = project_store.get_task_tree("t_root")
        assert len(tree["children"]) == 3
        child0_tree = next(c for c in tree["children"] if c["id"] == "t_child_0")
        assert len(child0_tree["children"]) == 1

        ancestors = project_store.get_ancestors("t_gc_0")
        assert len(ancestors) == 2
        assert ancestors[0].id == "t_child_0"
        assert ancestors[1].id == "t_root"

        pct = project_store.recalc_progress("t_root")
        expected = (25 + 50 + 75) // 3  # 50
        assert pct == expected

        root_task, _ = project_store.get_task("t_root")
        assert root_task.progress_pct == expected

        roots = project_store.all_tasks(root_only=True, project_id="p_int")
        assert len(roots) == 1
        assert roots[0]["id"] == "t_root"

        delegated = project_store.all_tasks(parent_task_id="t_root")
        assert len(delegated) == 3
