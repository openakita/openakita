"""S4 — 跨期波动异常分析 (🟡 aggregated)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._base_paths import TEMPLATE_DIR
from ._base import ScenarioRunResult, execute_scenario

if TYPE_CHECKING:
    from ...routes import FinanceAutoService
    from ..router import FinanceAIRouter

SCENARIO_ID = "cross_period_anomaly"
DEFAULT_LEVEL = "aggregated"
PROMPT_TEMPLATE = (TEMPLATE_DIR / "cross_period_anomaly.md.j2").read_text(encoding="utf-8")


def build_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Each item should already be aggregated (item name + yoy %).

    The desensitizer will bucket any stray amount fields that slip
    through, but we expect the caller to send only ratios + names.
    """
    return {
        "items": [
            {
                "item_name": str(it.get("item_name") or it.get("name") or ""),
                "yoy_pct": str(it.get("yoy_pct") or it.get("delta_pct") or ""),
                "this_period_bucket": str(it.get("this_period_bucket") or ""),
                "last_period_bucket": str(it.get("last_period_bucket") or ""),
            }
            for it in items
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
