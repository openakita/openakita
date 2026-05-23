"""M1 W3 Stage 5 — industry-overrides HTTP layer.

Two endpoints:

* ``GET /api/plugins/finance-auto/industries`` — list every shipped
  overlay with its metadata (the front-end uses this to populate the
  industry picker on the "create org" dialog).
* ``GET /api/plugins/finance-auto/orgs/{org_id}/effective-config`` —
  shows the deep-merged configuration that will actually drive that
  org's report generation / aux-mode behaviour, so the accountant can
  audit *why* a particular default was chosen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter

from .config.industry_loader import (
    effective_config,
    list_industries,
    load_overlay,
    merge_manual_input_presets,
)
from .config.manual_inputs_loader import cash_flow_aux_presets

if TYPE_CHECKING:
    from .routes import FinanceAutoService


def register_industry_endpoints(
    router: APIRouter, service: "FinanceAutoService"
) -> None:

    @router.get(
        "/industries",
        summary="列出所有可用行业覆盖 (W3 Stage 5)",
    )
    async def list_industry_overlays() -> dict[str, Any]:
        # ``general`` is always available even when no YAML exists -- it
        # represents "no overlay, use base defaults".
        items = list_industries()
        if not any(i["industry"] == "general" for i in items):
            items.insert(0, {
                "industry": "general",
                "label": "通用 (无行业覆盖)",
                "description": "默认无覆盖，使用全局基线配置。",
                "path": "",
                "overlay_keys": [],
            })
        return {"items": items, "total": len(items)}

    @router.get(
        "/orgs/{org_id}/effective-config",
        summary="查看该账套合并后的有效配置 (W3 Stage 5)",
    )
    async def get_effective_config(org_id: str) -> dict[str, Any]:
        org = await service.get_org(org_id)
        base = {
            "org_defaults": {
                "aux_mode": org.aux_mode,
                "industry": org.industry,
                "standard": org.standard,
            },
            "manual_inputs_overlay": [],
        }
        effective = effective_config(base=base, industry=org.industry)
        overlay = load_overlay(org.industry)
        # Compute the merged manual-input preset list so the UI can show
        # exactly what slots the cash-flow drawer will surface.
        merged_presets = merge_manual_input_presets(
            cash_flow_aux_presets(), industry=org.industry,
        )
        return {
            "org_id": org_id,
            "industry": org.industry,
            "overlay_loaded": bool(overlay),
            "overlay_keys": list(overlay.keys()) if overlay else [],
            "effective": effective,
            "manual_input_slots_after_overlay": [
                p.model_dump() for p in merged_presets
            ],
        }
