from __future__ import annotations

from openakita.core.policy_v2 import ToolPolicy
from openakita.core.risk_intent import TurnRiskAuthorization
from openakita.core.risk_scope import authorization_covers_tool_call, extract_tool_scope
from openakita.core.tool_execution_context import ToolExecutionContext


def _memory_policy() -> ToolPolicy:
    return ToolPolicy(
        commit_requires_riskgate=True,
        riskgate_operation="memory_delete",
        riskgate_scope_params=("query", "source", "memory_type"),
        riskgate_scope_required_any=("query", "source", "memory_type"),
        riskgate_scope_exact_params=("source", "memory_type"),
        riskgate_scope_text_params=("query",),
    )


def test_extract_tool_scope_uses_policy_declared_params_only():
    policy = _memory_policy()

    assert extract_tool_scope(
        {
            "query": " marker ",
            "source": "profile",
            "confirm_token": "not-scope",
            "max_delete": 20,
        },
        policy,
    ) == {"query": "marker", "source": "profile"}


def test_authorization_scope_covers_matching_text_and_exact_fields():
    policy = _memory_policy()
    auth = TurnRiskAuthorization(
        original_message="delete marker",
        confirmation_id="conf-scope",
        authorized_intent={
            "operation": "memory_delete",
            "scope": {
                "query": "OPENAKITA_RISKGATE_689_REPRO_TEST",
                "source": "profile",
            },
            "tool_names": ["memory_delete_by_query"],
        },
    )

    assert authorization_covers_tool_call(
        auth,
        tool_name="memory_delete_by_query",
        tool_input={"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "source": "profile"},
        policy=policy,
    )

    assert not authorization_covers_tool_call(
        auth,
        tool_name="memory_delete_by_query",
        tool_input={"query": "OPENAKITA_RISKGATE_689_REPRO_TEST", "source": "other"},
        policy=policy,
    )
    assert not authorization_covers_tool_call(
        auth,
        tool_name="memory_delete_by_query",
        tool_input={"query": "DIFFERENT_MARKER", "source": "profile"},
        policy=policy,
    )


def test_authorization_scope_can_match_query_against_raw_text():
    policy = _memory_policy()
    auth = TurnRiskAuthorization(
        original_message="delete marker",
        confirmation_id="conf-raw",
        authorized_intent={
            "operation": "memory_delete",
            "scope": {"raw": "请删除所有包含 OPENAKITA_RISKGATE_689_REPRO_TEST 的记忆"},
        },
    )

    assert authorization_covers_tool_call(
        auth,
        tool_name="memory_delete_by_query",
        tool_input={"query": "OPENAKITA_RISKGATE_689_REPRO_TEST"},
        policy=policy,
    )


def test_authorization_scope_text_matching_normalizes_case_and_space():
    policy = _memory_policy()
    auth = TurnRiskAuthorization(
        original_message="delete marker",
        confirmation_id="conf-normalized",
        authorized_intent={
            "operation": "memory_delete",
            "scope": {"query": "  OpenAkita_RiskGate_689_Repro_Test  "},
        },
    )

    assert authorization_covers_tool_call(
        auth,
        tool_name="memory_delete_by_query",
        tool_input={"query": "openakita_riskgate_689_repro_test"},
        policy=policy,
    )


def test_authorization_scope_does_not_match_broad_query_from_raw_text():
    policy = _memory_policy()
    auth = TurnRiskAuthorization(
        original_message="delete memory marker",
        confirmation_id="conf-raw-broad",
        authorized_intent={
            "operation": "memory_delete",
            "scope": {"raw": "请删除所有包含 OPENAKITA_RISKGATE_689_REPRO_TEST 的记忆"},
        },
    )

    assert not authorization_covers_tool_call(
        auth,
        tool_name="memory_delete_by_query",
        tool_input={"query": "记忆"},
        policy=policy,
    )
    assert not authorization_covers_tool_call(
        auth,
        tool_name="memory_delete_by_query",
        tool_input={"query": "memory"},
        policy=policy,
    )


def test_tool_execution_context_consumes_shared_authorization_state():
    policy = _memory_policy()
    auth = TurnRiskAuthorization(
        original_message="delete marker",
        confirmation_id="conf-consume",
        authorized_intent={
            "operation": "memory_delete",
            "scope": {"query": "marker"},
            "tool_names": ["memory_delete_by_query"],
        },
    )
    parent = ToolExecutionContext(risk_authorization=auth)
    first = parent.for_tool(
        tool_name="memory_delete_by_query",
        tool_input={"query": "marker"},
        tool_policy=policy,
    )
    second = parent.for_tool(
        tool_name="memory_delete_by_query",
        tool_input={"query": "marker"},
        tool_policy=policy,
    )

    assert first.authorize_tool_commit(consume=True)
    assert parent.risk_authorization_consumed
    assert not second.authorize_tool_commit()
