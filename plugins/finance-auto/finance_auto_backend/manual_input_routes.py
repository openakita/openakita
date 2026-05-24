"""HTTP layer for the W3 Stage 4 manual_inputs feature.

Endpoints
---------
* ``GET  /api/plugins/finance-auto/orgs/{org_id}/periods/{period_id}/manual-inputs``
* ``PUT  /api/plugins/finance-auto/orgs/{org_id}/periods/{period_id}/manual-inputs/{field_key}``
* ``DELETE …`` is intentionally omitted — clearing a value uses a PUT
  with ``value=""`` so the audit trail (``decided_by`` / ``decided_at``
  / ``version``) is preserved.

Listing always returns the canonical preset list (so the UI can render
unfilled slots) merged with persisted records.  Unknown ``field_key``
PUTs are rejected with 400 to keep the table free of stray keys.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from .config.manual_inputs_loader import cash_flow_aux_presets
from .models import (
    ManualInputListResponse,
    ManualInputPreset,
    ManualInputRecord,
    ManualInputSlot,
    ManualInputSubmitRequest,
)

if TYPE_CHECKING:
    from .routes import FinanceAutoService


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return f"mi_{secrets.token_hex(6)}"


def _row_to_record(row) -> ManualInputRecord:
    return ManualInputRecord(
        id=row["id"],
        org_id=row["org_id"],
        period_id=row["period_id"],
        field_key=row["field_key"],
        field_label=row["field_label"] or "",
        value=row["value"] or "",
        value_type=row["value_type"] or "cny",
        source=row["source"] or "manual",
        notes=row["notes"],
        decided_by=row["decided_by"] or "local",
        decided_at=row["decided_at"],
        version=int(row["version"] or 1),
    )


def _slot_from_preset(
    preset: ManualInputPreset, record: ManualInputRecord | None
) -> ManualInputSlot:
    return ManualInputSlot(
        key=preset.key,
        label=preset.label,
        value_type=preset.value_type,
        default_source=preset.default_source,
        source_hint=preset.source_hint,
        required_by=list(preset.required_by),
        record=record,
        filled=bool(record and record.value not in ("", None)),
    )


async def get_manual_input_value(
    service: "FinanceAutoService", *, org_id: str, period_id: str, field_key: str,
) -> float | None:
    """Helper for the report-generation pipeline: return the float value
    of a field (or None if unset / non-numeric).  Centralises the
    float-parsing fallback so callers stay terse."""
    async with service.db.conn.execute(
        "SELECT value, value_type FROM manual_inputs "
        "WHERE org_id=? AND period_id=? AND field_key=?",
        (org_id, period_id, field_key),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    raw = (row["value"] or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def register_manual_input_endpoints(
    router: APIRouter, service: "FinanceAutoService"
) -> None:

    @router.get(
        "/orgs/{org_id}/periods/{period_id}/manual-inputs",
        summary="列出该期所有手填字段（含未填占位）— W3 Stage 4",
    )
    async def list_inputs(
        org_id: str, period_id: str,
    ) -> ManualInputListResponse:
        await service.get_org(org_id)
        presets = cash_flow_aux_presets()
        records: dict[str, ManualInputRecord] = {}
        async with service.db.conn.execute(
            "SELECT * FROM manual_inputs WHERE org_id=? AND period_id=?",
            (org_id, period_id),
        ) as cur:
            async for row in cur:
                rec = _row_to_record(row)
                records[rec.field_key] = rec
        slots = [_slot_from_preset(p, records.get(p.key)) for p in presets]
        filled = sum(1 for s in slots if s.filled)
        return ManualInputListResponse(
            period_id=period_id,
            org_id=org_id,
            slots=slots,
            filled_count=filled,
            total_count=len(slots),
        )

    @router.put(
        "/orgs/{org_id}/periods/{period_id}/manual-inputs/{field_key}",
        summary="提交 / 更新一个手填字段值",
    )
    async def upsert_input(
        org_id: str,
        period_id: str,
        field_key: str,
        payload: ManualInputSubmitRequest,
    ) -> ManualInputRecord:
        await service.get_org(org_id)
        presets = {p.key: p for p in cash_flow_aux_presets()}
        if field_key not in presets:
            raise HTTPException(
                status_code=400,
                detail=f"unknown manual-input field_key: {field_key!r}",
            )
        preset = presets[field_key]
        now = _utcnow_iso()

        # Try update first; if no row exists, insert a fresh one.
        async with service.db.conn.execute(
            "SELECT id, version FROM manual_inputs "
            "WHERE org_id=? AND period_id=? AND field_key=?",
            (org_id, period_id, field_key),
        ) as cur:
            existing = await cur.fetchone()
        if existing is None:
            # No row yet — caller may pass ``expected_version`` to assert
            # "I believe this slot is unset"; mismatch (expected != None
            # AND != 0) means another writer raced ahead of us.
            if (
                payload.expected_version is not None
                and payload.expected_version not in (0, None)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "version_conflict",
                        "field_key": field_key,
                        "expected_version": payload.expected_version,
                        "current_version": 0,
                        "message": (
                            "Slot is empty but client expected version "
                            f"{payload.expected_version}; refetch and retry."
                        ),
                    },
                )
            rid = _new_id()
            await service.db.conn.execute(
                "INSERT INTO manual_inputs(id, org_id, period_id, field_key, "
                "field_label, value, value_type, source, notes, decided_by, "
                "decided_at, version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rid, org_id, period_id, field_key, preset.label,
                    payload.value, payload.value_type or preset.value_type,
                    payload.source or preset.default_source,
                    payload.notes, payload.decided_by, now, 1,
                ),
            )
        else:
            rid = existing["id"]
            current_version = int(existing["version"] or 1)
            new_version = current_version + 1
            # Optimistic lock: when the caller supplies expected_version
            # the UPDATE includes ``WHERE id=? AND version=?`` so a stale
            # write is caught by SQLite's rowcount=0 result.  Without the
            # token we fall back to the old read-modify-write behaviour
            # to preserve compatibility with the M2 UI which doesn't yet
            # surface version numbers in the cash-flow补录 panel.
            if payload.expected_version is not None:
                if payload.expected_version != current_version:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "version_conflict",
                            "field_key": field_key,
                            "expected_version": payload.expected_version,
                            "current_version": current_version,
                        },
                    )
                cur = await service.db.conn.execute(
                    "UPDATE manual_inputs SET field_label=?, value=?, value_type=?, "
                    "source=?, notes=?, decided_by=?, decided_at=?, version=? "
                    "WHERE id=? AND version=?",
                    (
                        preset.label, payload.value,
                        payload.value_type or preset.value_type,
                        payload.source or preset.default_source,
                        payload.notes, payload.decided_by, now, new_version,
                        rid, current_version,
                    ),
                )
                rowcount = cur.rowcount
                await cur.close()
                if rowcount == 0:
                    # Another writer flipped the row between SELECT and
                    # UPDATE — re-read to surface the live version.
                    async with service.db.conn.execute(
                        "SELECT version FROM manual_inputs WHERE id=?",
                        (rid,),
                    ) as cur:
                        live = await cur.fetchone()
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "version_conflict",
                            "field_key": field_key,
                            "expected_version": payload.expected_version,
                            "current_version": int(live["version"])
                            if live else None,
                        },
                    )
            else:
                await service.db.conn.execute(
                    "UPDATE manual_inputs SET field_label=?, value=?, value_type=?, "
                    "source=?, notes=?, decided_by=?, decided_at=?, version=? "
                    "WHERE id=?",
                    (
                        preset.label, payload.value,
                        payload.value_type or preset.value_type,
                        payload.source or preset.default_source,
                        payload.notes, payload.decided_by, now, new_version, rid,
                    ),
                )
        await service.db.conn.commit()
        async with service.db.conn.execute(
            "SELECT * FROM manual_inputs WHERE id=?", (rid,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_record(row)
