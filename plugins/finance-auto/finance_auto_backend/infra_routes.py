"""M3 Infra Stage 4 — admin HTTP layer for key rotation + backups.

Wires the ``KeyRotationService`` and ``BackupRestoreService`` into the
plugin's FastAPI router under the ``/admin/*`` prefix.  Additionally
exposes ``GET /admin/system-info`` which aggregates schema / key /
backup metadata for the desktop Setup Center to render alongside the
Tauri-side ``finance_system_info`` command.

Endpoint families:

* Key rotation (Deliverable 1)
    * ``GET    /admin/key-versions``
    * ``GET    /admin/key-rotation-runs``
    * ``GET    /admin/key-rotation-preview``
    * ``POST   /admin/key-rotate``

* Backup / restore (Deliverable 2)
    * ``POST   /admin/backups``
    * ``GET    /admin/backups``
    * ``GET    /admin/backups/{backup_id}``
    * ``POST   /admin/backups/{backup_id}/restore``
    * ``DELETE /admin/backups/{backup_id}``
    * ``GET    /admin/backups/{backup_id}/download``

* System info (Deliverable 3)
    * ``GET    /admin/system-info``

Total endpoints added: 11 unique paths (FastAPI counts each method on a
shared path as a separate route, so the run-time ``len(router.routes)``
delta is ~13 once we add the buffer absorption helpers).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from .key_meta import GLOBAL_COMPONENT, read_key_meta
from .rbac import require_permission
from .schema import SCHEMA_VERSION
from .services.backup_restore import (
    BackupRestoreError,
    BackupRestoreService,
)
from .services.key_rotation import (
    KeyRotationError,
    KeyRotationService,
)

if TYPE_CHECKING:
    from .routes import FinanceAutoService

logger = logging.getLogger(__name__)


def _openakita_version() -> str | None:
    """Best-effort lookup of the host's openakita version string."""
    try:  # pragma: no cover — purely cosmetic
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("openakita")
        except PackageNotFoundError:
            return None
    except Exception:  # noqa: BLE001
        return None


