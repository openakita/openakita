"""VAT declaration upload + listing endpoints (M1 W2 Stage 5)."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .models import VatDeclarationListResponse, VatDeclarationModel
from .parsers.vat_declaration import VatParseError, parse_workbook

if TYPE_CHECKING:
    from .routes import FinanceAutoService

logger = logging.getLogger(__name__)

MAX_VAT_BYTES = 32 * 1024 * 1024


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_model(row: Any) -> VatDeclarationModel:
    raw_fields = json.loads(row["raw_fields_json"] or "{}")
    warnings = json.loads(row["warnings_json"] or "[]")
    return VatDeclarationModel(
        id=row["id"],
        org_id=row["org_id"],
        declaration_period=row["declaration_period"],
        province=row["province"],
        dialect=row["dialect"],
        confidence=row["confidence"] or 0.0,
        output_vat=row["output_vat"] or 0.0,
        input_vat=row["input_vat"] or 0.0,
        prev_credit=row["prev_credit"] or 0.0,
        tax_payable=row["tax_payable"] or 0.0,
        surtax_total=row["surtax_total"] or 0.0,
        raw_fields=raw_fields,
        warnings=warnings,
        source_file=row["source_file"],
        file_sha256=row["file_sha256"],
        uploaded_at=row["uploaded_at"],
    )


def register_vat_endpoints(router: APIRouter, service: FinanceAutoService) -> None:
    @router.post(
        "/orgs/{org_id}/vat-declarations",
        status_code=201,
        summary="上传增值税及附加税费申报表 (.xlsx)",
    )
    async def upload_vat(
        org_id: str,
        file: UploadFile = File(..., description="金税四期增值税申报表 .xlsx"),
        declaration_period: str = Form(..., description="申报期间，如 2025-01"),
    ) -> VatDeclarationModel:
        await service.get_org(org_id)
        if not file.filename:
            raise HTTPException(status_code=400, detail="missing filename")
        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="empty upload")
        if len(raw_bytes) > MAX_VAT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"upload too large: {len(raw_bytes)} bytes > "
                    f"{MAX_VAT_BYTES} bytes"
                ),
            )
        sha = hashlib.sha256(raw_bytes).hexdigest()

        suffix = Path(file.filename).suffix.lower() or ".xlsx"
        if suffix not in (".xlsx", ".xls"):
            raise HTTPException(
                status_code=400,
                detail=f"unsupported file extension {suffix!r}; expected .xlsx",
            )
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="finauto_vat_")
        import os as _os
        try:
            with _os.fdopen(fd, "wb") as fh:
                fh.write(raw_bytes)
            try:
                parsed = parse_workbook(
                    Path(tmp_path), declaration_period=declaration_period
                )
            except VatParseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("finance-auto: VAT parse failed")
                raise HTTPException(
                    status_code=422,
                    detail=f"VAT parse failed: {type(exc).__name__}: {exc}",
                ) from exc
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

        new_id = f"vat_{uuid.uuid4().hex[:12]}"
        province = None
        for note in parsed.warnings:
            if note.startswith("province hint matched: "):
                province = note.split(":")[1].split(" via")[0].strip()
                break

        await service.db.conn.execute(
            "INSERT INTO vat_declarations(id, org_id, declaration_period, "
            "province, dialect, confidence, output_vat, input_vat, "
            "prev_credit, tax_payable, surtax_total, raw_fields_json, "
            "warnings_json, source_file, file_sha256, uploaded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id,
                org_id,
                parsed.declaration_period,
                province,
                parsed.dialect,
                parsed.confidence,
                parsed.output_vat,
                parsed.input_vat,
                parsed.prev_credit,
                parsed.tax_payable,
                parsed.surtax_total,
                json.dumps(parsed.raw_fields, ensure_ascii=False),
                json.dumps(parsed.warnings, ensure_ascii=False),
                file.filename,
                sha,
                _utcnow_iso(),
            ),
        )
        await service.db.conn.commit()
        async with service.db.conn.execute(
            "SELECT * FROM vat_declarations WHERE id=?", (new_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=500, detail="VAT row vanished after insert")
        return _row_to_model(row)

    @router.get(
        "/orgs/{org_id}/vat-declarations",
        summary="列出账套已上传的增值税申报表",
    )
    async def list_vat(org_id: str) -> VatDeclarationListResponse:
        await service.get_org(org_id)
        async with service.db.conn.execute(
            "SELECT * FROM vat_declarations WHERE org_id=? "
            "ORDER BY uploaded_at DESC",
            (org_id,),
        ) as cur:
            rows = await cur.fetchall()
        items = [_row_to_model(r) for r in rows]
        return VatDeclarationListResponse(declarations=items, total=len(items))
