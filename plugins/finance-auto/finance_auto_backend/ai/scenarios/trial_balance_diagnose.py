"""S3 — 试算平衡失败诊断 (🟢 metadata)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .._base_paths import TEMPLATE_DIR
from ._base import ScenarioRunResult, execute_scenario

if TYPE_CHECKING:
    from ...routes import FinanceAutoService
    from ..router import FinanceAIRouter

SCENARIO_ID = "trial_balance_diagnose"
DEFAULT_LEVEL = "metadata"
PROMPT_TEMPLATE = (TEMPLATE_DIR / "trial_balance_diagnose.md.j2").read_text(
    encoding="utf-8"
)


def build_payload(
    *,
    debit_sum_bucket: str,
    credit_sum_bucket: str,
    diff_bucket: str,
    diff_ratio: float,
    suspicious_account_count: int,
    direction_anomaly_count: int,
) -> dict[str, Any]:
    return {
        "debit_total": debit_sum_bucket,
        "credit_total": credit_sum_bucket,
        "delta_magnitude": diff_bucket,
        "delta_ratio": f"{diff_ratio:.6f}",
        "suspicious_accounts": int(suspicious_account_count),
        "direction_anomaly_count": int(direction_anomaly_count),
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
