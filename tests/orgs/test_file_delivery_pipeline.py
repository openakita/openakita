"""Tests for the consolidated file-delivery pipeline.

Covers four delivery legs that must all funnel into the single
``OrgRuntime._register_file_output`` entry:

1. ``write_file`` hook (parameter aliases: path / filename / filepath /
   file_path all end up registered against the same file).
2. ``deliver_artifacts`` hook (receipts get turned into blackboard
   attachments + ProjectTask links).
3. ``org_submit_deliverable`` with ``file_attachments`` — files produced
   by ``run_shell`` make it onto the deliverable even though run_shell
   doesn't trigger the file-output hook directly.
4. ``org_accept_deliverable`` returns JSON receipts (status ==
   "relayed"), so TaskVerify can see the implicit delivery.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import json
import pytest

from openakita.orgs.tool_handler import OrgToolHandler


# ---------------------------------------------------------------------------
# _record_file_output (thin wrapper) — parameter aliases must all reach
# _register_file_output with the same canonical file_path.
# ---------------------------------------------------------------------------


class TestRecordFileOutputAliases:
    def _mk_runtime_capturing_register(self):
        from openakita.orgs import runtime as runtime_module

        rt = runtime_module.OrgRuntime.__new__(runtime_module.OrgRuntime)
        rt.get_current_chain_id = MagicMock(return_value="chain-1")
        captured: list[dict] = []

        def fake_register(
            org_id, node_id, *, chain_id, filename, file_path, workspace=None,
        ):
            captured.append({
                "org_id": org_id, "node_id": node_id,
                "chain_id": chain_id, "filename": filename,
                "file_path": file_path, "workspace": workspace,
            })
            return {
                "filename": filename or Path(file_path).name,
                "file_path": file_path,
                "file_size": 0,
            }

        rt._register_file_output = fake_register
        return rt, captured

    @pytest.mark.parametrize(
        "alias_key", ["path", "filepath", "file_path", "filename"],
    )
    def test_write_file_aliases_all_reach_register(self, tmp_path, alias_key):
        rt, captured = self._mk_runtime_capturing_register()
        target = tmp_path / "doc.md"
        target.write_text("hello", encoding="utf-8")

        rt._record_file_output(
            org_id="org_x", node_id="node_y",
            tool_name="write_file",
            tool_input={alias_key: str(target), "content": "hello"},
            result="✅ wrote",
            workspace=tmp_path,
        )
        assert len(captured) == 1
        assert captured[0]["file_path"] == str(target)

    def test_write_file_failure_result_is_skipped(self, tmp_path):
        rt, captured = self._mk_runtime_capturing_register()
        rt._record_file_output(
            org_id="org_x", node_id="node_y",
            tool_name="write_file",
            tool_input={"path": str(tmp_path / "nope.md")},
            result="❌ something went wrong",
            workspace=tmp_path,
        )
        assert captured == []

    def test_deliver_artifacts_receipts_registered(self, tmp_path):
        rt, captured = self._mk_runtime_capturing_register()
        f1 = tmp_path / "a.md"; f1.write_text("a", encoding="utf-8")
        f2 = tmp_path / "b.md"; f2.write_text("b", encoding="utf-8")
        payload = {
            "receipts": [
                {"status": "delivered", "name": "a.md", "path": str(f1)},
                {"status": "delivered", "name": "b.md", "path": str(f2)},
                # Non-"delivered" receipts must be ignored.
                {"status": "skipped", "name": "c.md", "path": str(tmp_path / "c.md")},
            ],
        }
        rt._record_file_output(
            org_id="org_x", node_id="node_y",
            tool_name="deliver_artifacts",
            tool_input={},
            result=json.dumps(payload) + "\n\n[执行日志] noise",
            workspace=tmp_path,
        )
        assert {c["file_path"] for c in captured} == {str(f1), str(f2)}

    def test_deliver_artifacts_malformed_json_safe(self, tmp_path):
        rt, captured = self._mk_runtime_capturing_register()
        rt._record_file_output(
            org_id="org_x", node_id="node_y",
            tool_name="deliver_artifacts",
            tool_input={},
            result="not json at all",
            workspace=tmp_path,
        )
        assert captured == []


# ---------------------------------------------------------------------------
# org_submit_deliverable — file_attachments must be funneled through the
# same _register_file_output entry, and surviving attachments must end up
# in the TASK_DELIVERED metadata.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSubmitDeliverableAttachments:
    async def test_declared_files_get_registered_and_forwarded(
        self, tmp_path, persisted_org, org_dir, mock_runtime,
    ):
        # Add parent edge so we have a "上级" to submit to
        from openakita.orgs.models import EdgeType, OrgEdge
        persisted_org.edges.append(
            OrgEdge(source="node_cto", target="node_dev", edge_type=EdgeType.HIERARCHY),
        )

        f = tmp_path / "plan.md"
        f.write_text("# plan", encoding="utf-8")

        register_calls: list[dict] = []

        def fake_register(
            org_id, node_id, *, chain_id, filename, file_path, workspace=None,
        ):
            register_calls.append({
                "chain_id": chain_id,
                "filename": filename,
                "file_path": file_path,
            })
            return {
                "filename": filename or Path(file_path).name,
                "file_path": file_path,
                "file_size": 1,
            }

        mock_runtime._register_file_output = fake_register
        mock_runtime._resolve_org_workspace = MagicMock(return_value=tmp_path)

        handler = OrgToolHandler(mock_runtime)
        result = await handler.handle(
            "org_submit_deliverable",
            {
                "to_node": "node_cto",
                "task_chain_id": "chain-abc",
                "deliverable": "计划写完了",
                "summary": "done",
                "file_attachments": [
                    {"filename": "plan.md", "file_path": str(f)},
                ],
            },
            persisted_org.id, "node_dev",
        )
        assert "已提交" in result
        # registered through canonical entry exactly once per attachment
        assert len(register_calls) == 1
        assert register_calls[0]["file_path"] == str(f)

        # The outgoing message must carry file_attachments in metadata so
        # the parent can merge them into its own ProjectTask.
        messenger = mock_runtime.get_messenger(persisted_org.id)
        pending = list(messenger._pending_messages.values())
        assert pending, "expected at least one queued message"
        sent = pending[-1]
        meta = getattr(sent, "metadata", {}) or {}
        assert "file_attachments" in meta
        assert meta["file_attachments"][0]["file_path"] == str(f)

    async def test_missing_file_attachments_skipped_gracefully(
        self, tmp_path, persisted_org, mock_runtime,
    ):
        mock_runtime._register_file_output = MagicMock(return_value=None)
        mock_runtime._resolve_org_workspace = MagicMock(return_value=tmp_path)

        handler = OrgToolHandler(mock_runtime)
        result = await handler.handle(
            "org_submit_deliverable",
            {
                "to_node": "node_cto",
                "task_chain_id": "chain-xyz",
                "deliverable": "文字交付",
                "file_attachments": [
                    {"filename": "ghost.md", "file_path": str(tmp_path / "does_not_exist.md")},
                ],
            },
            persisted_org.id, "node_dev",
        )
        # Missing file ⇒ register returns None ⇒ not added to metadata,
        # but submit still succeeds (deliverable text is the primary signal).
        assert "已提交" in result


# ---------------------------------------------------------------------------
# org_accept_deliverable — must return structured JSON receipts so
# reasoning_engine / TaskVerify can count it as a relayed delivery.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAcceptReturnsReceipts:
    async def test_accept_returns_json_with_receipts_when_child_has_files(
        self, tmp_path, persisted_org, org_dir, mock_runtime,
    ):
        # Seed a child ProjectTask with file_attachments linked to the chain.
        from openakita.orgs.project_store import ProjectStore
        from openakita.orgs.models import OrgProject, ProjectTask, TaskStatus

        store = ProjectStore(org_dir)
        project = store.create_project(OrgProject(
            id="proj_1", org_id=persisted_org.id,
            name="p", description="",
        ))
        # Parent carries its own chain id (the one it was delegated under);
        # the child uses the chain id we're accepting, distinct from parent.
        parent_task = store.add_task(project.id, ProjectTask(
            id="task_parent", project_id=project.id,
            title="parent", description="", assignee_node_id="node_cto",
            status=TaskStatus.IN_PROGRESS, chain_id="chain-parent",
        ))
        store.add_task(project.id, ProjectTask(
            id="task_child", project_id=project.id,
            title="child", description="",
            assignee_node_id="node_dev", status=TaskStatus.DELIVERED,
            chain_id="chain-ok", parent_task_id=parent_task.id,
            file_attachments=[
                {"filename": "plan.md", "file_path": str(tmp_path / "plan.md"), "file_size": 123},
            ],
        ))

        # Sanity-check the fixture: the child task really is on disk with
        # file_attachments before we hit the handler (guards against subtle
        # fixture regressions rather than handler regressions).
        sanity = ProjectStore(org_dir).find_task_by_chain("chain-ok")
        assert sanity is not None, "child task missing — fixture is broken"
        assert sanity.file_attachments, "child file_attachments empty on disk"

        handler = OrgToolHandler(mock_runtime)
        raw = await handler.handle(
            "org_accept_deliverable",
            {
                "from_node": "node_dev",
                "task_chain_id": "chain-ok",
                "feedback": "good",
            },
            persisted_org.id, "node_cto",
        )
        payload = json.loads(raw)
        assert payload["ok"] is True
        assert payload["accepted_from"] == "node_dev"
        assert payload["chain_id"] == "chain-ok"
        assert isinstance(payload["receipts"], list)
        assert payload["receipts"], (
            "expected at least one relayed receipt, got: " + raw
        )
        r0 = payload["receipts"][0]
        assert r0["status"] == "relayed"
        assert r0["source_node"] == "node_dev"
        assert r0["filename"] == "plan.md"

    async def test_accept_returns_empty_receipts_when_no_child_files(
        self, persisted_org, org_dir, mock_runtime,
    ):
        handler = OrgToolHandler(mock_runtime)
        raw = await handler.handle(
            "org_accept_deliverable",
            {
                "from_node": "node_dev",
                "task_chain_id": "chain-empty",
                "feedback": "good",
            },
            persisted_org.id, "node_cto",
        )
        payload = json.loads(raw)
        assert payload["ok"] is True
        assert payload["receipts"] == []
