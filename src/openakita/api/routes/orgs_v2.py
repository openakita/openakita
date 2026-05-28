"""V2 organisation API facade.

This route module exposes the new :mod:`openakita.runtime` stack
(``runtime/templates`` for now; ``runtime/supervisor`` and
``runtime/messenger`` once Phase 6 wires the per-org executor) over
HTTP. It is a *parallel* surface to ``api/routes/orgs.py``; the
legacy v1 routes keep running. A request only ever reaches v2 when
``settings.runtime_v2_enabled`` is true — otherwise the router
returns ``404 /api/v2/orgs-spec/...``.

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

``GET    /api/v2/orgs-spec/templates``                   list TemplateSpec records
``GET    /api/v2/orgs-spec/templates/{id}``               one TemplateSpec
``POST   /api/v2/orgs-spec/templates/{id}/instantiate``   -> fresh OrgV2 (not persisted)
``POST   /api/v2/orgs-spec``                              persist an instantiated org
``GET    /api/v2/orgs-spec``                              list persisted orgs
``GET    /api/v2/orgs-spec/{id}``                         get one persisted org
``PATCH  /api/v2/orgs-spec/{id}``                         patch name / description
``DELETE /api/v2/orgs-spec/{id}``                         delete one persisted org

P-RC-9 P9.7a-2 (Group A R3 LOCKED, see ``docs/revamp/P-RC-9-P9.7-DECISIONS.md`` D-1): this router moved
from ``/api/v2/orgs[/...]`` to ``/api/v2/orgs-spec[/...]`` so the
P9.7 mint can claim the original ``/api/v2/orgs`` namespace.
308 Permanent Redirect shims at the old paths ride v2.0.x via
``_orgs_v2_legacy_redirects.router``.

Persistence layer: :mod:`openakita.orgs` (JSON file under
``data/orgs_v2.json``). Phase 7 upgrades this to the SQLite-backed
checkpointer; the API contract stays the same.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from openakita.config import settings
from openakita.orgs import OrgNotFound, get_default_store
from openakita.orgs.manager import OrgManager, OrgNameConflictError
from openakita.orgs.org_models import EdgeType, OrgStatus
from openakita.orgs.store import JsonOrgStore
from openakita.runtime.models import EdgeKind, OrgV2
from openakita.runtime.templates import (
    GLOBAL_REGISTRY,
    TemplateValidationError,
    collect_builtin_factories,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/orgs-spec", tags=["v2:组织编排"])


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
        "[orgs_v2] registry bootstrapped: %d template(s) registered (%d total in registry)",
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


def _resolve_template_id(template_id: str) -> str | None:
    """Return a registered template id, trying hyphen <-> underscore variants.

    Built-in v2 templates self-register under underscore-case
    (``aigc_video_studio``), but file-backed mint templates live under
    hyphen-case (``aigc-video-studio``). Frontend OrgEditorView may
    forward either form depending on which catalog it last fetched.
    Normalize here so both flavours resolve to the same TemplateSpec.
    Returns ``None`` when no variant is registered.
    """
    if template_id in GLOBAL_REGISTRY:
        return template_id
    variants = []
    if "-" in template_id:
        variants.append(template_id.replace("-", "_"))
    if "_" in template_id:
        variants.append(template_id.replace("_", "-"))
    for v in variants:
        if v in GLOBAL_REGISTRY:
            return v
    return None


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

    ROADMAP — Response format unification (P9.7gamma):
    This spec endpoint returns a ``{"templates": [...]}`` envelope,
    but the runtime sibling at ``/api/v2/orgs/templates`` returns a
    bare array. The plan is to change THIS endpoint to a bare array
    to match runtime, since the frontend only consumes runtime today
    (see ``apps/setup-center/src/api/orgs.ts``). Tracked in
    ``docs/follow-ups/skipped-items-roadmap.md`` §A.4 and
    ``_skip_items_rca_v11.md`` §4.3. DO NOT change the runtime
    endpoint's shape.
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
    resolved = _resolve_template_id(template_id)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown template id {template_id!r}",
        )
    spec = GLOBAL_REGISTRY.get(resolved)
    return spec.to_jsonable()


@router.post(
    "/templates/{template_id}/instantiate",
    summary="Clone a template into a fresh OrgV2 (not persisted)",
)
def instantiate_template(template_id: str, body: _InstantiateBody) -> dict[str, Any]:
    """Mint a fresh :class:`OrgV2` from the template and return it.

    The returned org is *not* persisted — pass it to ``POST /api/v2/
    orgs`` to commit it to the store. Today the editor posts to this
    endpoint to get the resolved structure (with fresh ULIDs and
    overrides applied), then either renders it for review or
    immediately POSTs it to the persistence endpoint.
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
    resolved = _resolve_template_id(template_id)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown template id {template_id!r}",
        )
    try:
        org = GLOBAL_REGISTRY.instantiate(
            resolved,
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


# ---------------------------------------------------------------------------
# OrgV2 resource CRUD
# ---------------------------------------------------------------------------


class _CreateOrgBody(BaseModel):
    """Persist an already-instantiated OrgV2 returned by
    :func:`instantiate_template`. Posting the raw ``to_jsonable()``
    payload back here is the canonical create flow."""

    org: dict[str, Any] = Field(
        ...,
        description="The OrgV2 payload returned by /templates/{id}/instantiate.",
    )


class _PatchOrgBody(BaseModel):
    """Whitelisted patch surface. Nodes/edges/defaults regen via the
    template instantiate flow rather than mutating in place."""

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None


# ---------------------------------------------------------------------------
# Manager resolution (Sprint 13 H2 / RC-1)
# ---------------------------------------------------------------------------
#
# The spec CRUD endpoints write through OrgManager so mint and spec
# write paths share one SSoT (``data/orgs/<id>/org.json``). Reads
# go through the same shim ``get_default_store()`` returns -- it
# now routes through ``OrgManager.as_orgv2`` and unions in legacy
# ``data/orgs_v2.json`` rows for the deprecation soak. See
# ``src/openakita/orgs/store.py`` module docstring for the full
# contract.


def _resolve_manager_for_writes() -> OrgManager:
    """Return the :class:`OrgManager` the spec CRUD should write to.

    Priority:

    1. The :class:`JsonOrgStore` shim's backing manager (set by
       ``api/server.py`` via :func:`set_default_org_manager` or by
       a test fixture via ``reset_default_store(..., manager=)``).
       This is the canonical resolution -- the same manager
       ``app.state.org_manager`` exposes.
    2. A settings-derived fallback when the active backend is
       :class:`SqliteOrgStore` (opt-in, rare). The spec routes
       still land orgs in ``OrgManager`` so the IM canary read
       path can see them; SQLite-backend deployments accept that
       trade-off because RC-1 explicitly chose OrgManager as the
       SSoT regardless of the legacy JSON / SQLite alternation.
    """
    store = get_default_store()
    if isinstance(store, JsonOrgStore):
        return store._get_manager()
    # SQLite-backend (opt-in): build manager from settings so the
    # spec CRUD still flows into the SSoT.
    from openakita.orgs.store import _build_default_manager

    return _build_default_manager()


def _orgv2_dict_to_organization_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate an :class:`OrgV2` jsonable payload into the dict
    :meth:`OrgManager.create` consumes.

    The translation is intentionally light: only the fields the
    legacy :class:`Organization` model can carry are forwarded;
    OrgV2-only metadata (``defaults``, ``last_seen`` /
    ``last_progress_at`` per node) is dropped because OrgManager
    has no place to put them. Nodes/edges are projected via
    :func:`_orgv2_node_dict_to_orgnode_data` /
    :func:`_orgv2_edge_dict_to_orgedge_data`.
    """
    nodes_raw = payload.get("nodes") or []
    edges_raw = payload.get("edges") or []
    data: dict[str, Any] = {
        "id": payload.get("id") or "",
        "name": payload.get("name") or "",
        "description": payload.get("description") or "",
        "nodes": [_orgv2_node_dict_to_orgnode_data(n) for n in nodes_raw],
        "edges": [_orgv2_edge_dict_to_orgedge_data(e) for e in edges_raw],
    }
    # Status mapping: OrgV2 (CREATED/ACTIVE/RUNNING/PAUSED/STOPPED) ->
    # Organization (DORMANT/ACTIVE/RUNNING/PAUSED/ARCHIVED). Just-
    # minted spec orgs land as DORMANT so the v1 lifecycle FSM
    # treats them as "not yet active" until an explicit start verb.
    raw_status = (payload.get("status") or "").lower()
    status_map = {
        "created": OrgStatus.DORMANT.value,
        "active": OrgStatus.ACTIVE.value,
        "running": OrgStatus.RUNNING.value,
        "paused": OrgStatus.PAUSED.value,
        "stopped": OrgStatus.ARCHIVED.value,
    }
    data["status"] = status_map.get(raw_status, OrgStatus.DORMANT.value)
    return data


def _orgv2_node_dict_to_orgnode_data(node_dict: dict[str, Any]) -> dict[str, Any]:
    """Project one OrgV2 NodeV2 jsonable into an OrgNode jsonable.

    Field mapping:
    * ``id`` 1:1
    * ``role`` -> ``role_title`` (the v2 wire calls the function
      identifier "role"; v1 calls the human label "role_title")
    * ``label`` -> falls back into ``role_title`` when ``role`` is
      blank so the projection is non-empty
    * ``role`` (the function id) -> ``agent_profile_id`` (the
      legacy slot that picks an AgentProfile by id)
    * ``persona_prompt`` -> ``custom_prompt``
    * ``tool_subset`` -> ``external_tools``
    * ``workbench`` -> ``plugin_origin`` (when present)
    * everything else: dropped (not representable in OrgNode)
    """
    role = (node_dict.get("role") or "").strip()
    label = (node_dict.get("label") or "").strip()
    title = label or role or (node_dict.get("id") or "")
    workbench = node_dict.get("workbench") or None
    plugin_origin: dict[str, Any] | None = None
    if isinstance(workbench, dict) and workbench.get("plugin_id"):
        plugin_origin = {
            "plugin_id": str(workbench.get("plugin_id")),
            "mode": str(workbench.get("mode") or "default"),
        }
    return {
        "id": node_dict.get("id") or "",
        "role_title": title,
        "agent_profile_id": role or "default",
        "custom_prompt": node_dict.get("persona_prompt") or "",
        "external_tools": list(node_dict.get("tool_subset") or []),
        "plugin_origin": plugin_origin,
    }


def _orgv2_edge_dict_to_orgedge_data(edge_dict: dict[str, Any]) -> dict[str, Any]:
    """Project one OrgV2 EdgeV2 jsonable into an OrgEdge jsonable."""
    kind = (edge_dict.get("kind") or EdgeKind.HIERARCHY.value).lower()
    try:
        edge_type = EdgeType(kind).value
    except ValueError:
        edge_type = EdgeType.HIERARCHY.value
    return {
        "id": edge_dict.get("id") or "",
        "source": edge_dict.get("src") or "",
        "target": edge_dict.get("dst") or "",
        "edge_type": edge_type,
    }


# ---------------------------------------------------------------------------
# CRUD endpoints (Sprint 13 H2: writes always flow through OrgManager)
# ---------------------------------------------------------------------------


@router.get("", summary="List persisted v2 organisations")
def list_orgs() -> dict[str, Any]:
    """List spec orgs.

    Reads through the shim, which unions OrgManager (mint + spec
    creates) with the legacy ``data/orgs_v2.json`` soak file --
    so callers see one consistent set regardless of how the
    org was originally minted.
    """
    _require_v2_enabled()
    store = get_default_store()
    items = [org.to_jsonable() for org in store.list()]
    return {"orgs": items, "count": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Persist a v2 organisation")
def create_org(body: _CreateOrgBody) -> dict[str, Any]:
    """Persist an instantiated OrgV2 -- writes through OrgManager.

    Sprint 13 H2 (RC-1): pre-fix this wrote to
    ``data/orgs_v2.json`` via :class:`JsonOrgStore`, leaving the
    org invisible to mint readers (the v25 H2 / E4 symptom). Now
    the schema-projected payload lands in
    ``data/orgs/<id>/org.json`` so the IM canary path and the
    spec list can both see it.
    """
    _require_v2_enabled()

    # Fill server-generated timestamps if the caller omitted
    # them: the strict :meth:`OrgV2.from_jsonable` /
    # :meth:`NodeV2.from_jsonable` require ``created_at``, but
    # pre-Sprint-13 the JsonOrgStore.create flow auto-stamped
    # them, and we preserve that ergonomic contract for callers
    # that don't supply timestamps.
    from datetime import datetime

    now_iso = datetime.now(UTC).isoformat()
    payload_for_validation: dict[str, Any] = dict(body.org)
    payload_for_validation.setdefault("created_at", now_iso)
    payload_for_validation.setdefault("updated_at", now_iso)
    nodes_for_validation: list[dict[str, Any]] = []
    for node_dict in payload_for_validation.get("nodes") or []:
        if not isinstance(node_dict, dict):
            nodes_for_validation.append(node_dict)
            continue
        n = dict(node_dict)
        n.setdefault("created_at", now_iso)
        nodes_for_validation.append(n)
    payload_for_validation["nodes"] = nodes_for_validation
    try:
        OrgV2.from_jsonable(payload_for_validation)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid OrgV2 payload: {exc}",
        ) from exc

    manager = _resolve_manager_for_writes()
    org_data = _orgv2_dict_to_organization_data(body.org)

    # Idempotent guard against id collisions: the original
    # ``JsonOrgStore.create`` raised ValueError("already exists"); v1's
    # ``OrgManager.create`` only checks name uniqueness, so we surface
    # the id collision here so callers keep getting 409 on dup id.
    existing_id = org_data.get("id")
    if existing_id and manager.get(existing_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"OrgV2 with id={existing_id!r} already exists",
        )
    try:
        organization = manager.create(org_data)
    except OrgNameConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    saved = manager.as_orgv2(organization.id)
    if saved is None:  # pragma: no cover - manager.create just persisted it
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OrgManager.create succeeded but as_orgv2 returned None",
        )
    return saved.to_jsonable()


@router.get("/{org_id}", summary="Get a persisted v2 organisation")
def get_org(org_id: str) -> dict[str, Any]:
    """Get a spec org -- reads through the manager-backed shim.

    Mint orgs are now visible here (they weren't pre-Sprint-13);
    legacy ``data/orgs_v2.json`` rows are still resolvable during
    the deprecation soak.
    """
    _require_v2_enabled()
    try:
        org = get_default_store().get(org_id)
    except OrgNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return org.to_jsonable()


@router.patch("/{org_id}", summary="Patch a v2 organisation (name / description)")
def patch_org(org_id: str, body: _PatchOrgBody) -> dict[str, Any]:
    """Patch name / description on a spec org -- writes via OrgManager.update."""
    _require_v2_enabled()
    manager = _resolve_manager_for_writes()
    if manager.get(org_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OrgV2 with id={org_id!r} not found",
        )
    patch_data: dict[str, Any] = {}
    if body.name is not None:
        patch_data["name"] = body.name
    if body.description is not None:
        patch_data["description"] = body.description
    try:
        if patch_data:
            manager.update(org_id, patch_data)
    except OrgNameConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    projected = manager.as_orgv2(org_id)
    if projected is None:  # pragma: no cover - just patched
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OrgV2 with id={org_id!r} not found",
        )
    return projected.to_jsonable()


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a persisted v2 organisation",
)
def delete_org(org_id: str) -> None:
    """Delete a spec org -- writes via OrgManager.delete."""
    _require_v2_enabled()
    manager = _resolve_manager_for_writes()
    deleted = manager.delete(org_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"OrgV2 with id={org_id!r} not found",
        )
    return None
