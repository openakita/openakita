"""回归测试：CEO/CPO/PM 多层派工链路 + deliverable 自动附件 + identity prompt
精确节点 id 引导。

覆盖三组本地修复：

  Bug A — task_affinity 不再静默把 ``to_node`` 改写成调用者自己。
    旧实现里，CEO->CPO 派工后 ``affinity[chain_X] = CPO``；CPO 拿同一个
    chain（无论是 LLM 显式传入还是 legacy 模式复用）继续派给 PM 时，
    ``to_node="pm"`` 会被覆盖成 ``cpo``，紧接着撞上"不能委派给自己"
    的硬错误，PM 永远收不到任务。
    修复后只在 ``existing_affinity`` 与 ``to_node`` 同属一个 clone 组
    时才改写，正常上下游路径完全不受影响。

  Bug B — ``org_submit_deliverable`` 在 caller 没显式传 ``file_attachments``
    且 deliverable 看起来是结构化文档（含 markdown 标题/列表/代码块）
    且字符数 ≥ 阈值时，自动落盘到 ``<workspace>/deliverables/`` 并通过
    ``_register_file_output`` 这个唯一登记入口写入黑板/任务，前端就能
    点附件下载。短聊天回复、纯文本汇报等不会触发，已传附件的也不会
    重复落盘。

  Bug C — ``OrgIdentity.build_org_context_prompt`` 不再在 system prompt
    里出现误导性的 ``node_xxxxxxxx`` 字面占位符；改为引用调用者自己
    的真实 id 作为示例，告诉 LLM "看下面组织结构里反引号包住的精确 id"。
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openakita.config import settings
from openakita.orgs.identity import OrgIdentity, ResolvedIdentity
from openakita.orgs.tool_handler import OrgToolHandler

from .conftest import make_node, make_org, make_edge


# ---------------------------------------------------------------------------
# Shared fixtures specific to these regression tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_runtime_full(mock_runtime):
    """Same shape as ``mock_runtime_full`` in test_org_orchestration_fix.py.

    Defined locally so this file is self-contained and the tests can be
    moved/run independently.
    """
    mock_runtime._chain_parent = {}
    mock_runtime._chain_events = OrderedDict()
    mock_runtime._max_chain_events = 256
    mock_runtime._node_inbox_events = {}
    mock_runtime._closed_chains = {}
    mock_runtime._touch_trackers_for_org = MagicMock()
    return mock_runtime


def _last_task_message(messenger):
    """Return the most recent TASK_ASSIGN message routed through messenger."""
    pending = list(messenger._pending_messages.values())
    assert pending, "expected at least one queued message"
    return pending[-1]


# ===========================================================================
# Bug A — task_affinity must NOT silently rewrite to_node onto caller itself
# ===========================================================================


class TestAffinityNoSelfRewrite:
    """Regression for the CEO->CPO->PM bug.

    Reproduces the user log scenario at the unit-test level: pre-bind affinity
    for ``chain_X`` to the caller node and assert that delegate keeps the
    original LLM ``to_node`` instead of falling back to ``existing_affinity``
    (which would self-loop).
    """

    @pytest.mark.asyncio
    async def test_explicit_chain_id_does_not_self_redirect_to_caller(
        self, mock_runtime_full, persisted_org,
    ):
        # Mimic the bug log: a previous delegate already bound affinity[X]=cto.
        messenger = mock_runtime_full.get_messenger(persisted_org.id)
        messenger.bind_task_affinity("chain_X", "node_cto")

        handler = OrgToolHandler(mock_runtime_full)
        # CTO calls delegate(to_node=node_dev) reusing chain_X explicitly.
        result = await handler._handle_org_delegate_task(
            {
                "to_node": "node_dev",
                "task": "实现登录",
                "task_chain_id": "chain_X",
            },
            persisted_org.id, "node_cto",
        )

        assert "任务已分配" in result
        assert "委派给自己" not in result
        # Affinity must now be (re)bound to node_dev — proves to_node was not
        # silently rewritten back onto the caller.
        assert messenger.get_task_affinity("chain_X") == "node_dev"
        # And the actual task message routed by messenger has to_node=node_dev.
        msg = _last_task_message(messenger)
        assert msg.to_node == "node_dev"
        assert msg.from_node == "node_cto"

    @pytest.mark.asyncio
    async def test_legacy_chain_mode_does_not_self_redirect_to_caller(
        self, mock_runtime_full, persisted_org, monkeypatch,
    ):
        # Legacy: chain_id is reused from caller's current_chain instead of
        # re-generated each delegate. This is the original failure path before
        # ``org_chain_parent_enforced`` was introduced.
        monkeypatch.setattr(settings, "org_chain_parent_enforced", False)
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="chain_X")

        messenger = mock_runtime_full.get_messenger(persisted_org.id)
        messenger.bind_task_affinity("chain_X", "node_cto")

        handler = OrgToolHandler(mock_runtime_full)
        result = await handler._handle_org_delegate_task(
            {"to_node": "node_dev", "task": "做需求评审"},
            persisted_org.id, "node_cto",
        )
        assert "任务已分配" in result
        assert "委派给自己" not in result
        assert messenger.get_task_affinity("chain_X") == "node_dev"
        msg = _last_task_message(messenger)
        assert msg.to_node == "node_dev"

    @pytest.mark.asyncio
    async def test_default_mode_new_child_chain_is_unaffected_by_old_affinity(
        self, mock_runtime_full, persisted_org,
    ):
        """In default (enforced) mode, a new child chain id is generated, so
        affinity bound on a parent chain must not bleed onto the new chain.
        """
        messenger = mock_runtime_full.get_messenger(persisted_org.id)
        messenger.bind_task_affinity("chain_X", "node_cto")
        mock_runtime_full.get_current_chain_id = MagicMock(return_value="chain_X")

        handler = OrgToolHandler(mock_runtime_full)
        result = await handler._handle_org_delegate_task(
            {"to_node": "node_dev", "task": "实现搜索"},
            persisted_org.id, "node_cto",
        )
        assert "任务已分配" in result
        msg = _last_task_message(messenger)
        assert msg.to_node == "node_dev"
        # New chain should be a fresh id, parented under chain_X
        assert "chain_X" in mock_runtime_full._chain_parent.values()


class TestAffinityCloneGroupStillWorks:
    """Make sure the legitimate use of affinity (sticky-routing across clones
    of the same source node) is preserved by the same-clone-group guard.
    """

    @pytest.mark.asyncio
    async def test_clone_group_member_is_redirected_back_to_existing_affinity(
        self, org_manager, tmp_data_dir,
    ):
        # Build a tiny org: ceo + two dev clones (dev_a, dev_b) of the same
        # source role. Only direct child of ceo to keep hierarchy validation
        # happy is ``role_dev``; the clones list role_dev as ``clone_source``.
        nodes = [
            make_node("node_ceo", "CEO", 0, "管理层"),
            make_node("role_dev", "开发", 1, "技术部"),
            make_node(
                "node_dev_a", "开发A", 1, "技术部",
                clone_source="role_dev",
            ),
            make_node(
                "node_dev_b", "开发B", 1, "技术部",
                clone_source="role_dev",
            ),
        ]
        edges = [
            make_edge("node_ceo", "role_dev"),
            make_edge("node_ceo", "node_dev_a"),
            make_edge("node_ceo", "node_dev_b"),
        ]
        org = org_manager.create(
            make_org(id="org_clone", nodes=nodes, edges=edges).to_dict(),
        )

        # Wire up a runtime mock similar to mock_runtime / mock_runtime_full
        # but pointing at this clone-aware org.
        from openakita.orgs.event_store import OrgEventStore
        from openakita.orgs.blackboard import OrgBlackboard
        from openakita.orgs.messenger import OrgMessenger

        org_dir = org_manager._org_dir(org.id)
        es = OrgEventStore(org_dir, org.id)
        bb = OrgBlackboard(org_dir, org.id)
        messenger = OrgMessenger(org, org_dir)

        from unittest.mock import AsyncMock
        rt = MagicMock()
        rt._manager = org_manager
        rt.get_org = MagicMock(return_value=org)
        rt._active_orgs = {org.id: org}
        rt._chain_delegation_depth = {}
        rt._chain_parent = {}
        rt._chain_events = OrderedDict()
        rt._closed_chains = {}
        rt.is_chain_closed = MagicMock(return_value=False)
        rt.get_current_chain_id = MagicMock(return_value=None)
        rt._cleanup_accepted_chain = MagicMock(return_value=None)
        rt.get_event_store = MagicMock(return_value=es)
        rt.get_blackboard = MagicMock(return_value=bb)
        rt.get_messenger = MagicMock(return_value=messenger)
        rt._broadcast_ws = AsyncMock()
        rt._save_org = AsyncMock()
        scaler_mock = MagicMock()
        scaler_mock.try_reclaim_idle_clones = AsyncMock(return_value=[])
        rt.get_scaler = MagicMock(return_value=scaler_mock)
        rt._touch_trackers_for_org = MagicMock()

        # Pre-bind chain_X to dev_a (a clone of role_dev). Now CEO calls
        # delegate(to_node=node_dev_b) reusing chain_X. node_dev_b is also a
        # clone of role_dev → same clone group → must be redirected back to
        # the bound clone (dev_a) for sticky routing.
        messenger.bind_task_affinity("chain_X", "node_dev_a")

        handler = OrgToolHandler(rt)
        result = await handler._handle_org_delegate_task(
            {
                "to_node": "node_dev_b",
                "task": "继续聊上一轮的事情",
                "task_chain_id": "chain_X",
            },
            org.id, "node_ceo",
        )
        assert "任务已分配" in result
        msg = _last_task_message(messenger)
        # Must have routed to the originally bound clone, not the LLM's choice.
        assert msg.to_node == "node_dev_a"


# ===========================================================================
# Bug B — auto-persist long structured deliverables as attachments
# ===========================================================================


@pytest.mark.asyncio
class TestDeliverableAutoAttachment:
    async def test_long_markdown_deliverable_is_auto_attached(
        self, tmp_path, persisted_org, mock_runtime,
    ):
        # Add the parent edge so the dev node has a "上级" to submit to.
        from openakita.orgs.models import EdgeType, OrgEdge
        persisted_org.edges.append(
            OrgEdge(source="node_cto", target="node_dev", edge_type=EdgeType.HIERARCHY),
        )

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
                "file_size": Path(file_path).stat().st_size,
            }

        mock_runtime._register_file_output = fake_register
        mock_runtime._resolve_org_workspace = MagicMock(return_value=tmp_path)

        # Real-world deliverable shape from the user bug report — markdown
        # heading + list bullets, ~480 chars in Chinese.
        body = (
            "# 产品部本周工作计划（2026-04-18 至 2026-04-25）\n\n"
            "## 任务一：产品需求评审与优先级梳理\n"
            "- **时间安排**：2 小时（周一上午 9:00-11:00）\n"
            "- **优先级**：高\n"
            "- **预期产出**：完成现有需求池的评审，输出优先级排序文档，确定本周开发任务清单\n\n"
            "## 任务二：用户反馈分析与产品优化方案\n"
            "- **时间安排**：3 小时（周二下午）\n"
            "- **优先级**：中\n"
            "- **预期产出**：整理近两周用户反馈（约 50 条），识别 Top 5 高频问题\n\n"
            "## 任务三：线上产品发布会演示准备\n"
            "- **时间安排**：4 小时（周三上午）\n"
            "- **优先级**：高\n"
            "- **预期产出**：演示流程设计文档、核心功能展示顺序规划、演示脚本大纲\n"
        )
        assert len(body) >= 300

        handler = OrgToolHandler(mock_runtime)
        result = await handler.handle(
            "org_submit_deliverable",
            {
                "to_node": "node_cto",
                "task_chain_id": "chain-auto-attach",
                "deliverable": body,
                "summary": "产品部本周工作计划",
            },
            persisted_org.id, "node_dev",
        )
        assert "已提交" in result

        # The auto-persist path must have funnelled through the canonical
        # _register_file_output entry exactly once with our generated md path.
        assert len(register_calls) == 1
        assert register_calls[0]["filename"].endswith(".md")
        produced = Path(register_calls[0]["file_path"])
        assert produced.exists()
        assert produced.is_file()
        # File should live under <workspace>/deliverables/ — proves we did
        # not escape the workspace via title path-traversal.
        assert produced.parent.resolve() == (tmp_path / "deliverables").resolve()
        # File content must include the original deliverable body verbatim
        # (so downstream consumers see the same thing the agent submitted).
        assert "产品部本周工作计划" in produced.read_text(encoding="utf-8")

        # Outgoing TASK_DELIVERED message must carry the new attachment in
        # metadata so the parent's tracker can pick it up.
        messenger = mock_runtime.get_messenger(persisted_org.id)
        sent = _last_task_message(messenger)
        meta = getattr(sent, "metadata", {}) or {}
        assert "file_attachments" in meta
        assert any(
            Path(a["file_path"]).name == produced.name
            for a in meta["file_attachments"]
        )

    async def test_explicit_attachments_skip_auto_persist(
        self, tmp_path, persisted_org, mock_runtime,
    ):
        """If the caller already provided file_attachments, we must NOT
        spawn a parallel auto-persist file (no double registration).
        """
        from openakita.orgs.models import EdgeType, OrgEdge
        persisted_org.edges.append(
            OrgEdge(source="node_cto", target="node_dev", edge_type=EdgeType.HIERARCHY),
        )

        f = tmp_path / "explicit.md"
        f.write_text("# explicit", encoding="utf-8")

        register_calls: list[dict] = []

        def fake_register(
            org_id, node_id, *, chain_id, filename, file_path, workspace=None,
        ):
            register_calls.append({"file_path": file_path})
            return {
                "filename": filename or Path(file_path).name,
                "file_path": file_path,
                "file_size": Path(file_path).stat().st_size,
            }

        mock_runtime._register_file_output = fake_register
        mock_runtime._resolve_org_workspace = MagicMock(return_value=tmp_path)

        handler = OrgToolHandler(mock_runtime)
        # Long markdown body that WOULD trigger auto-persist if no explicit
        # attachment was present.
        body = "# 大文档标题\n\n" + ("- bullet 内容\n" * 60)
        assert len(body) >= 300

        result = await handler.handle(
            "org_submit_deliverable",
            {
                "to_node": "node_cto",
                "task_chain_id": "chain-explicit",
                "deliverable": body,
                "file_attachments": [
                    {"filename": "explicit.md", "file_path": str(f)},
                ],
            },
            persisted_org.id, "node_dev",
        )
        assert "已提交" in result
        # Exactly one register call — for the explicit attachment, no auto file.
        assert len(register_calls) == 1
        assert register_calls[0]["file_path"] == str(f)
        # No <workspace>/deliverables/ file should have been written.
        deliverables_dir = tmp_path / "deliverables"
        if deliverables_dir.exists():
            assert not any(deliverables_dir.iterdir()), (
                "auto-persist should be skipped when explicit attachments exist"
            )

    async def test_short_or_unstructured_deliverable_does_not_trigger(
        self, tmp_path, persisted_org, mock_runtime,
    ):
        """Plain conversational replies (short / no markdown structure)
        must never auto-spawn a file attachment — that would make every
        chat-style "我已完成" turn into noise on the blackboard.
        """
        from openakita.orgs.models import EdgeType, OrgEdge
        persisted_org.edges.append(
            OrgEdge(source="node_cto", target="node_dev", edge_type=EdgeType.HIERARCHY),
        )

        register_calls: list[dict] = []
        mock_runtime._register_file_output = MagicMock(
            side_effect=lambda *a, **kw: register_calls.append(kw) or None,
        )
        mock_runtime._resolve_org_workspace = MagicMock(return_value=tmp_path)

        handler = OrgToolHandler(mock_runtime)

        # Case 1: short text, no markdown — must NOT trigger.
        await handler.handle(
            "org_submit_deliverable",
            {
                "to_node": "node_cto",
                "task_chain_id": "chain-short",
                "deliverable": "我已经完成了，详情见聊天记录。",
            },
            persisted_org.id, "node_dev",
        )

        # Case 2: long but unstructured paragraph (no heading, no bullets,
        # no fenced code block) — must NOT trigger either, since it's just
        # prose rather than a "document" worth attaching.
        long_prose = (
            "这是一段很长的纯文字汇报内容，没有任何 markdown 结构标记。"
            * 30
        )
        assert len(long_prose) >= 300
        await handler.handle(
            "org_submit_deliverable",
            {
                "to_node": "node_cto",
                "task_chain_id": "chain-prose",
                "deliverable": long_prose,
            },
            persisted_org.id, "node_dev",
        )

        deliverables_dir = tmp_path / "deliverables"
        if deliverables_dir.exists():
            assert list(deliverables_dir.iterdir()) == [], (
                "short / unstructured deliverables must not auto-persist"
            )
        # And register_file_output must not have been called for these cases.
        assert register_calls == []


# ===========================================================================
# Bug C — system prompt no longer mentions the literal "node_xxxxxxxx"
# ===========================================================================


class TestIdentityPromptUsesRealNodeIds:
    """``OrgIdentity.build_org_context_prompt`` must:
      - never emit the literal placeholder ``node_xxxxxxxx``
      - reference the calling node's actual id as the example for
        ``to_node``-shaped parameters
      - still mention an org introspection tool so a confused LLM has a
        recovery path.
    """

    def _build(self, tmp_path: Path, org, node):
        identity = OrgIdentity(tmp_path)
        resolved = ResolvedIdentity(soul="", agent="", role="", level=0)
        return identity.build_org_context_prompt(node, org, resolved)

    def test_short_id_org_does_not_emit_node_xxxxxxxx_placeholder(
        self, tmp_path,
    ):
        # Match the user's real org shape: short ids like ``cpo`` / ``pm``.
        nodes = [
            make_node("ceo", "CEO", 0, "管理层"),
            make_node("cpo", "CPO", 1, "产品部"),
            make_node("pm", "产品经理", 2, "产品部"),
        ]
        edges = [
            make_edge("ceo", "cpo"),
            make_edge("cpo", "pm"),
        ]
        org = make_org(id="org_short", nodes=nodes, edges=edges)

        prompt = self._build(tmp_path, org, org.get_node("cpo"))

        assert "node_xxxxxxxx" not in prompt, (
            "system prompt must not tell the LLM to invent fake node ids"
        )
        # It should reference the caller's own id as an inline example —
        # this is what the new wording does (``例如 \`{node.id}\```).
        assert "`cpo`" in prompt
        # And it should give the LLM a recovery path to introspect the org.
        assert "org_get_org_chart" in prompt or "org_find_colleague" in prompt

    def test_long_uuid_id_org_also_uses_real_id_as_example(self, tmp_path):
        nodes = [
            make_node("node_ceo_abcd1234", "CEO", 0, "管理层"),
            make_node("node_cto_efgh5678", "CTO", 1, "技术部"),
        ]
        edges = [
            make_edge("node_ceo_abcd1234", "node_cto_efgh5678"),
        ]
        org = make_org(id="org_long", nodes=nodes, edges=edges)

        prompt = self._build(tmp_path, org, org.get_node("node_cto_efgh5678"))

        assert "node_xxxxxxxx" not in prompt
        assert "`node_cto_efgh5678`" in prompt
