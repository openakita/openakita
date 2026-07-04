"""Audit-template upload, listing, and render endpoints (M1 W2 Stage 6)."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .models import (
    AuditTemplate,
    AuditTemplateListResponse,
    AuditTemplateRenderRequest,
)
from .rbac import require_permission
from .services.audit_template import (
    build_allowlist,
    deserialise_report,
    render_template,
    scan_template,
    serialise_report,
    validate_placeholders,
)

if TYPE_CHECKING:
    from .routes import FinanceAutoService

logger = logging.getLogger(__name__)

MAX_AUDIT_BYTES = 64 * 1024 * 1024
_STORAGE_ROOT = Path(__file__).resolve().parent.parent / "data" / "audit_templates"


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _build_allowlist_for_org(service: FinanceAutoService) -> set[str]:
    """Collect every reference_code ever generated for any org and merge with
    the static context names.  This is a best-effort allowlist -- templates
    targeting reports that haven't been generated yet will report unknown
    placeholders, which is the correct behaviour."""
    async with service.db.conn.execute(
        "SELECT DISTINCT reference_code FROM report_cells"
    ) as cur:
        rows = await cur.fetchall()
    codes = [row[0] for row in rows]
    return build_allowlist(codes)


def _row_to_model(row: Any) -> AuditTemplate:
    return AuditTemplate(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        file_path=row["file_path"],
        file_sha256=row["file_sha256"],
        file_size=row["file_size"] or 0,
        placeholder_count=row["placeholder_count"] or 0,
        unknown_placeholder_count=row["unknown_placeholder_count"] or 0,
        placeholder_report=json.loads(row["placeholder_report_json"] or "{}"),
        uploaded_at=row["uploaded_at"],
    )


def register_audit_endpoints(router: APIRouter, service: FinanceAutoService) -> None:
    @router.post(
        "/audit-templates",
        status_code=201,
        summary="上传审计底稿模板 (.xlsx) 并校验 Jinja2 占位符",
    )
    async def upload_audit_template(
        file: UploadFile = File(..., description="审计底稿模板 .xlsx"),
        name: str = Form(..., description="模板显示名"),
        description: str | None = Form(default=None),
        _user: str = Depends(require_permission("audit_template", "upload")),
    ) -> AuditTemplate:
        if not file.filename:
            raise HTTPException(status_code=400, detail="missing filename")
        suffix = Path(file.filename).suffix.lower() or ".xlsx"
        if suffix != ".xlsx":
            raise HTTPException(
                status_code=400,
                detail=f"unsupported extension {suffix!r}; expected .xlsx",
            )
        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="empty upload")
        if len(raw_bytes) > MAX_AUDIT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"upload too large: {len(raw_bytes)} bytes > "
                    f"{MAX_AUDIT_BYTES} bytes"
                ),
            )
        sha = hashlib.sha256(raw_bytes).hexdigest()

        new_id = f"tpl_{uuid.uuid4().hex[:12]}"
        _STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
        target = _STORAGE_ROOT / f"{new_id}.xlsx"
        target.write_bytes(raw_bytes)

        try:
            placeholders = scan_template(target)
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail=f"template scan failed: {type(exc).__name__}: {exc}",
            ) from exc

        allow = await _build_allowlist_for_org(service)
        report = validate_placeholders(placeholders, allow)
        report_json = serialise_report(report)

        await service.db.conn.execute(
            "INSERT INTO audit_templates(id, name, description, file_path, "
            "file_sha256, file_size, placeholder_count, "
            "unknown_placeholder_count, placeholder_report_json, uploaded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                new_id,
                name,
                description,
                str(target),
                sha,
                len(raw_bytes),
                len(placeholders),
                len(report.unknown),
                report_json,
                _utcnow_iso(),
            ),
        )
        await service.db.conn.commit()
        async with service.db.conn.execute(
            "SELECT * FROM audit_templates WHERE id=?", (new_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="audit template vanished")
        return _row_to_model(row)

    @router.get(
        "/audit-templates",
        summary="列出已上传的审计底稿模板",
    )
    async def list_audit_templates() -> AuditTemplateListResponse:
        async with service.db.conn.execute(
            "SELECT * FROM audit_templates ORDER BY uploaded_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        items = [_row_to_model(r) for r in rows]
        return AuditTemplateListResponse(templates=items, total=len(items))

    @router.post(
        "/orgs/{org_id}/audit-templates/{tpl_id}/render",
        summary="使用一份已生成的报表数据渲染审计底稿模板",
        response_class=FileResponse,
    )
    async def render_audit_template(
        org_id: str,
        tpl_id: str,
        payload: AuditTemplateRenderRequest,
    ) -> FileResponse:
        org = await service.get_org(org_id)
        async with service.db.conn.execute(
            "SELECT * FROM audit_templates WHERE id=?", (tpl_id,)
        ) as cur:
            tpl_row = await cur.fetchone()
        if tpl_row is None:
            raise HTTPException(status_code=404, detail="audit template not found")
        tpl = _row_to_model(tpl_row)
        report = deserialise_report(json.dumps(tpl.placeholder_report, ensure_ascii=False))

        if payload.strict and report.unknown:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": (
                        "audit template has unknown placeholders; pass "
                        "strict=false to render anyway"
                    ),
                    "unknown": report.unknown,
                },
            )

        async with service.db.conn.execute(
            "SELECT * FROM reports WHERE org_id=? AND id=?",
            (org_id, payload.report_id),
        ) as cur:
            rep_row = await cur.fetchone()
        if rep_row is None:
            raise HTTPException(status_code=404, detail="report not found")
        async with service.db.conn.execute(
            "SELECT * FROM report_cells WHERE report_id=?",
            (payload.report_id,),
        ) as cur:
            cell_rows = await cur.fetchall()

        cells_ctx: dict[str, dict[str, Any]] = {}
        for c in cell_rows:
            cells_ctx[c["reference_code"]] = {
                "value": c["value"] or 0.0,
                "label": c["target_label"],
                "code": c["code"],
                "is_total": bool(c["is_total"]),
                "is_tbd": bool(c["is_tbd"]),
            }

        # Flat alias layer: BS_1001 directly returns the cell's value so
        # ``{{ BS_1001 }}`` works for compact templates.
        flat: dict[str, float] = {
            ref: ctx["value"] for ref, ctx in cells_ctx.items()
        }

        context = {
            "org": {
                "id": org.id,
                "name": org.name,
                "code": org.code,
                "industry": org.industry,
                "standard": org.standard,
                "fiscal_start": org.fiscal_start,
            },
            "report": {
                "id": rep_row["id"],
                "period_id": rep_row["period_id"],
                "sheet_kind": rep_row["sheet_kind"],
                "accounting_standard": rep_row["accounting_standard"],
                "template_id": rep_row["template_id"],
                "generated_at": rep_row["generated_at"],
            },
            "year": rep_row["period_id"][:4]
            if rep_row["period_id"] else "",
            "period": rep_row["period_id"],
            "standard": rep_row["accounting_standard"],
            "today": _utcnow_iso()[:10],
            "now": _utcnow_iso(),
            "auditor": "",
            "engagement_id": "",
            "cells": cells_ctx,
            **flat,
        }

        fd, name = tempfile.mkstemp(suffix=".xlsx", prefix="finauto_audit_")
        import os as _os
        _os.close(fd)
        out = Path(name)
        out.unlink(missing_ok=True)

        try:
            render_template(tpl.file_path, out, context=context)
        except Exception as exc:
            logger.exception("audit template render failed")
            raise HTTPException(
                status_code=422,
                detail=f"template render failed: {type(exc).__name__}: {exc}",
            ) from exc

        return FileResponse(
            str(out),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            filename=f"{tpl.name}_{rep_row['period_id']}.xlsx",
        )
