"""S6 — 审计风险预警 (🟡 aggregated)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._base_paths import TEMPLATE_DIR
from ._base import ScenarioRunResult, execute_scenario

if TYPE_CHECKING:
    from ...routes import FinanceAutoService
    from ..router import FinanceAIRouter

SCENARIO_ID = "audit_risk_warning"
DEFAULT_LEVEL = "aggregated"
PROMPT_TEMPLATE = (TEMPLATE_DIR / "audit_risk_warning.md.j2").read_text(encoding="utf-8")


def build_payload(indicators: list[dict[str, Any]]) -> dict[str, Any]:
    """Each indicator: ``{"indicator": str, "value_ratio": str|float,
    "yoy_pct": str|float, "threshold_breached": bool}`` — already
    aggregated, no raw amounts.
    """
    return {
        "indicators": [
            {
                "indicator": str(it.get("indicator") or ""),
                "value_ratio": str(it.get("value_ratio") or ""),
                "yoy_pct": str(it.get("yoy_pct") or ""),
                "threshold_breached": bool(it.get("threshold_breached", False)),
            }
            for it in indicators
        ]
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
