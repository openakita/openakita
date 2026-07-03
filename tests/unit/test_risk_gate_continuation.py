"""RiskGate confirmation regression tests.

The backend owns pending/confirmed RiskGate state. UI clients can submit a
decision and render returned status, but executable authorization is only
created from structured tool-call RiskGate metadata.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openakita.core.risk_intent import TurnRiskAuthorization

_TEST_QUERY = "OPENAKITA_RISKGATE_689_REPRO_TEST"


def _tool_input(query: str = _TEST_QUERY) -> dict:
    return {"query": query, "dry_run": False}


def _tool_classification(query: str = _TEST_QUERY) -> dict:
    return {
        "kind": "tool_call",
        "risk_level": "high",
        "operation": "memory_delete",
        "operation_kind": "memory_delete",
        "target_kind": "tool",
        "requires_confirmation": True,
        "action": None,
        "tool_name": "declared_delete_tool",
        "tool_input": _tool_input(query),
        "riskgate_scope": {"query": query},
    }


def _open_tool_confirmation(
    *,
    conversation_id: str,
    request_id: str,
    query: str = _TEST_QUERY,
    original_message: str = "tool:declared_delete_tool",
):
    from openakita.core.risk_gate_workflow import get_risk_gate_workflow

    return get_risk_gate_workflow().open_tool_confirmation(
        conversation_id=conversation_id,
        original_message=original_message,
        classification=_tool_classification(query),
        request_id=request_id,
        tool_name="declared_delete_tool",
        tool_args=_tool_input(query),
        reason="tool commit requires RiskGate",
        timeout_seconds=60,
        channel="desktop",
        approval_class="destructive",
        policy_version=2,
        decision_chain=[],
        delegate_chain=[],
        root_user_id=None,
    )


def test_turn_authorization_defaults_to_operation_matching():
    auth = TurnRiskAuthorization(
        original_message="请删除长期记忆中所有包含 X 的记忆。",
        confirmation_id="conf-tool-free",
        authorized_intent={"operation": "memory_delete"},
    )

    assert auth.tool_names_for_policy() == ()
    assert auth.operation_for_policy() == "delete"


def test_confirmation_store_tracks_multiple_pending_in_one_conversation():
    from openakita.core.confirmation_state import get_confirmation_store
    from openakita.core.risk_gate_workflow import get_risk_gate_workflow
    from openakita.core.ui_confirm_bus import reset_ui_confirm_bus

    reset_ui_confirm_bus()
    store = get_confirmation_store()
    workflow = get_risk_gate_workflow()
    conv = "conv-riskgate-multi-pending"
    store.clear(conv)
    first = _open_tool_confirmation(
        conversation_id=conv,
        request_id="req-first",
        query="A",
        original_message="删除 A",
    ).pending
    second = _open_tool_confirmation(
        conversation_id=conv,
        request_id="req-second",
        query="B",
        original_message="删除 B",
    ).pending

    assert store.get_record(first.confirmation_id).original_message == "删除 A"
    assert store.get_record(second.confirmation_id).original_message == "删除 B"
    assert store.get(conv).confirmation_id == second.confirmation_id

    result = workflow.resolve_decision(first.confirmation_id, "deny")

    assert result is not None
    assert result.approved_tool_call is None
    assert store.get_record(first.confirmation_id).state == "cancelled"
    assert not store.get_record(first.confirmation_id).is_pending()
    assert store.get_record(second.confirmation_id).is_pending()
    assert [r.confirmation_id for r in store.list_records(conv)] == [
        first.confirmation_id,
        second.confirmation_id,
    ]
    reset_ui_confirm_bus()


def test_confirmation_store_times_out_pending_records_then_evicts_them(monkeypatch):
    from openakita.core import confirmation_state as state_mod

    now = 1000.0
    monkeypatch.setattr(state_mod.time, "time", lambda: now)
    store = state_mod.PendingRiskConfirmationStore(
        ttl_seconds=10,
        terminal_retention_seconds=5,
    )
    conv = "conv-riskgate-ttl-pending"
    pending = store.create_record(
        conversation_id=conv,
        original_message="tool:declared_delete_tool",
        classification=_tool_classification(),
        request_id="req-riskgate-ttl-pending",
    )

    assert store.get_record(pending.confirmation_id).is_pending()

    now = 1011.0
    record = store.get_record(pending.confirmation_id)
    assert record is not None
    assert record.state == "timeout"
    assert record.decision == "timeout"
    assert record.retain_until == 1016.0
    assert store.get(conv) is None

    now = 1016.0
    assert store.get_record(pending.confirmation_id) is None
    assert store.list_records(conv) == []


def test_confirmation_store_evicts_resolved_terminal_records(monkeypatch):
    from openakita.core import confirmation_state as state_mod

    now = 2000.0
    monkeypatch.setattr(state_mod.time, "time", lambda: now)
    store = state_mod.PendingRiskConfirmationStore(
        ttl_seconds=60,
        terminal_retention_seconds=5,
    )
    conv = "conv-riskgate-ttl-terminal"
    pending = store.create_record(
        conversation_id=conv,
        original_message="tool:declared_delete_tool",
        classification=_tool_classification(),
        request_id="req-riskgate-ttl-terminal",
    )
    record = store.get_record(pending.confirmation_id)
    assert record is not None

    store.transition_record(
        record,
        state=state_mod.RiskGateConfirmationState.CANCELLED,
        decision="deny",
        answer="deny",
    )
    assert store.get_record(pending.confirmation_id) is record
    assert record.retain_until == 2005.0

    now = 2005.0
    assert store.sweep_expired() == 1
    assert store.get_record(pending.confirmation_id) is None


# ---------------------------------------------------------------------------
# RiskGate authorization entry points
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_risk_gate_workflow_owns_tool_call_state_and_waiter():
    from openakita.core.risk_gate_workflow import get_risk_gate_workflow
    from openakita.core.ui_confirm_bus import get_ui_confirm_bus, reset_ui_confirm_bus

    reset_ui_confirm_bus()
    bus = get_ui_confirm_bus()
    workflow = get_risk_gate_workflow()
    pending = workflow.open_tool_confirmation(
        conversation_id="conv-riskgate-workflow",
        original_message="tool:declared_delete_tool",
        classification=_tool_classification(),
        request_id="req-riskgate-workflow",
        tool_name="declared_delete_tool",
        tool_args=_tool_input(),
        reason="tool commit requires RiskGate",
        timeout_seconds=60,
        channel="desktop",
        approval_class="destructive",
        policy_version=2,
        decision_chain=[],
        delegate_chain=[],
        root_user_id=None,
    ).pending

    result = workflow.resolve_decision(pending.confirmation_id, "allow_once")

    assert result is not None
    assert result.handled is True
    assert result.riskgate_state == "confirmed"
    assert result.approved_tool_call is not None
    assert await bus.wait_for_resolution(pending.confirmation_id, 0.01) == "allow_once"
    grant = await workflow.wait_for_tool_grant(
        confirmation_id=pending.confirmation_id,
        timeout_seconds=0.01,
    )
    assert grant is not None
    assert grant.tool_name == "declared_delete_tool"
    reset_ui_confirm_bus()


@pytest.mark.asyncio
async def test_security_confirm_endpoint_resolves_riskgate_tool_call():
    from openakita.api.routes import config as config_mod
    from openakita.core.confirmation_state import get_confirmation_store
    from openakita.core.ui_confirm_bus import get_ui_confirm_bus, reset_ui_confirm_bus

    reset_ui_confirm_bus()
    bus = get_ui_confirm_bus()
    store = get_confirmation_store()
    conv = "conv-riskgate-tool-call"
    store.clear(conv)
    pending = _open_tool_confirmation(
        conversation_id=conv,
        request_id="req-riskgate-tool-call",
    ).pending

    response = await config_mod.security_confirm(
        config_mod.SecurityConfirmRequest(
            confirm_id=pending.confirmation_id,
            decision="allow_once",
        ),
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
    )

    assert response["kind"] == "risk_gate"
    assert response["riskgate_state"] == "confirmed"
    assert response["tool"] == "declared_delete_tool"
    assert response["execution"]["state"] == "confirmed"
    assert await bus.wait_for_resolution(pending.confirmation_id, 0.01) == "allow_once"
    record = store.get_record(pending.confirmation_id)
    assert record is not None
    assert record.state == "confirmed"
    assert store.get(conv) is None
    reset_ui_confirm_bus()


@pytest.mark.asyncio
async def test_security_confirmation_resolver_wakes_riskgate_tool_waiter():
    from openakita.core.confirmation_state import get_confirmation_store
    from openakita.core.security_confirmation import resolve_security_confirmation
    from openakita.core.ui_confirm_bus import get_ui_confirm_bus, reset_ui_confirm_bus

    reset_ui_confirm_bus()
    bus = get_ui_confirm_bus()
    store = get_confirmation_store()
    conv = "conv-riskgate-tool-call-resolver"
    store.clear(conv)
    pending = _open_tool_confirmation(
        conversation_id=conv,
        request_id="req-riskgate-tool-call-resolver",
    ).pending
    response = resolve_security_confirmation(pending.confirmation_id, "allow_once")

    assert response["handled"] is True
    assert response["kind"] == "risk_gate"
    assert response["riskgate_state"] == "confirmed"
    assert await bus.wait_for_resolution(pending.confirmation_id, 0.01) == "allow_once"
    record = store.get_record(pending.confirmation_id)
    assert record is not None
    assert record.state == "confirmed"
    assert store.get(conv) is None
    reset_ui_confirm_bus()


@pytest.mark.asyncio
async def test_security_confirm_endpoint_reports_terminal_riskgate_tool_call():
    from openakita.api.routes import config as config_mod
    from openakita.core.confirmation_state import get_confirmation_store
    from openakita.core.security_confirmation import resolve_security_confirmation
    from openakita.core.ui_confirm_bus import reset_ui_confirm_bus

    reset_ui_confirm_bus()
    store = get_confirmation_store()
    conv = "conv-riskgate-tool-call-terminal"
    store.clear(conv)
    pending = _open_tool_confirmation(
        conversation_id=conv,
        request_id="req-riskgate-tool-call-terminal",
    ).pending
    resolve_security_confirmation(pending.confirmation_id, "deny")

    response = await config_mod.security_confirm(
        config_mod.SecurityConfirmRequest(
            confirm_id=pending.confirmation_id,
            decision="allow_once",
        ),
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
    )

    assert response["kind"] == "risk_gate"
    assert response["riskgate_state"] == "cancelled"
    assert response["execution"]["state"] == "cancelled"
    assert "ui_message" in response
    reset_ui_confirm_bus()


@pytest.mark.asyncio
async def test_riskgate_tool_wait_timeout_marks_store_terminal():
    from openakita.core.confirmation_state import get_confirmation_store
    from openakita.core.risk_gate_tools import resolve_riskgate_tool_decision
    from openakita.core.ui_confirm_bus import reset_ui_confirm_bus

    reset_ui_confirm_bus()
    store = get_confirmation_store()
    conv = "conv-riskgate-tool-timeout"
    store.clear(conv)
    pending = _open_tool_confirmation(
        conversation_id=conv,
        request_id="req-riskgate-tool-timeout",
    ).pending

    approved = await resolve_riskgate_tool_decision(
        confirmation_id=pending.confirmation_id,
        timeout_seconds=0.01,
    )

    assert approved is None
    record = store.get_record(pending.confirmation_id)
    assert record is not None
    assert record.state == "timeout"
    assert store.get(conv) is None
    reset_ui_confirm_bus()


@pytest.mark.asyncio
async def test_riskgate_tool_prompt_result_centralizes_confirmed_execution():
    from openakita.core.confirmation_state import get_confirmation_store
    from openakita.core.reasoning_engine import (
        _execute_riskgate_tool_confirmation,
        _open_riskgate_tool_confirmation,
    )
    from openakita.core.risk_gate_workflow import get_risk_gate_workflow
    from openakita.core.ui_confirm_bus import reset_ui_confirm_bus

    class FakeExecutor:
        def __init__(self):
            self.calls = []

        async def execute_tool_with_policy(self, **kwargs):
            self.calls.append(kwargs)
            return ("deleted marker", None)

    reset_ui_confirm_bus()
    conv = "conv-riskgate-tool-result-helper"
    get_confirmation_store().clear(conv)
    workflow = get_risk_gate_workflow()
    policy_result = SimpleNamespace(
        metadata={
            "riskgate_required": True,
            "riskgate_operation": "memory_delete",
            "riskgate_scope": {"query": _TEST_QUERY},
            "risk_level": "high",
        },
        reason="tool commit requires RiskGate",
        approval_class=SimpleNamespace(value="destructive"),
        to_ui_chain=lambda: [],
    )
    confirmation = _open_riskgate_tool_confirmation(
        conversation_id=conv,
        tool_name="declared_delete_tool",
        tool_input=_tool_input(),
        policy_result=policy_result,
        tool_id="tc-riskgate-helper",
        timeout_seconds=60,
        channel="desktop",
        delegate_chain=[],
        root_user_id=None,
    )
    assert confirmation.prompt_event["type"] == "security_confirm"
    assert confirmation.prompt_event["source"] == "risk_gate"
    workflow.resolve_decision(confirmation.confirmation_id, "allow_once")
    executor = FakeExecutor()

    result = await _execute_riskgate_tool_confirmation(
        executor,
        confirmation=confirmation,
        detect_result_errors=True,
        summarize_tool_result=lambda _tool, text: f"summary: {text}",
    )

    assert result.result_text == "deleted marker"
    assert result.result_summary == "summary: deleted marker"
    assert result.tool_result == {
        "type": "tool_result",
        "tool_use_id": "tc-riskgate-helper",
        "content": "deleted marker",
        "is_error": False,
        "tool_name": "declared_delete_tool",
    }
    assert result.end_events[0]["type"] == "tool_call_end"
    assert result.end_events[0]["id"] == "tc-riskgate-helper"
    assert result.end_events[0]["is_error"] is False
    call = executor.calls[0]
    assert call["policy_result"].metadata["confirmed_bypass"] is True
    auth = call["execution_context"].risk_authorization
    assert auth.confirmation_id == confirmation.confirmation_id
    assert auth.authorized_intent["operation"] == "memory_delete"
    assert auth.authorized_intent["tool_names"] == ["declared_delete_tool"]
    reset_ui_confirm_bus()


@pytest.mark.asyncio
async def test_security_confirm_endpoint_cancels_riskgate_pending():
    from openakita.api.routes import config as config_mod
    from openakita.core.confirmation_state import get_confirmation_store

    store = get_confirmation_store()
    conv = "conv-riskgate-modal-cancel"
    store.clear(conv)
    pending = _open_tool_confirmation(
        conversation_id=conv,
        request_id="req-riskgate-modal-cancel",
    ).pending

    response = await config_mod.security_confirm(
        config_mod.SecurityConfirmRequest(
            confirm_id=pending.confirmation_id,
            decision="deny",
        ),
        None,
    )

    assert response["kind"] == "risk_gate"
    assert response["decision"] == "deny"
    assert response["riskgate_state"] == "cancelled"
    assert response["ui_message"] == "RiskGate 确认已拒绝，本次高风险操作已取消，未继续执行。"
    assert response["execution"]["state"] == "cancelled"
    assert response["execution"]["backend_owned"] is True
    assert response["execution"]["client_action"] == "none"
    assert store.get(conv) is None


@pytest.mark.asyncio
async def test_security_confirm_endpoint_records_riskgate_timeout_terminal_state():
    from openakita.api.routes import config as config_mod
    from openakita.core.confirmation_state import get_confirmation_store

    store = get_confirmation_store()
    conv = "conv-riskgate-modal-timeout"
    store.clear(conv)
    pending = _open_tool_confirmation(
        conversation_id=conv,
        request_id="req-riskgate-modal-timeout",
    ).pending

    response = await config_mod.security_confirm(
        config_mod.SecurityConfirmRequest(
            confirm_id=pending.confirmation_id,
            decision="timeout",
        ),
        None,
    )

    record = store.get_record(pending.confirmation_id)
    assert response["kind"] == "risk_gate"
    assert response["decision"] == "timeout"
    assert response["riskgate_state"] == "timeout"
    assert response["execution"]["state"] == "timeout"
    assert response["execution"]["client_action"] == "none"
    assert record is not None
    assert record.state == "timeout"
    assert store.get(conv) is None


def test_ask_user_reply_request_is_structured():
    from openakita.api.routes.chat import _ask_user_reply_context
    from openakita.api.schemas import ChatRequest

    req = ChatRequest(
        message="confirm_delete",
        conversation_id="conv-normal-ask",
        ask_user_reply={"kind": "normal", "message_id": "msg-1", "answer": "confirm_delete"},
    )

    assert req.ask_user_reply is not None
    assert req.ask_user_reply.answer == "confirm_delete"
    assert req.message == "confirm_delete"
    ctx = _ask_user_reply_context(req)
    assert ctx is not None
    assert ctx.answer == "confirm_delete"
    assert ctx.message_id == "msg-1"


def test_ask_user_reply_context_falls_back_to_message():
    from openakita.api.routes.chat import _ask_user_reply_context
    from openakita.api.schemas import ChatRequest

    req = ChatRequest(
        message="继续使用方案 A",
        conversation_id="conv-normal-ask",
        ask_user_reply={"kind": "normal", "message_id": "msg-2"},
    )

    ctx = _ask_user_reply_context(req)
    assert ctx is not None
    assert ctx.answer == "继续使用方案 A"


def test_ask_user_reply_context_can_use_explicit_answer_without_message():
    from openakita.api.routes.chat import _ask_user_reply_context
    from openakita.api.schemas import ChatRequest

    req = ChatRequest(
        message="",
        conversation_id="conv-normal-ask",
        ask_user_reply={"kind": "normal", "message_id": "msg-3", "answer": "选择第二项"},
    )

    ctx = _ask_user_reply_context(req)
    assert ctx is not None
    assert ctx.answer == "选择第二项"
