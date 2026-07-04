"""M2 AI scenarios — one module per S1-S6.

Each scenario module exposes:

* ``SCENARIO_ID``       (str)   — must match an ``ai_scenarios.scenario_id``.
* ``DEFAULT_LEVEL``     (SensitivityLevel) — default sensitivity tier.
* ``PROMPT_TEMPLATE``   (str)   — Jinja2-friendly prompt template (kept as
                                  a module constant so unit tests can
                                  diff/patch it without touching disk).
* ``run(...)``          (coro)  — orchestrator that calls desensitize →
                                  check_consent → router.complete →
                                  parse_response → record audit row.

Importing this package side-effect-loads every scenario into a
registry keyed by ``SCENARIO_ID`` so the API + acceptance script can
look them up by id.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from . import (
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

ScenarioRunner = Callable[..., Awaitable[dict]]

# Registry keyed by scenario_id — keeps the lookup explicit so a typo
# in a string id doesn't silently fall through to the wrong scenario.
SCENARIO_REGISTRY: dict[str, ScenarioRunner] = {
    erp_source_detect.SCENARIO_ID: erp_source_detect.run,
    account_classify_suggest.SCENARIO_ID: account_classify_suggest.run,
    trial_balance_diagnose.SCENARIO_ID: trial_balance_diagnose.run,
    cross_period_anomaly.SCENARIO_ID: cross_period_anomaly.run,
    cash_flow_aux_classify.SCENARIO_ID: cash_flow_aux_classify.run,
    audit_risk_warning.SCENARIO_ID: audit_risk_warning.run,
    # M3 raw scenarios (S6 audit opinion / S7 NL query / S11 notes).
    raw_audit_opinion.SCENARIO_ID: raw_audit_opinion.run,
    raw_nl_query.SCENARIO_ID: raw_nl_query.run,
    raw_notes_draft.SCENARIO_ID: raw_notes_draft.run,
}


def list_scenario_ids() -> list[str]:
    return list(SCENARIO_REGISTRY.keys())


__all__ = [
    "SCENARIO_REGISTRY",
    "ScenarioRunner",
    "account_classify_suggest",
    "audit_risk_warning",
    "cash_flow_aux_classify",
    "cross_period_anomaly",
    "erp_source_detect",
    "list_scenario_ids",
    "raw_audit_opinion",
    "raw_nl_query",
    "raw_notes_draft",
    "trial_balance_diagnose",
]
