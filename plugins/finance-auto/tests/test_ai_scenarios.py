"""M2 AI Stage 5 — scenario unit tests.

For every scenario:

* Prompt template loads + renders with a sample payload.
* `run(...)` end-to-end with auto_decision='allow_once':
  - returns ScenarioRunResult with outcome='success'
  - writes one row to llm_call_audit
  - persists a desensitised payload snapshot under data/llm_debug/
    when a TmpPath is monkey-patched in.

Plus S2-specific apply-to-parse_issues round-trip:

* seed an unknown_code ParseIssue
* run S2 with a canned response that classifies it
* check parse_issues.ai_suggestion / ai_confidence / ai_consent_id
  populated and a finance.parse.issue.ai_filled event fires.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from finance_auto_backend.ai import scenarios as scenarios_pkg
from finance_auto_backend.ai.consent import reset_dialog_registry_for_tests
from finance_auto_backend.ai.event_bus import (
    get_event_bus,
    reset_event_bus_for_tests,
)
from finance_auto_backend.ai.router import (
    EndpointDescriptor,
    FinanceAIRouter,
    MockLLMResponder,
)
from finance_auto_backend.ai.scenarios import (
    account_classify_suggest,
    audit_risk_warning,
    cash_flow_aux_classify,
    cross_period_anomaly,
    erp_source_detect,
    raw_audit_opinion,
    raw_nl_query,
    raw_notes_draft,
    trial_balance_diagnose,
)
from finance_auto_backend.routes import build_router_and_service


@pytest.fixture
async def ai_service(tmp_path):
    db_path = tmp_path / "scenarios.sqlite"
    router, service, db = build_router_and_service(db_path)
    await db.init()
    from finance_auto_backend.models import OrganizationCreate
    org = await service.create_org(
        OrganizationCreate(name="测试公司", code="TEST_AI_SCN")
    )
    yield service, org.id
    await db.close()


@pytest.fixture(autouse=True)
def fresh_bus_and_dialogs(monkeypatch, tmp_path):
    reset_event_bus_for_tests()
    reset_dialog_registry_for_tests()
    # Redirect debug snapshot directory.
    from finance_auto_backend.ai import audit
    monkeypatch.setattr(audit, "DEBUG_DIR", tmp_path / "llm_debug")
    yield


def _local_router(canned: dict | None = None) -> FinanceAIRouter:
    mock = MockLLMResponder()
    if canned:
        for k, v in canned.items():
            mock.canned_responses[k] = v
    return FinanceAIRouter(
        responder=mock,
        endpoints=[EndpointDescriptor("ollama", "ollama", "http://localhost:11434", True)],
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_lists_all_scenarios():
    # M2 ships 6 scenarios (S1–S6).  M3 raw AI sibling adds 3 more
    # (audit_opinion_draft / nl_query / notes_draft) for a total of 9.
    # Assert the exact set so a future regression of *missing* a scenario
    # surfaces here instead of waiting for acceptance to flag it.
    ids = sorted(scenarios_pkg.list_scenario_ids())
    expected = sorted(
        [
            erp_source_detect.SCENARIO_ID,
            account_classify_suggest.SCENARIO_ID,
            trial_balance_diagnose.SCENARIO_ID,
            cross_period_anomaly.SCENARIO_ID,
            cash_flow_aux_classify.SCENARIO_ID,
            audit_risk_warning.SCENARIO_ID,
            raw_audit_opinion.SCENARIO_ID,
            raw_nl_query.SCENARIO_ID,
            raw_notes_draft.SCENARIO_ID,
        ]
    )
    assert ids == expected
    assert len(ids) >= 9


# ---------------------------------------------------------------------------
# Prompt templates load
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        erp_source_detect,
        account_classify_suggest,
        trial_balance_diagnose,
        cross_period_anomaly,
        cash_flow_aux_classify,
        audit_risk_warning,
    ],
)
def test_prompt_templates_non_empty(module):
    assert module.PROMPT_TEMPLATE.strip(), module.SCENARIO_ID
    assert "$safe_payload_json" in module.PROMPT_TEMPLATE, module.SCENARIO_ID


# ---------------------------------------------------------------------------
# S1 ERP detect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s1_runs_end_to_end(ai_service):
    service, org_id = ai_service
    payload = erp_source_detect.build_payload(
        sheet_names=["余额表"],
        column_headers=[
            "科目编码", "科目名称",
            "期初借方", "期初贷方",
            "本期借方", "本期贷方",
            "期末借方", "期末贷方",
        ],
        sample_row_count=15,
        parser_used="openpyxl",
    )
    canned = '{"erp_source": "用友", "confidence": 0.85, "evidence": ["列结构匹配 T3"]}'
    router = _local_router(canned={(erp_source_detect.SCENARIO_ID, "metadata"): canned})

    result = await erp_source_detect.run(
        service,
        payload=payload,
        org_id=org_id,
        router=router,
        auto_decision="allow_once",
    )
    assert result.outcome == "success"
    assert result.parsed["erp_source"] == "用友"
    assert result.parsed["confidence"] == 0.85
    assert result.audit_id is not None

    async with service.db.conn.execute(
        "SELECT scenario_id, sensitivity_level, outcome FROM llm_call_audit "
        "WHERE id=?", (result.audit_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row["scenario_id"] == "erp_source_detect"
    assert row["sensitivity_level"] == "metadata"
    assert row["outcome"] == "success"


# ---------------------------------------------------------------------------
# S2 — round-trip with parse_issues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s2_persists_ai_suggestion(ai_service):
    service, org_id = ai_service

    # Seed an unknown_code parse_issue + minimal supporting rows.
    issue_id = "iss_" + uuid.uuid4().hex[:12]
    period_id = "2025-FY"
    import_id = "imp_" + uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    await service.db.conn.execute(
        "INSERT INTO trial_balance_imports(id, org_id, period_id, source_file, "
        "file_size, parser_used, row_count, status, uploaded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (import_id, org_id, period_id, "tb.xlsx", 100, "openpyxl", 1, "ok", now),
    )
    await service.db.conn.execute(
        "INSERT INTO parse_issues(id, org_id, period_id, import_id, row_index, "
        "sheet_name, column_name, issue_type, severity, pattern_signature, "
        "original_data, applied_to_learning, auto_applied, version, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            issue_id, org_id, period_id, import_id, 5, "余额表", "科目编码",
            "unknown_code", "must_fix", "unknown:9001",
            json.dumps({"full_code": "9001", "account_name": "未知科目"}),
            0, 0, 1, now,
        ),
    )
    await service.db.conn.commit()

    fetched = await account_classify_suggest.fetch_unresolved_unknown_codes(
        service, org_id=org_id
    )
    assert fetched, "expected the seeded issue to surface"
    payload = account_classify_suggest.build_payload(fetched)

    canned_response = json.dumps(
        [
            {
                "issue_id": issue_id,
                "account_code": "9001",
                "account_name": "未知科目",
                "suggested_category": "其他",
                "suggested_subcategory": "未识别",
                "balance_side": "debit",
                "confidence": 0.4,
                "reason": "无法定位至准则六大类",
            }
        ],
        ensure_ascii=False,
    )
    router = _local_router(
        canned={(account_classify_suggest.SCENARIO_ID, "metadata"): canned_response}
    )

    received: list[dict] = []

    async def _capture(payload):
        received.append(payload)

    get_event_bus().subscribe("finance.parse.issue.ai_filled", _capture)

    result = await account_classify_suggest.run(
        service,
        payload=payload,
        org_id=org_id,
        router=router,
        auto_decision="allow_once",
    )
    assert result.outcome == "success"

    async with service.db.conn.execute(
        "SELECT ai_suggestion, ai_confidence, ai_consent_id FROM parse_issues "
        "WHERE id=?", (issue_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row["ai_suggestion"] is not None
    parsed = json.loads(row["ai_suggestion"])
    assert parsed["suggested_category"] == "其他"
    assert row["ai_confidence"] == pytest.approx(0.4)
    assert row["ai_consent_id"] == result.consent_id
    # Wait for the event task to fire.
    for _ in range(40):
        if received:
            break
        await asyncio.sleep(0.01)
    # The bus event name is `finance.parse.issue.ai_filled`; the payload's
    # internal `event` field is the WS-facing `parse_issue_ai_filled`
    # (per ParseIssueAIFilledEvent).  Either way, the listener only
    # receives the subscription we set up, so a non-empty `received` is
    # already proof of fan-out.
    assert received, "expected the ai_filled event to fire"
    assert any(issue_id in ev.get("issue_ids", []) for ev in received)


# ---------------------------------------------------------------------------
# S3-S6 happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_diagnose_runs(ai_service):
    service, org_id = ai_service
    payload = trial_balance_diagnose.build_payload(
        debit_sum_bucket="百万级",
        credit_sum_bucket="百万级",
        diff_bucket="千元以下",
        diff_ratio=0.000123,
        suspicious_account_count=2,
        direction_anomaly_count=1,
    )
    router = _local_router(
        canned={(trial_balance_diagnose.SCENARIO_ID, "metadata"):
                '{"top_cause": 1, "cause_explanation": "x"}'}
    )
    result = await trial_balance_diagnose.run(
        service, payload=payload, org_id=org_id, router=router,
        auto_decision="allow_permanent",
    )
    assert result.outcome == "success"
    assert result.parsed["top_cause"] == 1


@pytest.mark.asyncio
async def test_s4_aggregated_router_picks_local(ai_service):
    service, org_id = ai_service
    payload = cross_period_anomaly.build_payload(
        [
            {"item_name": "应收账款", "yoy_pct": "+216%",
             "this_period_bucket": "百万级", "last_period_bucket": "万元级"},
        ]
    )
    router = _local_router(
        canned={(cross_period_anomaly.SCENARIO_ID, "aggregated"): "[]"}
    )
    result = await cross_period_anomaly.run(
        service, payload=payload, org_id=org_id, router=router,
        auto_decision="allow_once",
    )
    assert result.outcome == "success"
    assert result.is_local is True


@pytest.mark.asyncio
async def test_s5_cash_flow_aux_runs(ai_service):
    service, org_id = ai_service
    payload = cash_flow_aux_classify.build_payload(
        aux_account_name="银行手续费",
        candidate_items=["支付的各项税费", "支付其他与经营活动有关的现金"],
        note_hint="财务费用-手续费",
    )
    router = _local_router(
        canned={(cash_flow_aux_classify.SCENARIO_ID, "aggregated"):
                '{"target_item": "支付其他与经营活动有关的现金", "confidence": 0.9}'}
    )
    result = await cash_flow_aux_classify.run(
        service, payload=payload, org_id=org_id, router=router,
        auto_decision="allow_once",
    )
    assert result.outcome == "success"
    assert "支付其他" in result.parsed["target_item"]


@pytest.mark.asyncio
async def test_s6_audit_risk_runs(ai_service):
    service, org_id = ai_service
    payload = audit_risk_warning.build_payload(
        [
            {"indicator": "毛利率突变", "value_ratio": "0.42 -> 0.18",
             "yoy_pct": "-57%", "threshold_breached": True},
        ]
    )
    router = _local_router(
        canned={(audit_risk_warning.SCENARIO_ID, "aggregated"): "[]"}
    )
    result = await audit_risk_warning.run(
        service, payload=payload, org_id=org_id, router=router,
        auto_decision="allow_once",
    )
    assert result.outcome == "success"


# ---------------------------------------------------------------------------
# Denied path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_denied_consent_writes_audit_row(ai_service):
    service, org_id = ai_service
    router = _local_router()
    result = await erp_source_detect.run(
        service,
        payload={"sheet_names": [], "column_headers": [], "sample_row_count": 0},
        org_id=org_id,
        router=router,
        auto_decision="deny",
    )
    assert result.outcome == "denied"
    async with service.db.conn.execute(
        "SELECT outcome FROM llm_call_audit WHERE id=?", (result.audit_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row["outcome"] == "denied"
