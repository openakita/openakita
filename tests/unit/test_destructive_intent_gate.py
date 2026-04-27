from types import SimpleNamespace

from openakita.core.agent import _build_destructive_intent_question, _classify_risk_intent
from openakita.core.confirmation_state import ConfirmationDecision, get_confirmation_store
from openakita.core.loop_budget_guard import LoopBudgetGuard
from openakita.core.risk_intent import TargetKind
from openakita.core.working_facts import extract_working_facts, format_working_facts


def test_destructive_intent_detects_policy_allowlist_delete():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "删除 security user_allowlist 第 0 条")
    assert result.requires_confirmation
    assert result.target_kind == TargetKind.SECURITY_USER_ALLOWLIST
    assert result.action == "remove_security_allowlist_entry"
    assert result.parameters["index"] == 0


def test_destructive_intent_uses_intent_analyzer_flag():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=True))

    assert _classify_risk_intent(intent, "改一下配置").requires_confirmation


def test_readonly_allowlist_explanation_does_not_confirm():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "解释 allowlist 三者区别")

    assert not result.requires_confirmation


def test_arithmetic_add_does_not_confirm():
    intent = SimpleNamespace(
        complexity=SimpleNamespace(destructive_potential=False),
        requires_tools=False,
        risk_level_hint="none",
    )

    result = _classify_risk_intent(intent, "what is 19 * 23, and then add 4")

    assert not result.requires_confirmation


def test_removed_fact_revision_does_not_confirm():
    intent = SimpleNamespace(
        complexity=SimpleNamespace(destructive_potential=False),
        requires_tools=False,
        risk_level_hint="none",
    )

    result = _classify_risk_intent(
        intent,
        "one module was removed, calculate the revised count",
    )

    assert not result.requires_confirmation


def test_hypothetical_delete_discussion_does_not_confirm():
    intent = SimpleNamespace(
        complexity=SimpleNamespace(destructive_potential=False),
        requires_tools=False,
        risk_level_hint="low",
    )

    result = _classify_risk_intent(
        intent,
        "suppose I say delete files, what should you do?",
    )

    assert not result.requires_confirmation


def test_rm_rf_still_requires_confirmation():
    intent = SimpleNamespace(complexity=SimpleNamespace(destructive_potential=False))

    result = _classify_risk_intent(intent, "rm -rf data")

    assert result.requires_confirmation


def test_destructive_intent_question_requires_confirmation():
    result = _classify_risk_intent(None, "删除 security user_allowlist 第 0 条")
    question = _build_destructive_intent_question("删除 security user_allowlist 第 0 条", result)

    assert "确认继续" in question
    assert "只查看" in question


def test_pending_confirmation_consumes_known_answers():
    store = get_confirmation_store()
    store.clear("conv-test")
    pending = store.create(
        conversation_id="conv-test",
        original_message="删除 security user_allowlist 第 0 条",
        classification=_classify_risk_intent(None, "删除 security user_allowlist 第 0 条").to_dict(),
        request_id="req-test",
    )

    decision, consumed = store.consume("conv-test", "确认继续")

    assert decision == ConfirmationDecision.CONFIRM
    assert consumed is pending
    assert store.get("conv-test") is None


def test_working_facts_extracts_maple_code():
    facts = extract_working_facts("测试代号是 Maple-42", source_turn=20)
    rendered = format_working_facts(facts)

    assert facts["test_code"]["value"] == "Maple-42"
    assert "Maple-42" in rendered


def test_loop_budget_guard_exit_reasons():
    guard = LoopBudgetGuard(max_total_tool_calls=1)
    decision = guard.record_tool_calls([{"name": "read_file"}, {"name": "grep"}])

    assert decision.should_stop
    assert decision.exit_reason == "tool_budget_exceeded"
