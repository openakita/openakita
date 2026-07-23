"""Org CRUD + templates + lifecycle endpoints (P-RC-9 P9.7beta-1).

Mints the 17 endpoints in cluster 3.1 of
``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md`` (B1-B17). Every
route delegates to the P9.5 :class:`OrgManager` subsystem via the
``_get_manager(request)`` helper defined in
:mod:`openakita.api.routes.orgs_v2_runtime` -- thin wiring only
(D-4 LOCKED). Behavioural oracle: ``src/openakita/api/routes/orgs.py``
(v1). Returns ``dict[str, Any]`` to preserve v1 wire shape; typed
Pydantic response models ride P9.7gamma per charter section 5.

ADR refs: ADR-0011 (D-3 layer separation), ADR-0012 (no shim
under v1; free-function helpers ``list_avatar_presets`` /
``build_workbench_templates`` are not v1 ``OrgManager`` methods).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from openakita.api.schemas.orgs_v2 import OrgCreate
from openakita.api.schemas.orgs_v2.orgs import OrgPatch

from .orgs_v2_runtime import _get_manager, router

logger = logging.getLogger(__name__)

ALLOWED_AVATAR_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/svg+xml",
}
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2 MB
_AVATAR_EXT_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
_FILE_FIELD = File(...)


def _raise_org_name_conflict(exc: Any) -> None:
    """v1 ``_raise_org_name_conflict`` parity envelope (409)."""
    raise HTTPException(
        409,
        {
            "code": "org_name_conflict",
            "message": f"Organization name already in use: {exc.name!r}",
            "name": exc.name,
            "conflict_org_id": exc.conflict_org_id,
        },
    )


def _to_dict(obj: Any) -> Any:
    """Return ``obj.to_dict()`` if available else the object itself."""
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


# ---------------------------------------------------------------------------
# B1-B2: list + create
# ---------------------------------------------------------------------------


@router.get("", summary="B1 list organizations")
def list_orgs(request: Request, include_archived: bool = False) -> list[dict[str, Any]]:
    return _get_manager(request).list_orgs(include_archived=include_archived)


@router.post("", status_code=201, summary="B2 create organization")
def create_org(request: Request, body: OrgCreate) -> dict[str, Any]:
    from openakita.orgs import OrgNameConflictError

    try:
        org = _get_manager(request).create(body.model_dump(exclude_none=True))
    except OrgNameConflictError as exc:
        _raise_org_name_conflict(exc)
    return _to_dict(org)


# ---------------------------------------------------------------------------
# B3-B4: avatars
# ---------------------------------------------------------------------------


@router.get("/avatar-presets", summary="B3 list avatar presets")
def get_avatar_presets() -> list[dict[str, Any]]:
    from openakita.orgs._runtime_templates import list_avatar_presets

    return list_avatar_presets()


@router.post("/avatars/upload", summary="B4 upload custom avatar")
async def upload_avatar(request: Request, file: UploadFile = _FILE_FIELD) -> dict[str, Any]:
    if file.content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(400, f"Unsupported file type: {file.content_type}")
    data = await file.read()
    if len(data) > MAX_AVATAR_SIZE:
        raise HTTPException(400, f"File too large (max {MAX_AVATAR_SIZE // 1024}KB)")
    ext = _AVATAR_EXT_MAP.get(file.content_type or "", ".png")
    digest = hashlib.md5(data, usedforsecurity=False).hexdigest()[:12]
    filename = f"{digest}_{int(time.time())}{ext}"
    from openakita.config import settings

    avatar_dir = settings.data_dir / "avatars"
    avatar_dir.mkdir(parents=True, exist_ok=True)
    (avatar_dir / filename).write_bytes(data)
    return {"url": f"/api/avatars/{filename}", "filename": filename, "size": len(data)}


# ---------------------------------------------------------------------------
# B5-B7: template catalog
# ---------------------------------------------------------------------------


@router.get("/templates", summary="B5 list org templates")
def list_templates(request: Request) -> list[dict[str, Any]]:
    return _get_manager(request).list_templates()


@router.get("/plugin-workbench-templates", summary="B6 list workbench templates")
def list_plugin_workbench_templates(request: Request) -> list[dict[str, Any]]:
    from openakita.orgs._runtime_templates import build_workbench_templates

    agent = getattr(request.app.state, "agent", None)
    pm = getattr(agent, "_plugin_manager", None) if agent else None
    return build_workbench_templates(pm)


@router.get("/templates/{template_id}", summary="B7 get org template")
def get_template(request: Request, template_id: str) -> dict[str, Any]:
    tpl = _get_manager(request).get_template(template_id)
    if tpl is None:
        raise HTTPException(404, f"Template not found: {template_id}")
    return tpl


# ---------------------------------------------------------------------------
# B8-B9: from-template + import
# ---------------------------------------------------------------------------


@router.post("/from-template", status_code=201, summary="B8 create org from template")
async def create_from_template(request: Request) -> dict[str, Any]:
    from openakita.orgs import OrgNameConflictError

    body = await request.json()
    template_id = body.pop("template_id", None)
    if not template_id:
        raise HTTPException(400, "template_id is required")
    try:
        org = _get_manager(request).create_from_template(template_id, overrides=body)
    except FileNotFoundError:
        raise HTTPException(404, f"Template not found: {template_id}") from None
    except OrgNameConflictError as exc:
        _raise_org_name_conflict(exc)
    return _to_dict(org)


@router.post("/import", status_code=201, summary="B9 import org from file")
async def import_org(request: Request, file: UploadFile = _FILE_FIELD) -> dict[str, Any]:
    try:
        data = json.loads(await file.read())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"Invalid file format: {exc}") from exc
    org_data = data.get("organization")
    if not isinstance(org_data, dict):
        raise HTTPException(400, "Missing or invalid 'organization' field")
    org_data.pop("id", None)
    org_data["status"] = "dormant"
    org_data["total_tasks_completed"] = 0
    org_data["total_messages_exchanged"] = 0
    try:
        org = _get_manager(request).create(org_data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Import failed: {exc}") from exc
    return {
        "message": f"Organization {org_data.get('name', '')!r} imported",
        "organization": _to_dict(org),
        "renamed": False,
    }


# ---------------------------------------------------------------------------
# B10-B12: single-org CRUD
# ---------------------------------------------------------------------------


@router.get("/{org_id}", summary="B10 get organization")
def get_org(request: Request, org_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    rt = getattr(request.app.state, "org_runtime", None)
    snap = getattr(rt, "get_org_snapshot", None) if rt else None
    org: Any = None
    if callable(snap):
        try:
            org = snap(org_id)
        except Exception:  # noqa: BLE001
            org = None
    if org is None:
        org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    return _to_dict(org)


@router.put("/{org_id}", summary="B11 update organization")
def update_org(request: Request, org_id: str, body: OrgPatch) -> dict[str, Any]:
    from openakita.orgs import OrgNameConflictError

    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    try:
        org = mgr.update(org_id, body.model_dump(exclude_none=True))
    except OrgNameConflictError as exc:
        _raise_org_name_conflict(exc)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, f"Invalid org data: {exc}") from exc
    return _to_dict(org)


@router.patch("/{org_id}", summary="B11p partial update organization")
def patch_org(request: Request, org_id: str, body: OrgPatch) -> dict[str, Any]:
    """Partial update -- mirrors :func:`update_org` (PUT) semantics.

    Closes smoke F-5: without an explicit PATCH handler on this mint
    runtime route, FastAPI's first-match routing fell through to the
    Group A 308 shim (``_orgs_v2_deprecated_redirects._r_patch_org``), which
    redirected the request to ``/api/v2/orgs-spec/{org_id}`` -- backed
    by a *different* persistence store -- producing apparent 404s on
    orgs that had just been created via this runtime mint ``POST``.

    The body schema (:class:`OrgPatch`) is already all-optional so
    PATCH and PUT are semantically equivalent today; future work can
    tighten PUT to a full-replace contract without touching this
    handler.
    """
    from openakita.orgs import OrgNameConflictError

    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    try:
        org = mgr.update(org_id, body.model_dump(exclude_none=True))
    except OrgNameConflictError as exc:
        _raise_org_name_conflict(exc)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, f"Invalid org data: {exc}") from exc
    return _to_dict(org)


@router.delete("/{org_id}", summary="B12 delete organization")
def delete_org(request: Request, org_id: str) -> dict[str, Any]:
    if not _get_manager(request).delete(org_id):
        raise HTTPException(404, f"Organization not found: {org_id}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# B13-B17: duplicate / archive / unarchive / save-as-template / export
# ---------------------------------------------------------------------------


@router.post("/{org_id}/duplicate", status_code=201, summary="B13 duplicate organization")
async def duplicate_org(request: Request, org_id: str) -> dict[str, Any]:
    from openakita.orgs import OrgNameConflictError

    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    try:
        org = mgr.duplicate(org_id, new_name=body.get("name"))
    except OrgNameConflictError as exc:
        _raise_org_name_conflict(exc)
    return _to_dict(org)


@router.post("/{org_id}/archive", summary="B14 archive organization")
def archive_org(request: Request, org_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    return _to_dict(mgr.archive(org_id))


@router.post("/{org_id}/unarchive", summary="B15 unarchive organization")
def unarchive_org(request: Request, org_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    return _to_dict(mgr.unarchive(org_id))


@router.post("/{org_id}/save-as-template", summary="B16 save org as template")
async def save_as_template(request: Request, org_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    if mgr.get(org_id) is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    tid = mgr.save_as_template(org_id, template_id=body.get("template_id"))
    return {"template_id": tid}


@router.post("/{org_id}/export", summary="B17 export organization")
def export_org(request: Request, org_id: str) -> JSONResponse:
    """Thin envelope; full file capture rides P9.7gamma per charter section 5."""
    org = _get_manager(request).get(org_id)
    if org is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    return JSONResponse(
        content={
            "format": "akita-org",
            "version": "1.0",
            "organization": _to_dict(org),
            "files": {},
        }
    )
