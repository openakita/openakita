"""S1 — ERP 来源识别 (🟢 metadata).

Inputs: column-headers + sample row schema from a freshly uploaded
balance file.  Output: JSON guess of the ERP source + confidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._base_paths import TEMPLATE_DIR
from ._base import ScenarioRunResult, execute_scenario

if TYPE_CHECKING:
    from ...routes import FinanceAutoService
    from ..router import FinanceAIRouter

SCENARIO_ID = "erp_source_detect"
DEFAULT_LEVEL = "metadata"

_TEMPLATE_PATH = TEMPLATE_DIR / "erp_source_detect.md.j2"

PROMPT_TEMPLATE = _TEMPLATE_PATH.read_text(encoding="utf-8")


def build_payload(
    *,
    sheet_names: list[str],
    column_headers: list[str],
    sample_row_count: int,
    parser_used: str | None = None,
) -> dict[str, Any]:
    """Build the metadata-level payload.

    Numbers / dates are deliberately excluded — desensitize at metadata
    will scrub them anyway, but doing it at the boundary makes the
    intent obvious to readers + reviewers.
    """
    return {
        "sheet_names": list(sheet_names),
        "column_headers": list(column_headers),
        "sample_row_count": int(sample_row_count),
        "parser_used": parser_used or "",
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
