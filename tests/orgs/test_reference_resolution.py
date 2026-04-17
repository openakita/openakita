"""Tests for Organization.resolve_reference + strict _resolve_node_refs.

Covers the six status branches of ``resolve_reference`` and verifies that
strict tools (``org_delegate_task`` / ``org_send_message``) surface
structured errors instead of silently binding to a fuzzy / self match.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openakita.orgs.models import (
    EdgeType,
    Organization,
    OrgEdge,
    OrgNode,
)
from openakita.orgs.tool_handler import OrgToolHandler, _STRICT_REF_TOOLS


# ---------------------------------------------------------------------------
# Organization.resolve_reference — the six canonical branches
# ---------------------------------------------------------------------------


def _mk_org(titles: list[tuple[str, str]]) -> Organization:
    """Build an org from (node_id, role_title) pairs with trivial hierarchy."""
    nodes = [
        OrgNode(
            id=nid,
            role_title=title,
            role_goal="",
            role_backstory="",
            level=idx,
            department="d",
        )
        for idx, (nid, title) in enumerate(titles)
    ]
    edges: list[OrgEdge] = []
    for i in range(len(nodes) - 1):
        edges.append(
            OrgEdge(
                source=nodes[i].id, target=nodes[i + 1].id,
                edge_type=EdgeType.HIERARCHY,
            )
        )
    return Organization(id="org_t", name="t", nodes=nodes, edges=edges)


class TestResolveReferenceBranches:
    def test_exact_id_literal(self):
        org = _mk_org([("node_ceo", "CEO"), ("node_cto", "CTO")])
        node, candidates, status = org.resolve_reference("node_cto")
        assert status == "exact_id"
        assert node is not None and node.id == "node_cto"
        assert candidates == []

    def test_exact_id_normalized(self):
        org = _mk_org([("node_ceo", "CEO"), ("node_cto", "CTO")])
        # Uppercased / hyphen-normalized underscore variants should still
        # resolve to the same canonical id.
        node, _c, status = org.resolve_reference("NODE_CTO")
        assert status == "exact_id"
        assert node is not None and node.id == "node_cto"

    def test_exact_title_unique(self):
        org = _mk_org([("node_ceo", "CEO"), ("node_cto", "CTO")])
        node, _c, status = org.resolve_reference("CTO")
        assert status == "exact_title"
        assert node is not None and node.id == "node_cto"

    def test_exact_title_case_insensitive(self):
        # "cto" should still resolve — case-insensitive title match must
        # remain exact (pre-existing caller expectation in
        # test_execution_robustness::test_resolve_case_insensitive).
        org = _mk_org([("node_ceo", "CEO"), ("node_cto", "CTO")])
        node, _c, status = org.resolve_reference("cto")
        assert status == "exact_title"
        assert node is not None and node.id == "node_cto"

    def test_ambiguous_title_lists_candidates(self):
        # Two nodes literally named "产品经理" ⇒ ambiguous.
        org = _mk_org([
            ("node_a", "产品经理"),
            ("node_b", "产品经理"),
            ("node_c", "CTO"),
        ])
        node, candidates, status = org.resolve_reference("产品经理")
        assert status == "ambiguous_title"
        assert node is None
        assert {c.id for c in candidates} == {"node_a", "node_b"}

    def test_fuzzy_returns_single_candidate_but_not_exact(self):
        # Substring collision: "产品" matches both "产品总监" and
        # "产品经理" via get_node's legacy substring fallback, but neither
        # is exact. Must surface as fuzzy with at least one candidate so
        # the strict handler can emit a "请用精确 id" error.
        org = _mk_org([
            ("node_director", "产品总监"),
            ("node_pm", "产品经理"),
        ])
        node, candidates, status = org.resolve_reference("产品")
        assert status == "fuzzy"
        assert node is None
        assert len(candidates) >= 1

    def test_not_found(self):
        org = _mk_org([("node_ceo", "CEO")])
        node, candidates, status = org.resolve_reference("neverHeardOfHim")
        assert status == "not_found"
        assert node is None
        assert candidates == []


# ---------------------------------------------------------------------------
# _STRICT_REF_TOOLS wiring — only write-effect tools get strict treatment
# ---------------------------------------------------------------------------


class TestStrictRefToolsConstant:
    def test_strict_set_covers_writers(self):
        assert "org_delegate_task" in _STRICT_REF_TOOLS
        assert "org_send_message" in _STRICT_REF_TOOLS
        assert "org_reply_message" in _STRICT_REF_TOOLS

    def test_strict_set_excludes_readers(self):
        # Search / read tools must keep lenient behaviour so
        # org_find_colleague, org_get_memory_of_node etc. still accept
        # role_title shorthand.
        assert "org_find_colleague" not in _STRICT_REF_TOOLS
        assert "org_get_memory_of_node" not in _STRICT_REF_TOOLS
        assert "org_get_org_chart" not in _STRICT_REF_TOOLS


# ---------------------------------------------------------------------------
# _resolve_node_refs — strict vs lenient behaviour
# ---------------------------------------------------------------------------


@pytest.fixture()
def substring_org():
    return _mk_org([
        ("node_director", "产品总监"),
        ("node_pm", "产品经理"),
    ])


class TestResolveNodeRefsStrict:
    def test_strict_keeps_fuzzy_query_raw(self, substring_org):
        rt = MagicMock()
        rt.get_org = MagicMock(return_value=substring_org)
        handler = OrgToolHandler(rt)

        args = {"to_node": "产品", "content": "hi"}
        handler._resolve_node_refs(
            args, substring_org.id, tool_name="org_send_message",
        )
        # fuzzy ⇒ NOT rewritten, preserved for the handler's error branch
        assert args["to_node"] == "产品"

    def test_strict_resolves_exact_id(self, substring_org):
        rt = MagicMock()
        rt.get_org = MagicMock(return_value=substring_org)
        handler = OrgToolHandler(rt)

        args = {"to_node": "node_pm", "content": "hi"}
        handler._resolve_node_refs(
            args, substring_org.id, tool_name="org_send_message",
        )
        assert args["to_node"] == "node_pm"

    def test_strict_resolves_exact_title(self, substring_org):
        rt = MagicMock()
        rt.get_org = MagicMock(return_value=substring_org)
        handler = OrgToolHandler(rt)

        args = {"to_node": "产品总监", "content": "hi"}
        handler._resolve_node_refs(
            args, substring_org.id, tool_name="org_send_message",
        )
        assert args["to_node"] == "node_director"

    def test_lenient_preserves_fuzzy_input_without_error(self, substring_org):
        # Tool not in strict set ⇒ legacy lenient path. The old contract is
        # "if get_node can find it, don't bother rewriting"; downstream
        # handlers call get_node() again and resolve correctly. What we
        # must NOT do is clear the value or raise — this test pins that
        # contract so refactors don't accidentally break search-flow
        # handlers like org_find_colleague.
        rt = MagicMock()
        rt.get_org = MagicMock(return_value=substring_org)
        handler = OrgToolHandler(rt)

        args = {"node_id": "产品总监"}
        handler._resolve_node_refs(
            args, substring_org.id, tool_name="org_find_colleague",
        )
        # Value preserved, and downstream can still resolve it.
        assert args["node_id"] == "产品总监"
        resolved = substring_org.get_node(args["node_id"])
        assert resolved is not None and resolved.id == "node_director"


# ---------------------------------------------------------------------------
# Structured error messages from delegate / send_message
# ---------------------------------------------------------------------------


@pytest.fixture()
def handler_with_substring_org(org_dir, persisted_org, mock_runtime):
    """Reuse the persisted_org fixture but swap in an org whose role_titles
    are substring-collidy so the strict guard has something to reject."""
    org = _mk_org([
        ("node_ceo", "CEO"),
        ("node_director", "产品总监"),
        ("node_pm", "产品经理"),
    ])
    # Hierarchy: CEO → 产品总监 → 产品经理 (so 产品经理 is 总监's child)
    org.edges = [
        OrgEdge(source="node_ceo", target="node_director", edge_type=EdgeType.HIERARCHY),
        OrgEdge(source="node_director", target="node_pm", edge_type=EdgeType.HIERARCHY),
    ]
    # Persist so messenger can open the sqlite-backed files.
    mock_runtime.get_org = MagicMock(return_value=org)
    mock_runtime._active_orgs = {org.id: org}

    from openakita.orgs.event_store import OrgEventStore
    from openakita.orgs.blackboard import OrgBlackboard
    from openakita.orgs.messenger import OrgMessenger

    mock_runtime.get_event_store = MagicMock(return_value=OrgEventStore(org_dir, org.id))
    mock_runtime.get_blackboard = MagicMock(return_value=OrgBlackboard(org_dir, org.id))
    mock_runtime.get_messenger = MagicMock(return_value=OrgMessenger(org, org_dir))
    mock_runtime._broadcast_ws = AsyncMock()
    return OrgToolHandler(mock_runtime), org


class TestStrictHandlerErrors:
    @pytest.mark.asyncio
    async def test_delegate_fuzzy_to_self_gets_self_error(
        self, handler_with_substring_org,
    ):
        handler, org = handler_with_substring_org
        # 产品总监 tries "to_node=产品" — fuzzy will match both, we want a
        # structured "不能委派给自己" message that includes the caller's id.
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "产品", "task": "写个方案"},
            org.id, "node_director",
        )
        assert "[org_delegate_task 失败]" in result
        assert "精确" in result or "node_xxxxxxxx" in result
        # Caller id should always appear to help the LLM orient.
        assert "node_director" in result

    @pytest.mark.asyncio
    async def test_delegate_ambiguous_title_lists_candidates(self, org_dir):
        # Two literal "产品经理"s under one director
        from openakita.orgs.messenger import OrgMessenger
        from openakita.orgs.event_store import OrgEventStore
        from openakita.orgs.blackboard import OrgBlackboard

        org = _mk_org([
            ("node_ceo", "CEO"),
            ("node_director", "产品总监"),
            ("node_pm_a", "产品经理"),
            ("node_pm_b", "产品经理"),
        ])
        org.edges = [
            OrgEdge(source="node_ceo", target="node_director", edge_type=EdgeType.HIERARCHY),
            OrgEdge(source="node_director", target="node_pm_a", edge_type=EdgeType.HIERARCHY),
            OrgEdge(source="node_director", target="node_pm_b", edge_type=EdgeType.HIERARCHY),
        ]
        rt = MagicMock()
        rt.get_org = MagicMock(return_value=org)
        rt._active_orgs = {org.id: org}
        rt._chain_delegation_depth = {}
        rt.is_chain_closed = MagicMock(return_value=False)
        rt.get_current_chain_id = MagicMock(return_value=None)
        rt.get_event_store = MagicMock(return_value=OrgEventStore(org_dir, org.id))
        rt.get_blackboard = MagicMock(return_value=OrgBlackboard(org_dir, org.id))
        rt.get_messenger = MagicMock(return_value=OrgMessenger(org, org_dir))
        rt._broadcast_ws = AsyncMock()
        rt._manager = MagicMock()
        rt._manager._org_dir = MagicMock(return_value=org_dir)

        handler = OrgToolHandler(rt)
        result = await handler.handle(
            "org_delegate_task",
            {"to_node": "产品经理", "task": "写 PRD"},
            org.id, "node_director",
        )
        assert "[org_delegate_task 失败]" in result
        assert "node_pm_a" in result and "node_pm_b" in result

    @pytest.mark.asyncio
    async def test_send_message_fuzzy_to_self_blocked(
        self, handler_with_substring_org,
    ):
        handler, org = handler_with_substring_org
        # 产品总监 sends a message with a fuzzy title that also matches
        # itself — must get a structured error, not a silent "消息已发送给自己".
        result = await handler.handle(
            "org_send_message",
            {"to_node": "产品", "content": "hi"},
            org.id, "node_director",
        )
        assert "[org_send_message 失败]" in result
        assert "node_director" in result
