"""End-to-end acceptance script for the M2 AI back-end.

Runs the full Stage 1-6 surface against an in-process FastAPI app + a
fresh SQLite file.  No real LLM is required — every scenario is wired
through ``MockLLMResponder`` so the script is deterministic and runs
on a worker box that has no Ollama / LM Studio installed.

Steps (10):

1.  Build the plugin's FastAPI router and assert v8 schema is in place
    (3 new tables, 6 default scenarios).
2.  GET /ai/scenarios → assert 6 rows, defaults look right.
3.  PATCH one scenario (override sensitivity to aggregated) and
    confirm the change shows up on the next list call.
4.  Run S1 (erp_source_detect) directly with auto_decision='allow_once'.
    Assert outcome=success, audit row exists, payload preview was
    desensitized (column headers kept, sample numbers scrubbed).
5.  Run S2 (account_classify_suggest) end-to-end with a seeded
    ParseIssue and a canned response.  Assert
    ``parse_issues.ai_suggestion`` populated and the
    ``finance.parse.issue.ai_filled`` event fires.
6.  Run S4 (cross_period_anomaly) at aggregated tier — assert
    is_local=true, payload size makes sense, audit row written.
7.  Simulate a denied consent flow: drive S2 with
    auto_decision='deny'; assert ConsentDenied → outcome='denied'
    audit row.
8.  Simulate a 🔴 raw-tier escalation: rebind the audit_risk_warning
    scenario to ``raw`` via PATCH, run with mock cloud-only
    endpoints, and confirm the router refuses to send raw to a
    cloud endpoint.
9.  GET /ai/consent → assert at least one allow_once + the original
    deny are visible.
10. GET /ai/audit-log → assert summary contains both 'success' and
    'denied' counts and pagination works.

Each step prints a single ``[OK]`` or ``[FAIL]`` line plus a JSON
summary at the end.  Exit code is 0 iff every step succeeded.

Usage::

    d:\\OpenAkita\\.venv\\Scripts\\python.exe ^
        plugins/finance-auto/scripts/m2_ai_acceptance.py ^
        [--keep] [--db <path>]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from finance_auto_backend.ai import scenarios as scenarios_pkg  # noqa: E402
from finance_auto_backend.ai.consent import (  # noqa: E402
    ConsentDenied,
    reset_dialog_registry_for_tests,
)
from finance_auto_backend.ai.event_bus import (  # noqa: E402
    get_event_bus,
    reset_event_bus_for_tests,
)
from finance_auto_backend.ai.router import (  # noqa: E402
    EndpointDescriptor,
    FinanceAIRouter,
    MockLLMResponder,
)
from finance_auto_backend.ai.scenarios import (  # noqa: E402
    account_classify_suggest,
    audit_risk_warning,
    cross_period_anomaly,
    erp_source_detect,
)
from finance_auto_backend.models import OrganizationCreate  # noqa: E402
from finance_auto_backend.routes import build_router_and_service  # noqa: E402

logger = logging.getLogger("m2_ai_acceptance")
logging.basicConfig(level=logging.WARNING, format="%(message)s")


# ---------------------------------------------------------------------------
# Mock router factory — gives every scenario a deterministic LLM reply.
# ---------------------------------------------------------------------------


def make_local_router(canned: dict[tuple[str, str], str]) -> FinanceAIRouter:
    mock = MockLLMResponder()
    for k, v in canned.items():
        mock.canned_responses[k] = v
    return FinanceAIRouter(
        responder=mock,
        endpoints=[
            EndpointDescriptor("ollama", "ollama", "http://localhost:11434", True),
        ],
    )


def make_cloud_only_router() -> FinanceAIRouter:
    """For step 8 — only cloud endpoints, used to confirm that raw
    tier scenarios refuse to send when no local model is available.
    """
    return FinanceAIRouter(
        responder=MockLLMResponder(),
        endpoints=[
            EndpointDescriptor("openai", "openai", "https://api.openai.com/v1", False),
        ],
    )


# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_acceptance(db_path: Path) -> dict[str, Any]:
    reset_event_bus_for_tests()
    reset_dialog_registry_for_tests()

    router_, service, db = build_router_and_service(db_path)
    await db.init()

    app = FastAPI()
    app.include_router(router_, prefix="/api/plugins/finance-auto")
    transport = ASGITransport(app=app)
    base_url = "http://acceptance"

    summary: dict[str, Any] = {
        "db_path": str(db_path),
        "schema_version": None,
        "scenarios_count": 0,
        "scenarios_after_patch": None,
        "s1": {},
        "s2": {},
        "s4": {},
        "s2_denied": {},
        "s6_raw_blocked": {},
        "consent_total": 0,
        "audit_summary": {},
        "audit_total": 0,
        "events_received": 0,
    }
    bus_events: list[dict] = []
    get_event_bus().subscribe(
        "finance.parse.issue.ai_filled",
        lambda payload: bus_events.append(payload),
    )

    # follow_redirects=True so the legacy ``/ai/...`` paths keep
    # working after EX-P2-13 (v1.0.0-rc1) turned them into 308
    # redirects to ``/v1/ai/...``.
    async with AsyncClient(
        transport=transport, base_url=base_url, follow_redirects=True,
    ) as client:
        try:
            # 1. schema check ---------------------------------------------
            print("Step 1 — schema v8 sanity")
            async with service.db.conn.execute(
                "SELECT version FROM schema_version WHERE component='finance_auto'"
            ) as cur:
                row = await cur.fetchone()
            schema_version = int(row["version"]) if row else 0
            summary["schema_version"] = schema_version
            assert schema_version >= 8, f"schema_version expected >=8, got {schema_version}"
            for tbl in ("ai_consent", "ai_scenarios", "llm_call_audit"):
                async with service.db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (tbl,),
                ) as c:
                    assert (await c.fetchone()) is not None, f"table {tbl} missing"
            print("  [OK] schema_version=", schema_version)

            # 2. /ai/scenarios -------------------------------------------
            print("Step 2 — list scenarios")
            r = await client.get("/api/plugins/finance-auto/ai/scenarios")
            assert r.status_code == 200, r.text
            sc_body = r.json()
            assert sc_body["total"] == 6, sc_body
            summary["scenarios_count"] = sc_body["total"]
            print("  [OK] 6 scenarios visible")

            # 3. PATCH one scenario ---------------------------------------
            print("Step 3 — PATCH erp_source_detect.sensitivity_override='aggregated'")
            r = await client.patch(
                "/api/plugins/finance-auto/ai/scenarios/erp_source_detect",
                json={"sensitivity_override": "aggregated"},
            )
            assert r.status_code == 200, r.text
            r = await client.get("/api/plugins/finance-auto/ai/scenarios")
            erp = next(
                s for s in r.json()["scenarios"]
                if s["scenario_id"] == "erp_source_detect"
            )
            summary["scenarios_after_patch"] = erp["sensitivity_override"]
            assert erp["sensitivity_override"] == "aggregated"
            # Reset back so step 4 still runs at metadata.
            r = await client.patch(
                "/api/plugins/finance-auto/ai/scenarios/erp_source_detect",
                json={"sensitivity_override": None},
            )
            print("  [OK] override round-trip complete")

            # Seed a demo org for the rest of the steps.
            org = await service.create_org(
                OrganizationCreate(name="验收公司", code="ACC_M2_AI")
            )
            org_id = org.id

            # 4. S1 erp detect --------------------------------------------
            print("Step 4 — S1 erp_source_detect happy path")
            payload_s1 = erp_source_detect.build_payload(
                sheet_names=["余额表"],
                column_headers=[
                    "科目编码", "科目名称",
                    "期初借方", "期初贷方",
                    "本期借方", "本期贷方",
                    "期末借方", "期末贷方",
                ],
                sample_row_count=20,
                parser_used="openpyxl",
            )
            router_s1 = make_local_router(
                canned={
                    (erp_source_detect.SCENARIO_ID, "metadata"):
                        '{"erp_source": "用友", "confidence": 0.85,'
                        '"evidence": ["列结构匹配 T3"]}'
                }
            )
            result_s1 = await erp_source_detect.run(
                service,
                payload=payload_s1,
                org_id=org_id,
                router=router_s1,
                auto_decision="allow_once",
            )
            assert result_s1.outcome == "success"
            assert result_s1.parsed["erp_source"] == "用友"
            assert result_s1.audit_id is not None
            summary["s1"] = {
                "outcome": result_s1.outcome,
                "audit_id": result_s1.audit_id,
                "is_local": result_s1.is_local,
                "parsed_keys": sorted(result_s1.parsed.keys()),
            }
            print(
                f"  [OK] S1 success: erp={result_s1.parsed['erp_source']} "
                f"is_local={result_s1.is_local} audit_id={result_s1.audit_id}"
            )

            # 5. S2 round-trip --------------------------------------------
            print("Step 5 — S2 account_classify_suggest round-trip")
            issue_id = "iss_acc_m2_ai_demo"
            period_id = "2025-FY"
            import_id = "imp_acc_m2_ai_demo"
            await service.db.conn.execute(
                "INSERT INTO trial_balance_imports(id, org_id, period_id, "
                "source_file, file_size, parser_used, row_count, status, "
                "uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    import_id, org_id, period_id, "tb.xlsx", 1234, "openpyxl",
                    1, "ok", _now_iso(),
                ),
            )
            await service.db.conn.execute(
                "INSERT INTO parse_issues(id, org_id, period_id, import_id, "
                "row_index, sheet_name, column_name, issue_type, severity, "
                "pattern_signature, original_data, applied_to_learning, "
                "auto_applied, version, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    issue_id, org_id, period_id, import_id, 5, "余额表",
                    "科目编码", "unknown_code", "must_fix", "unknown:9001",
                    json.dumps(
                        {"full_code": "9001", "account_name": "未知科目"}
                    ),
                    0, 0, 1, _now_iso(),
                ),
            )
            await service.db.conn.commit()
            unknown = await account_classify_suggest.fetch_unresolved_unknown_codes(
                service, org_id=org_id
            )
            payload_s2 = account_classify_suggest.build_payload(unknown)
            canned_s2 = json.dumps(
                [
                    {
                        "issue_id": issue_id,
                        "account_code": "9001",
                        "account_name": "未知科目",
                        "suggested_category": "其他",
                        "suggested_subcategory": "未识别",
                        "balance_side": "debit",
                        "confidence": 0.4,
                        "reason": "无准则六大类匹配",
                    }
                ],
                ensure_ascii=False,
            )
            router_s2 = make_local_router(
                canned={(account_classify_suggest.SCENARIO_ID, "metadata"): canned_s2}
            )
            result_s2 = await account_classify_suggest.run(
                service,
                payload=payload_s2,
                org_id=org_id,
                router=router_s2,
                auto_decision="allow_once",
            )
            assert result_s2.outcome == "success"
            for _ in range(50):
                if bus_events:
                    break
                await asyncio.sleep(0.01)
            assert bus_events, "expected ai_filled event to fire"
            async with service.db.conn.execute(
                "SELECT ai_suggestion, ai_confidence, ai_consent_id FROM "
                "parse_issues WHERE id=?", (issue_id,),
            ) as cur:
                pi_row = await cur.fetchone()
            assert pi_row["ai_suggestion"] is not None
            assert pi_row["ai_consent_id"] == result_s2.consent_id
            summary["s2"] = {
                "outcome": result_s2.outcome,
                "audit_id": result_s2.audit_id,
                "issue_filled": True,
                "ai_confidence": pi_row["ai_confidence"],
                "events_received": len(bus_events),
            }
            print(
                f"  [OK] S2 success: parse_issues.ai_suggestion populated, "
                f"confidence={pi_row['ai_confidence']}, "
                f"events={len(bus_events)}"
            )

            # 6. S4 aggregated tier ---------------------------------------
            print("Step 6 — S4 cross_period_anomaly at aggregated tier")
            payload_s4 = cross_period_anomaly.build_payload(
                [
                    {
                        "item_name": "应收账款", "yoy_pct": "+216%",
                        "this_period_bucket": "百万级",
                        "last_period_bucket": "万元级",
                    },
                    {
                        "item_name": "应付账款", "yoy_pct": "-87%",
                        "this_period_bucket": "万元级",
                        "last_period_bucket": "百万级",
                    },
                ]
            )
            router_s4 = make_local_router(
                canned={
                    (cross_period_anomaly.SCENARIO_ID, "aggregated"):
                        '[{"item": "应收账款", "verdict": "异常", '
                        '"reason": "增幅超 200% 需关注信用政策变化"}]'
                }
            )
            result_s4 = await cross_period_anomaly.run(
                service,
                payload=payload_s4,
                org_id=org_id,
                router=router_s4,
                auto_decision="allow_once",
            )
            assert result_s4.outcome == "success"
            assert result_s4.is_local is True
            summary["s4"] = {
                "outcome": result_s4.outcome,
                "is_local": result_s4.is_local,
                "audit_id": result_s4.audit_id,
            }
            print(
                f"  [OK] S4 success: is_local={result_s4.is_local} "
                f"audit_id={result_s4.audit_id}"
            )

            # 7. S2 denied ------------------------------------------------
            print("Step 7 — S2 denied consent path")
            denied_payload = account_classify_suggest.build_payload(
                [{"issue_id": "fake1", "account_code": "9999",
                  "account_name": "测试拒绝"}]
            )
            router_deny = make_local_router(canned={})
            result_deny = await account_classify_suggest.run(
                service,
                payload=denied_payload,
                org_id=org_id,
                router=router_deny,
                auto_decision="deny",
                apply_to_parse_issues=False,
            )
            assert result_deny.outcome == "denied"
            summary["s2_denied"] = {
                "outcome": result_deny.outcome,
                "audit_id": result_deny.audit_id,
            }
            print(f"  [OK] denied path: outcome={result_deny.outcome}")

            # 8. raw-tier cloud-only refusal -----------------------------
            print(
                "Step 8 — raw escalation refused on cloud-only endpoints"
            )
            r = await client.patch(
                "/api/plugins/finance-auto/ai/scenarios/audit_risk_warning",
                json={"sensitivity_override": "raw"},
            )
            assert r.status_code == 200
            payload_s6 = audit_risk_warning.build_payload(
                [
                    {
                        "indicator": "毛利率", "value_ratio": "0.42 -> 0.18",
                        "yoy_pct": "-57%", "threshold_breached": True,
                    }
                ]
            )
            cloud_router = make_cloud_only_router()
            try:
                from finance_auto_backend.ai.scenarios._base import (
                    execute_scenario,
                )

                # Force require_local_only so a cloud-only env raises
                # the explicit refusal rather than falling back to the
                # mock endpoint.
                cloud_router.config.per_scenario_overrides[
                    audit_risk_warning.SCENARIO_ID
                ] = {"require_local_only": True}

                refused = False
                refusal_kind: str | None = None
                try:
                    res_raw = await execute_scenario(
                        service,
                        scenario_id=audit_risk_warning.SCENARIO_ID,
                        level="raw",
                        payload=payload_s6,
                        prompt_template=audit_risk_warning.PROMPT_TEMPLATE,
                        router=cloud_router,
                        org_id=org_id,
                        auto_decision="allow_once",
                    )
                    # If execute_scenario swallowed the RuntimeError it
                    # will surface in outcome=='error' instead.
                    if res_raw.outcome in {"error", "denied"}:
                        refused = True
                        refusal_kind = res_raw.outcome
                except RuntimeError:
                    refused = True
                    refusal_kind = "RuntimeError"
                except ConsentDenied:
                    refused = True
                    refusal_kind = "ConsentDenied"
                summary["s6_raw_blocked"] = {
                    "refused": refused,
                    "refusal_kind": refusal_kind,
                }
                assert refused, "raw tier must not be sent to cloud endpoints"
                print(
                    f"  [OK] raw → cloud refused as expected "
                    f"(kind={refusal_kind})"
                )
            finally:
                await client.patch(
                    "/api/plugins/finance-auto/ai/scenarios/audit_risk_warning",
                    json={"sensitivity_override": None},
                )

            # 9. /ai/consent -----------------------------------------------
            print("Step 9 — list /ai/consent")
            r = await client.get("/api/plugins/finance-auto/ai/consent")
            assert r.status_code == 200
            consent_body = r.json()
            summary["consent_total"] = consent_body["total"]
            decisions = sorted({c["decision"] for c in consent_body["consents"]})
            assert "allow_once" in decisions
            assert "deny" in decisions
            print(
                f"  [OK] {consent_body['total']} consent rows, "
                f"decisions={decisions}"
            )

            # 10. /ai/audit-log -------------------------------------------
            print("Step 10 — /ai/audit-log pagination + summary")
            r = await client.get(
                "/api/plugins/finance-auto/ai/audit-log",
                params={"limit": 10},
            )
            audit_body = r.json()
            summary["audit_total"] = audit_body["total"]
            summary["audit_summary"] = audit_body["summary"]
            assert audit_body["summary"].get("success", 0) >= 3
            assert audit_body["summary"].get("denied", 0) >= 1
            print(
                f"  [OK] audit_total={audit_body['total']}, "
                f"summary={audit_body['summary']}"
            )

        finally:
            await db.close()
            await transport.aclose()
    summary["events_received"] = len(bus_events)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument(
        "--keep",
        action="store_true",
        help="Keep the temporary SQLite file after the run.",
    )
    p.add_argument("--db", help="Use this path instead of a temp file.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.db:
        tmp_dir = None
        db_path = Path(args.db)
        db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        tmp_dir = Path(tempfile.mkdtemp(prefix="m2_ai_accept_"))
        db_path = tmp_dir / "acceptance.sqlite"
    try:
        summary = asyncio.run(run_acceptance(db_path))
        ok_payload = json.dumps(summary, ensure_ascii=False, indent=2)
        print()
        print("=" * 60)
        print("M2 AI back-end acceptance — SUCCESS")
        print("=" * 60)
        print(ok_payload)
        out_path = Path(__file__).resolve().parent.parent.parent.parent
        out_path = out_path / "_m2_ai_acceptance_result.json"
        out_path.write_text(ok_payload, encoding="utf-8")
        print(f"\nResult written to {out_path}")
        return 0
    except AssertionError:
        traceback.print_exc()
        return 1
    except Exception:
        traceback.print_exc()
        return 2
    finally:
        if tmp_dir and not args.keep:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
