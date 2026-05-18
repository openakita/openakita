"""V2 organisation API facade.

This route module exposes the new :mod:`openakita.runtime` stack
(``runtime/templates`` for now; ``runtime/supervisor`` and
``runtime/messenger`` once Phase 6 wires the per-org executor) over
HTTP. It is a *parallel* surface to ``api/routes/orgs.py``; the
legacy v1 routes keep running. A request only ever reaches v2 when
``settings.runtime_v2_enabled`` is true — otherwise the router
returns ``404 /api/v2/...``.

Why a separate module instead of adding endpoints to ``orgs.py``:

* Phase 6 of the revamp plan calls for a clean facade swap behind a
  feature flag. Mixing the two sets of routes inside one 91k-line
  file would make Phase 8 deletion mechanical *only* for parts that
  are 100% v1 — the entanglement would force us to read and split
  code we want to drop wholesale. A standalone module is a single
  atomic delete in Phase 8 if we ever need to revert.
* The v2 surface is intentionally narrower (no avatars, no agent
  profiles, no positional layout fields). Keeping it in its own
  file makes the contract obvious — readers do not have to grep
  through legacy fields to know what the v2 wire format is.

Endpoints (all gated by ``runtime_v2_enabled``):

``GET  /api/v2/orgs/templates``                  list TemplateSpec records
``GET  /api/v2/orgs/templates/{id}``              one TemplateSpec
``POST /api/v2/orgs/templates/{id}/instantiate``  -> fresh OrgV2

The instantiate endpoint never persists. Phase 7 adds a
``/api/v2/orgs`` resource with full CRUD on top of the
``runtime/checkpoint`` store; for now the route lets the frontend
preview a template (e.g. for the "create org" wizard) and let the
caller decide where to persist the result.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from openakita.config import settings
from openakita.runtime.templates import (
    GLOBAL_REGISTRY,
    TemplateValidationError,
    collect_builtin_factories,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/orgs", tags=["v2:组织编排"])


# ---------------------------------------------------------------------------
# Lazy registry bootstrap
# ---------------------------------------------------------------------------


_BOOTSTRAPPED: bool = False


def _ensure_registry_bootstrapped() -> None:
    """Populate the global registry from the builtin package.

    We bootstrap lazily — on the first request rather than at import
    time — so that toggling ``runtime_v2_enabled`` off keeps the
    runtime/templates package side-effect-free for the rest of the
    application. Subsequent calls short-circuit on the
    ``_BOOTSTRAPPED`` latch.

    We use :func:`collect_builtin_factories` rather than the
    ``discover_builtins() + GLOBAL_REGISTRY.bootstrap()`` pair
    because the latter relies on a process-global pending queue
    that test fixtures sometimes monkeypatch. Walking the package
    via the survivable ``TEMPLATE_FACTORY_MARK`` attribute is a
    superset operation: it always finds every ``@template``-marked
    factory, regardless of whether the queue has already been
    drained earlier in this process's lifetime.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    factories = collect_builtin_factories()
    registered = 0
    for factory in factories:
        spec = factory()
        if spec.id in GLOBAL_REGISTRY:
            continue  # idempotent — fine if another path already registered it
        GLOBAL_REGISTRY.register(spec)
        registered += 1
    _BOOTSTRAPPED = True
    logger.info(
        "[orgs_v2] registry bootstrapped: %d template(s) registered "
        "(%d total in registry)",
        registered,
        len(GLOBAL_REGISTRY),
    )


def _require_v2_enabled() -> None:
    """Refuse the request if the v2 feature flag is off.

    We map "off" to 404 rather than 503 so a client probing for v2
    cannot fingerprint whether the v2 code is even installed; the
    UI flips between v1 / v2 paths based on the same flag, so this
    behaviour is enough for the canary deploy.
    """
    if not settings.runtime_v2_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="runtime v2 is disabled (settings.runtime_v2_enabled=False)",
        )


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class _InstantiateBody(BaseModel):
    """POST body for :func:`instantiate_template`.

    The override surface mirrors :meth:`TemplateRegistry.instantiate`
    exactly. We intentionally avoid arbitrary kwargs — every
    accepted key is whitelisted here so the route is a stable public
    contract.
    """

    name: str = Field(..., min_length=1, description="Display name for the new organisation.")
    description: str | None = Field(
        default=None,
        description="Override the template description; null means inherit from the template.",
    )
    defaults: dict[str, Any] | None = Field(
        default=None,
        description="Optional overrides to merge into DefaultsSpec.",
    )
    node_persona_prompts: dict[str, str] | None = Field(
        default=None,
        description="Per-NodeSpec.id persona prompt overrides.",
    )
    node_runtime_overrides: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="Per-NodeSpec.id NodeRuntimeOverrides patches.",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/templates", summary="List v2 organisation templates")
def list_templates() -> dict[str, Any]:
    """Return every registered :class:`TemplateSpec` in JSONable form.

    Wrapped in a ``{templates: [...], count: N}`` envelope so future
    additions (pagination, filtering, server-time) do not break
    older clients.
    """
    _require_v2_enabled()
    _ensure_registry_bootstrapped()
    items = [spec.to_jsonable() for spec in GLOBAL_REGISTRY.list()]
    return {"templates": items, "count": len(items)}


@router.get(
    "/templates/{template_id}",
    summary="Get a single v2 organisation template",
)
def get_template(template_id: str) -> dict[str, Any]:
    """Return one :class:`TemplateSpec` in JSONable form.

    Returns 404 if the id is unknown — symmetric with FastAPI
    convention and easier for the editor to handle than a 422.
    """
    _require_v2_enabled()
    _ensure_registry_bootstrapped()
    try:
        spec = GLOBAL_REGISTRY.get(template_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return spec.to_jsonable()


@router.post(
    "/templates/{template_id}/instantiate",
    summary="Clone a template into a fresh OrgV2 (not persisted)",
)
def instantiate_template(template_id: str, body: _InstantiateBody) -> dict[str, Any]:
    """Mint a fresh :class:`OrgV2` from the template and return it.

    The returned org is *not* persisted — Phase 7 will add a separate
    ``POST /api/v2/orgs`` endpoint that persists. Today the editor
    posts to this endpoint to get the resolved structure (with fresh
    ULIDs and overrides applied), then either renders it for review
    or immediately POSTs it to the legacy persistence layer through
    a one-time bridge.
    """
    _require_v2_enabled()
    _ensure_registry_bootstrapped()
    overrides: dict[str, Any] = {}
    if body.defaults is not None:
        overrides["defaults"] = body.defaults
    if body.node_persona_prompts is not None:
        overrides["node_persona_prompts"] = body.node_persona_prompts
    if body.node_runtime_overrides is not None:
        overrides["node_runtime_overrides"] = body.node_runtime_overrides
    try:
        org = GLOBAL_REGISTRY.instantiate(
            template_id,
            name=body.name,
            description=body.description,
            overrides=overrides or None,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except TemplateValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return org.to_jsonable()
