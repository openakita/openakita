"""S5 — 现金流量表辅助科目归类 (🟡 aggregated)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._base_paths import TEMPLATE_DIR
from ._base import ScenarioRunResult, execute_scenario

if TYPE_CHECKING:
    from ...routes import FinanceAutoService
    from ..router import FinanceAIRouter

SCENARIO_ID = "cash_flow_aux_classify"
DEFAULT_LEVEL = "aggregated"
PROMPT_TEMPLATE = (TEMPLATE_DIR / "cash_flow_aux_classify.md.j2").read_text(encoding="utf-8")


def build_payload(
    *,
    aux_account_name: str,
    candidate_items: list[str],
    note_hint: str | None = None,
) -> dict[str, Any]:
    return {
        "aux_account_name": aux_account_name,
        "candidate_items": list(candidate_items),
        "note_hint": note_hint or "",
    }


async def run(
    service: FinanceAutoService,
    *,
    payload: dict,
    org_id: str | None = None,
    router: FinanceAIRouter | None = None,
    auto_decision: str | None = None,
) -> ScenarioRunResult:
    return await execute_scenario(
        service,
        scenario_id=SCENARIO_ID,
        level=DEFAULT_LEVEL,
        payload=payload,
        prompt_template=PROMPT_TEMPLATE,
        router=router,
        org_id=org_id,
        auto_decision=auto_decision,
    )


__all__ = ["DEFAULT_LEVEL", "PROMPT_TEMPLATE", "SCENARIO_ID", "build_payload", "run"]