def register_infra_endpoints(
    router: APIRouter, service: "FinanceAutoService"
) -> None:
    """Attach the M3 infra admin endpoints to ``router``."""

    rotation_service = KeyRotationService(service)
    backup_service = BackupRestoreService(service)

    # ----------------------------- key versioning + rotation ----------

    @router.get(
        "/admin/key-versions",
        summary="列出某 component 的密钥版本历史 (M3 Infra)",
    )
    async def list_key_versions(
        component: str = Query(default=GLOBAL_COMPONENT),
    ) -> dict[str, Any]:
        rows = await rotation_service.list_versions(component=component)
        return {"component": component, "versions": rows, "total": len(rows)}

    @router.get(
        "/admin/key-rotation-runs",
        summary="列出最近的密钥轮换运行记录 (M3 Infra)",
    )
    async def list_rotation_runs(
        component: str = Query(default=GLOBAL_COMPONENT),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        rows = await rotation_service.list_runs(
            component=component, limit=limit
        )
        return {"component": component, "runs": rows, "total": len(rows)}

    @router.get(
        "/admin/key-rotation-preview",
        summary="预览本次轮换需要重新加密的行数 (M3 Infra)",
    )
    async def preview_rotation(
        component: str = Query(default=GLOBAL_COMPONENT),
    ) -> dict[str, Any]:
        return await rotation_service.preview_rotation(component=component)

    @router.post(
        "/admin/key-rotate",
        summary="触发整库密钥轮换 (M3 Infra §2.5)",
    )
    async def rotate_key(
        payload: dict = Body(default_factory=dict),
        _user: str = Depends(require_permission("admin_key", "rotate")),
    ) -> dict[str, Any]:
        component = payload.get("component") or GLOBAL_COMPONENT
        reason = payload.get("reason") or ""
        rotated_by = payload.get("rotated_by") or "local"
        try:
            return await rotation_service.rotate_key(
                component=component,
                reason=reason,
                rotated_by=rotated_by,
            )
        except KeyRotationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ----------------------------- backups ----------------------------

    @router.post(
        "/admin/backups",
        status_code=201,
        summary="创建加密备份 (M3 Infra §2.4 备份/迁移)",
    )
    async def create_backup_endpoint(
        payload: dict = Body(...),
        _user: str = Depends(require_permission("admin_backup", "create")),
    ) -> dict[str, Any]:
        passphrase = payload.get("passphrase")
        if not passphrase:
            raise HTTPException(
                status_code=400, detail="passphrase is required"
            )
        org_id = payload.get("org_id")
        dest_dir_raw = payload.get("dest_dir")
        dest_dir = Path(dest_dir_raw) if dest_dir_raw else None
        try:
            return await backup_service.create_backup(
                org_id=org_id, passphrase=passphrase, dest_dir=dest_dir
            )
        except BackupRestoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/admin/backups",
        summary="列出备份历史 (M3 Infra)",
    )
    async def list_backups_endpoint(
        org_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        rows = await backup_service.list_backups(org_id=org_id, limit=limit)
        return {"backups": rows, "total": len(rows)}

    @router.get(
        "/admin/backups/{backup_id}",
        summary="查看单条备份记录详情 (M3 Infra)",
    )
    async def get_backup_detail(backup_id: int) -> dict[str, Any]:
        row = await backup_service.get_backup(backup_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"backup {backup_id} not found"
            )
        return row

    @router.post(
        "/admin/backups/{backup_id}/restore",
        summary="校验并恢复备份 (M3 Infra)",
    )
    async def restore_backup_endpoint(
        backup_id: int,
        payload: dict = Body(...),
        overwrite: bool = Query(
            default=False,
            description=(
                "EX-P1-1: 显式覆盖已存在的目标 DB 文件；缺省 false 触发 409"
            ),
        ),
        _user: str = Depends(require_permission("admin_backup", "restore")),
    ) -> dict[str, Any]:
        passphrase = payload.get("passphrase")
        if not passphrase:
            raise HTTPException(
                status_code=400, detail="passphrase is required"
            )
        target_db_raw = payload.get("target_db_path")
        target_db_path = Path(target_db_raw) if target_db_raw else None
        dry_run = bool(payload.get("dry_run", False))
        # ``overwrite`` may also be passed inside the JSON body for
        # callers that find query strings awkward; the query-string
        # wins so URL surface stays canonical.
        body_overwrite = bool(payload.get("overwrite", False))
        effective_overwrite = overwrite or body_overwrite
        try:
            return await backup_service.restore_backup(
                backup_id=backup_id,
                passphrase=passphrase,
                target_db_path=target_db_path,
                dry_run=dry_run,
                overwrite=effective_overwrite,
            )
        except BackupRestoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete(
        "/admin/backups/{backup_id}",
        summary="删除备份记录 + 物理文件 (M3 Infra)",
    )
    async def delete_backup_endpoint(
        backup_id: int,
        _user: str = Depends(require_permission("admin_backup", "create")),
    ) -> dict[str, Any]:
        try:
            return await backup_service.delete_backup(backup_id)
        except BackupRestoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/admin/backups/{backup_id}/download",
        summary="下载备份归档 (.tar.gz, M3 Infra)",
    )
    async def download_backup_endpoint(backup_id: int) -> FileResponse:
        row = await backup_service.get_backup(backup_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"backup {backup_id} not found"
            )
        path = Path(row["backup_path"])
        if not path.exists():
            raise HTTPException(
                status_code=410,
                detail=f"backup file missing on disk: {path.name}",
            )
        return FileResponse(
            path=str(path),
            media_type="application/gzip",
            filename=path.name,
        )

    # ----------------------------- system info ------------------------

    @router.get(
        "/admin/system-info",
        summary="系统信息 (M3 Infra; Tauri 端 finance_system_info 的 REST 同源)",
    )
    async def system_info_endpoint() -> dict[str, Any]:
        meta = await read_key_meta(service.db.conn, GLOBAL_COMPONENT)
        version_rows = await rotation_service.list_versions()
        last_rotation_at: str | None = None
        for r in version_rows:
            if r.get("rotated_at"):
                if (
                    last_rotation_at is None
                    or (r["rotated_at"] or "") > last_rotation_at
                ):
                    last_rotation_at = r["rotated_at"]
        backups = await backup_service.list_backups(limit=500)
        backup_count = len(backups)
        # Current "live" key_version = highest active row, else 1 when
        # encryption is enabled, else 0.
        active_versions = [
            r["key_version"] for r in version_rows if r.get("status") == "active"
        ]
        if active_versions:
            current_key_version = max(active_versions)
        elif meta and meta.enabled:
            current_key_version = 1
        else:
            current_key_version = 0
        return {
            "schema_version": SCHEMA_VERSION,
            "key_version": current_key_version,
            "encryption_enabled": bool(meta and meta.enabled),
            "kdf_iterations": meta.kdf_iterations if meta else 0,
            "seed_source": meta.seed_source if meta else None,
            "backup_count": backup_count,
            "last_rotation_at": last_rotation_at,
            "openakita_version": _openakita_version(),
            "key_store_backend": "OS keyring (with env-var fallback)",
        }


__all__ = ["register_infra_endpoints"]
