"""Plugin management REST API."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from ...config import settings
from ...plugins import installer
from ...plugins.errors import PluginErrorCode, make_error_response
from ...plugins.installer import InstallProgress, PluginInstallError
from ...plugins.manifest import ManifestError, parse_manifest
from ...plugins.state import PluginState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])
_plugin_op_lock = asyncio.Lock()


def _plugins_dir() -> Path:
    return Path(settings.project_root) / "data" / "plugins"


def _plugin_state_path() -> Path:
    return Path(settings.project_root) / "data" / "plugin_state.json"


def _get_plugin_manager(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return None
    return getattr(agent, "_plugin_manager", None)


def _require_manager(request: Request):
    pm = _get_plugin_manager(request)
    if pm is None:
        raise HTTPException(
            status_code=503,
            detail=make_error_response(PluginErrorCode.MANAGER_UNAVAILABLE),
        )
    return pm


_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-_.]{0,128}$")


def _check_plugin_id(plugin_id: str) -> None:
    """Validate plugin_id to prevent path traversal."""
    if not _SAFE_ID_RE.match(plugin_id):
        raise HTTPException(
            status_code=400,
            detail=make_error_response(PluginErrorCode.INVALID_ID),
        )


def _read_readme(plugin_dir: Path) -> str:
    for name in ("README.md", "readme.md", "README.txt", "README"):
        p = plugin_dir / name
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="ignore")[:8000]
            except OSError:
                pass
    return ""


def _read_config_schema(plugin_dir: Path) -> dict[str, Any] | None:
    p = plugin_dir / "config_schema.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


_ICON_NAMES = ("icon.png", "icon.svg", "logo.png", "logo.svg", "icon.jpg", "logo.jpg")


def _find_icon(plugin_dir: Path) -> str | None:
    """Return the filename of the first matching icon file, or None."""
    for name in _ICON_NAMES:
        if (plugin_dir / name).is_file():
            return name
    return None


def _manifest_meta(manifest, plugin_dir: Path) -> dict[str, Any]:
    """Common metadata extracted from manifest + files."""
    icon_file = _find_icon(plugin_dir)
    # i18n surfacing: pass through the manifest's per-language fields so
    # the frontend can pick the right text without doing a second API call.
    # We always include the dict (even if empty) so the client can do a
    # straightforward `meta.display_name?.[lang] ?? meta.name` lookup.
    display_name_i18n: dict[str, str] = {}
    if getattr(manifest, "display_name_zh", ""):
        display_name_i18n["zh"] = manifest.display_name_zh
    if getattr(manifest, "display_name_en", ""):
        display_name_i18n["en"] = manifest.display_name_en
    description_i18n: dict[str, str] = dict(getattr(manifest, "description_i18n", {}) or {})
    ui_title = ""
    ui_title_i18n: dict[str, str] = {}
    ui_cfg = getattr(manifest, "ui", None)
    if ui_cfg is not None:
        ui_title = getattr(ui_cfg, "title", "") or ""
        ui_title_i18n = dict(getattr(ui_cfg, "title_i18n", {}) or {})

    meta: dict[str, Any] = {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "type": manifest.plugin_type,
        "category": manifest.category,
        "description": manifest.description,
        "author": manifest.author,
        "homepage": manifest.homepage,
        "permissions": manifest.permissions,
        "permission_level": manifest.max_permission_level,
        "tags": manifest.tags,
        "has_readme": (plugin_dir / "README.md").is_file() or (plugin_dir / "readme.md").is_file(),
        "has_config_schema": (plugin_dir / "config_schema.json").is_file(),
        "has_icon": icon_file is not None,
        "onboard": manifest.raw.get("onboard"),
        # i18n: clients should prefer these when present, fall back to `name`/`description`.
        "display_name_i18n": display_name_i18n,
        "description_i18n": description_i18n,
        "ui_title": ui_title,
        "ui_title_i18n": ui_title_i18n,
    }
    return meta


def _build_plugin_list(pm, plugins_dir: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    state = pm.state if pm is not None else PluginState.load(_plugin_state_path())
    failed: dict[str, str] = dict(pm.list_failed()) if pm else {}
    loaded_by_id: dict[str, dict[str, Any]] = {}
    if pm:
        for entry in pm.list_loaded():
            loaded_by_id[entry["id"]] = entry

    plugins: list[dict[str, Any]] = []
    if not plugins_dir.is_dir():
        return plugins, failed

    for child in sorted(plugins_dir.iterdir()):
        if not child.is_dir() or not (child / "plugin.json").is_file():
            continue
        try:
            manifest = parse_manifest(child)
        except ManifestError as e:
            plugins.append(
                {
                    "id": child.name,
                    "status": "invalid",
                    "error": str(e),
                },
            )
            continue

        pid = manifest.id
        enabled = state.is_enabled(pid)
        entry = state.get_entry(pid)
        meta = _manifest_meta(manifest, child)

        from ...plugins.manifest import BASIC_PERMISSIONS as _BASIC_PERMS

        granted_perms = entry.granted_permissions if entry else []
        granted_set = set(granted_perms) | _BASIC_PERMS
        all_requested = manifest.permissions
        pending_perms = [p for p in all_requested if p not in granted_set]

        if pm and pid in loaded_by_id:
            loaded_info = loaded_by_id[pid]
            pending_perms = loaded_info.get("pending_permissions", pending_perms)
            granted_perms = loaded_info.get("granted_permissions", granted_perms)
            row = {
                **meta,
                **loaded_info,
                "status": "loaded",
                "enabled": enabled,
                "granted_permissions": granted_perms,
                "pending_permissions": pending_perms,
            }
        elif pid in failed:
            row = {
                **meta,
                "status": "failed",
                "error": failed[pid],
                "enabled": enabled,
                "granted_permissions": granted_perms,
                "pending_permissions": pending_perms,
            }
        elif not enabled:
            row = {
                **meta,
                "status": "disabled",
                "enabled": False,
                "disabled_reason": entry.disabled_reason if entry else "",
                "granted_permissions": granted_perms,
                "pending_permissions": pending_perms,
            }
        else:
            row = {
                **meta,
                "status": "installed",
                "enabled": True,
                "granted_permissions": granted_perms,
                "pending_permissions": pending_perms,
            }
        plugins.append(row)

    return plugins, failed


async def _sync_new_plugins(pm, plugins_dir: Path) -> None:
    """Detect plugins on disk that are not yet loaded and hot-load them.

    Called by list_plugins so that clicking "Refresh" in the UI picks up
    manually placed plugins without requiring a backend restart.
    """
    if pm is None or not plugins_dir.is_dir():
        return
    loaded_ids = {e["id"] for e in pm.list_loaded()}
    failed_ids = set(pm.list_failed())
    state = pm.state
    for child in plugins_dir.iterdir():
        if not child.is_dir() or not (child / "plugin.json").is_file():
            continue
        try:
            manifest = parse_manifest(child)
        except ManifestError:
            continue
        pid = manifest.id
        if pid in loaded_ids or pid in failed_ids:
            continue
        if not state.is_enabled(pid):
            continue
        try:
            await pm.reload_plugin(pid)
            logger.info("Hot-loaded new plugin '%s' on refresh", pid)
        except Exception as e:
            logger.warning("Failed to hot-load plugin '%s': %s", pid, e)


@router.get("/list")
async def list_plugins(request: Request) -> dict[str, Any]:
    try:
        pm = _get_plugin_manager(request)
        plugins_dir = _plugins_dir()
        await _sync_new_plugins(pm, plugins_dir)
        plugins, failed = _build_plugin_list(pm, plugins_dir)
        return {"ok": True, "data": {"plugins": plugins, "failed": failed}}
    except Exception as e:
        logger.exception("Failed to list plugins")
        raise HTTPException(
            status_code=500,
            detail=make_error_response(PluginErrorCode.INTERNAL_ERROR),
        ) from e


@router.get("/ui-apps")
async def list_ui_plugins(request: Request) -> list[dict]:
    """Return all enabled plugins that have a UI, for sidebar rendering."""
    pm = _get_plugin_manager(request)
    if pm is None:
        return []
    try:
        return pm.list_ui_plugins()
    except Exception:
        logger.exception("Failed to list UI plugins")
        return []


class InstallBody(BaseModel):
    source: str = Field(..., min_length=1)
    background: bool = Field(
        False, description="Return immediately with install_id for SSE progress tracking"
    )


_PROGRESS_TTL = 120


async def _do_install(src: str, plugins_dir: Path, progress: InstallProgress, request: Request):
    """Core install logic shared by sync and background modes."""
    if installer._is_git_url(src):
        plugin_id = await asyncio.to_thread(
            installer.install_from_git,
            src,
            plugins_dir,
            progress=progress,
        )
    elif src.startswith(("http://", "https://")):
        plugin_id = await asyncio.to_thread(
            installer.install_from_url,
            src,
            plugins_dir,
            progress=progress,
        )
    else:
        local = Path(src)
        if (local / "plugin.json").is_file():
            pm_for_dev = _get_plugin_manager(request)
            dev_mode_on = bool(
                pm_for_dev is not None
                and getattr(pm_for_dev.state, "dev_mode_enabled", False)
            )
            plugin_id = await asyncio.to_thread(
                installer.install_from_path,
                local,
                plugins_dir,
                dev_mode=dev_mode_on,
            )
        else:
            plugin_id = await asyncio.to_thread(installer.install_bundle, local, plugins_dir)

    pm = _get_plugin_manager(request)
    hot_loaded = False
    if pm is not None:
        try:
            await pm.reload_plugin(plugin_id)
            hot_loaded = True
        except Exception as e:
            logger.warning("Plugin '%s' installed but failed to hot-load: %s", plugin_id, e)

    return plugin_id, hot_loaded


@router.post("/install")
async def install_plugin(body: InstallBody, request: Request) -> dict[str, Any]:
    plugins_dir = _plugins_dir()
    src = body.source.strip()
    progress = InstallProgress()
    install_id = uuid.uuid4().hex[:12]
    installer._register_progress(install_id, progress)

    if body.background:

        async def _background():
            async with _plugin_op_lock:
                try:
                    plugin_id, hot_loaded = await _do_install(src, plugins_dir, progress, request)
                    progress.finish(result={"plugin_id": plugin_id, "hot_loaded": hot_loaded})
                except Exception as e:
                    logger.exception("Background install failed for %s", src)
                    progress.finish(error=str(e))
            await asyncio.sleep(_PROGRESS_TTL)
            installer._unregister_progress(install_id)

        asyncio.create_task(_background())
        return {"ok": True, "data": {"install_id": install_id}}

    async with _plugin_op_lock:
        try:
            plugin_id, hot_loaded = await _do_install(src, plugins_dir, progress, request)
        except PluginInstallError as e:
            progress.finish(error=str(e))
            installer._unregister_progress(install_id)
            err_str = str(e)
            if "not a valid zip" in err_str.lower():
                code = PluginErrorCode.ZIP_INVALID
            elif "size limit" in err_str.lower() or "file count limit" in err_str.lower():
                code = PluginErrorCode.ZIP_BOMB
            elif "plugin.json" in err_str.lower():
                code = PluginErrorCode.MANIFEST_NOT_FOUND
            elif "network" in err_str.lower() or "http" in err_str.lower():
                code = PluginErrorCode.NETWORK_ERROR
            else:
                code = PluginErrorCode.INSTALL_FAILED
            raise HTTPException(
                status_code=400, detail=make_error_response(code, detail=err_str)
            ) from e
        except Exception as e:
            progress.finish(error=str(e))
            installer._unregister_progress(install_id)
            logger.exception("Unexpected error installing plugin from %s", src)
            raise HTTPException(
                status_code=500,
                detail=make_error_response(PluginErrorCode.INTERNAL_ERROR),
            ) from e

        progress.finish(result={"plugin_id": plugin_id, "hot_loaded": hot_loaded})
        installer._unregister_progress(install_id)
        return {
            "ok": True,
            "data": {
                "plugin_id": plugin_id,
                "hot_loaded": hot_loaded,
                "install_id": install_id,
            },
        }


@router.get("/install/progress/{install_id}")
async def install_progress_sse(install_id: str):
    """SSE endpoint for real-time install progress. Frontend connects here after POST /install."""

    async def _event_stream():
        progress = installer.get_install_progress(install_id)
        if progress is None:
            yield f"data: {json.dumps({'stage': 'done', 'message': '安装已完成', 'percent': 100, 'finished': True, 'error': ''})}\n\n"
            return
        while True:
            snap = progress.snapshot()
            yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            if snap["finished"]:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/{plugin_id}")
async def uninstall_plugin(
    plugin_id: str,
    request: Request,
    purge_data: bool = False,
) -> dict[str, Any]:
    """Uninstall a plugin.

    Query params:
      purge_data: if true, also delete ``data/plugin_data/<id>``
                  (the plugin's persistent storage). Defaults to false to
                  avoid surprising data loss.

    Status codes:
      200 — fully uninstalled (code dir gone; data purged if requested)
      207 — partially uninstalled (dir survived, but db files cleared so the
            next install / reinstall will not be blocked); UI should advise
            the user to retry after restart for a clean state
      409 — completely failed (e.g. dir locked and even db files are held)
    """
    _check_plugin_id(plugin_id)
    async with _plugin_op_lock:
        plugins_dir = _plugins_dir()
        data_root = plugins_dir.parent / "plugin_data"
        state_path = _plugin_state_path()
        pm = _get_plugin_manager(request)

        # 1. Stop the running instance first so the file handles drop.
        if pm:
            await pm.unload_plugin(plugin_id)

        # 2. Try to delete the on-disk plugin directory.
        #
        #    CRITICAL: do NOT touch persistent state until we know the
        #    deletion outcome. The previous order ("remove_plugin then
        #    uninstall") had a nasty failure mode: when uninstall returned
        #    partial/failure, the state file lost the entry but the plugin
        #    directory survived. On the next /list call, _sync_new_plugins
        #    rediscovered the leftover dir, and PluginState.is_enabled()
        #    returns True for unknown ids — so the plugin would silently
        #    "come back to life" after a refresh.
        result = await asyncio.to_thread(
            installer.uninstall,
            plugin_id,
            plugins_dir,
            purge_data=purge_data,
            data_root=data_root,
        )

        warnings: list[str] = list(result.get("warnings") or [])

        # 3. Reconcile state with the actual filesystem outcome.
        if result.get("removed"):
            # Code dir is gone — drop state entry entirely.
            if pm:
                pm.state.remove_plugin(plugin_id)
                pm.state.save(state_path)
            else:
                state = PluginState.load(state_path)
                state.remove_plugin(plugin_id)
                state.save(state_path)
            return {
                "ok": True,
                "data": {
                    "plugin_id": plugin_id,
                    "purged_data": bool(result.get("purged_data")),
                    "warnings": warnings,
                },
            }

        # Partial / total failure: keep an entry in plugin_state so the leftover
        # directory is NOT silently re-discovered & auto-loaded as a "new"
        # plugin on the next refresh. Mark it disabled with a clear reason so
        # the user can see what happened in the UI.
        disabled_reason = (
            "pending_removal_partial" if result.get("partial") else "pending_removal_failed"
        )
        if pm:
            pm.state.disable(plugin_id, reason=disabled_reason)
            pm.state.save(state_path)
        else:
            state = PluginState.load(state_path)
            state.disable(plugin_id, reason=disabled_reason)
            state.save(state_path)

        if result.get("partial"):
            # 207 Multi-Status — surface the partial outcome to the UI.
            # Returned via JSONResponse (not HTTPException) so the front-end
            # treats it as a 2xx response and can read the body normally.
            return JSONResponse(
                status_code=207,
                content={
                    "ok": False,
                    "data": {
                        "plugin_id": plugin_id,
                        "partial": True,
                        "purged_data": bool(result.get("purged_data")),
                        "warnings": warnings,
                    },
                    "error": {
                        "code": PluginErrorCode.UNINSTALL_FAILED.value,
                        "message": "插件目录无法完全删除",
                        "guidance": "已尽力清理 db 文件，建议重启后端后重新安装以彻底清理",
                        "detail": "; ".join(warnings),
                    },
                },
            )

        # Total failure — 409 Conflict (resource is in a state that prevents the op).
        raise HTTPException(
            status_code=409,
            detail=make_error_response(
                PluginErrorCode.UNINSTALL_FAILED,
                detail="; ".join(warnings) or "目录无法删除",
            ),
        )


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    _check_plugin_id(plugin_id)
    async with _plugin_op_lock:
        pm = _require_manager(request)
        await pm.enable_plugin(plugin_id)
        return {"ok": True, "data": {"plugin_id": plugin_id, "enabled": True}}


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    _check_plugin_id(plugin_id)
    async with _plugin_op_lock:
        pm = _require_manager(request)
        await pm.disable_plugin(plugin_id)
        return {"ok": True, "data": {"plugin_id": plugin_id, "enabled": False}}


def _plugin_config_path(plugin_id: str) -> Path:
    return _plugins_dir() / plugin_id / "config.json"


@router.get("/{plugin_id}/config")
async def get_plugin_config(plugin_id: str) -> dict[str, Any]:
    _check_plugin_id(plugin_id)
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=make_error_response(PluginErrorCode.NOT_FOUND),
        )
    path = _plugin_config_path(plugin_id)
    if not path.is_file():
        return {"ok": True, "data": {}}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        return {"ok": True, "data": config}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Plugin config read failed for %s: %s", plugin_id, e)
        raise HTTPException(
            status_code=500,
            detail=make_error_response(PluginErrorCode.CONFIG_INVALID),
        ) from e


@router.put("/{plugin_id}/config")
async def update_plugin_config(
    plugin_id: str,
    body: Annotated[dict[str, Any], Body()],
    request: Request,
) -> dict[str, Any]:
    _check_plugin_id(plugin_id)
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=make_error_response(PluginErrorCode.NOT_FOUND),
        )
    path = _plugin_config_path(plugin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=500,
                detail=make_error_response(PluginErrorCode.CONFIG_INVALID),
            ) from e
    current.update(body)

    schema = _read_config_schema(plugin_dir)
    if schema is not None:
        try:
            from jsonschema import ValidationError as JsonSchemaError
            from jsonschema import validate

            validate(instance=current, schema=schema)
        except JsonSchemaError as ve:
            raise HTTPException(
                status_code=400,
                detail=make_error_response(
                    PluginErrorCode.CONFIG_INVALID,
                    detail=ve.message,
                ),
            ) from ve
        except ImportError:
            logger.debug("jsonschema not installed, skipping config validation")
        except Exception as ve:
            logger.debug("Config schema validation error: %s", ve)

    path.write_text(
        json.dumps(current, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    pm = _get_plugin_manager(request)
    if pm is not None:
        hook_reg = getattr(pm, "_hook_registry", None)
        if hook_reg is not None:
            try:
                await hook_reg.dispatch(
                    "on_config_change",
                    plugin_id=plugin_id,
                    config=current,
                )
            except Exception:
                logger.debug("on_config_change dispatch failed for '%s'", plugin_id)
    return {"ok": True, "data": current}


@router.get("/{plugin_id}/readme")
async def get_plugin_readme(plugin_id: str) -> dict[str, str]:
    _check_plugin_id(plugin_id)
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=make_error_response(PluginErrorCode.NOT_FOUND),
        )
    readme = _read_readme(plugin_dir)
    return {"ok": True, "data": {"readme": readme}}


@router.get("/{plugin_id}/schema")
async def get_plugin_config_schema(plugin_id: str) -> dict[str, Any]:
    _check_plugin_id(plugin_id)
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=make_error_response(PluginErrorCode.NOT_FOUND),
        )
    schema = _read_config_schema(plugin_dir)
    return {"ok": True, "data": {"schema": schema}}


class PermissionGrantBody(BaseModel):
    permissions: list[str] = Field(..., min_length=1)
    reload: bool = Field(True, description="Reload plugin after granting permissions")


@router.post("/{plugin_id}/permissions/grant")
async def grant_permissions(
    plugin_id: str, body: PermissionGrantBody, request: Request
) -> dict[str, Any]:
    """Grant permissions to a plugin and optionally reload it."""
    _check_plugin_id(plugin_id)
    pm = _require_manager(request)
    pm.approve_permissions(plugin_id, body.permissions)
    if body.reload:
        await pm.reload_plugin(plugin_id)
    return {"ok": True, "data": {"granted": body.permissions}}


class PermissionRevokeBody(BaseModel):
    permissions: list[str] = Field(..., min_length=1)
    reload: bool = Field(True, description="Reload plugin after revoking permissions")


@router.post("/{plugin_id}/permissions/revoke")
async def revoke_permissions(
    plugin_id: str, body: PermissionRevokeBody, request: Request
) -> dict[str, Any]:
    """Revoke permissions from a plugin and optionally reload it."""
    _check_plugin_id(plugin_id)
    pm = _require_manager(request)
    pm.revoke_permissions(plugin_id, body.permissions)
    if body.reload:
        await pm.reload_plugin(plugin_id)
    return {"ok": True, "data": {"revoked": body.permissions}}


@router.get("/{plugin_id}/permissions")
async def get_plugin_permissions(plugin_id: str, request: Request) -> dict[str, Any]:
    """Get detailed permission info for a plugin."""
    _check_plugin_id(plugin_id)
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=make_error_response(PluginErrorCode.NOT_FOUND),
        )
    try:
        manifest = parse_manifest(plugin_dir)
    except ManifestError as e:
        logger.warning("Manifest error for '%s': %s", plugin_id, e)
        raise HTTPException(
            status_code=400,
            detail=make_error_response(PluginErrorCode.INVALID_MANIFEST),
        ) from e

    from ...plugins.manifest import ADVANCED_PERMISSIONS, BASIC_PERMISSIONS, SYSTEM_PERMISSIONS

    state = _get_plugin_manager(request)
    if state:
        entry = state.state.get_entry(plugin_id)
        granted = entry.granted_permissions if entry else list(BASIC_PERMISSIONS)
    else:
        granted = list(BASIC_PERMISSIONS)

    perm_details = []
    for p in manifest.permissions:
        if p in BASIC_PERMISSIONS:
            level = "basic"
        elif p in ADVANCED_PERMISSIONS:
            level = "advanced"
        elif p in SYSTEM_PERMISSIONS:
            level = "system"
        else:
            level = "unknown"
        perm_details.append(
            {
                "permission": p,
                "level": level,
                "granted": p in granted or p in BASIC_PERMISSIONS,
            }
        )

    return {
        "ok": True,
        "data": {
            "plugin_id": plugin_id,
            "permission_level": manifest.max_permission_level,
            "permissions": perm_details,
        },
    }


@router.post("/{plugin_id}/reload")
async def reload_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    """Reload a plugin (useful after granting permissions or changing config)."""
    _check_plugin_id(plugin_id)
    async with _plugin_op_lock:
        pm = _require_manager(request)
        await pm.reload_plugin(plugin_id)
        return {"ok": True, "data": {"plugin_id": plugin_id}}


@router.get("/{plugin_id}/logs")
async def get_plugin_logs(
    plugin_id: str,
    request: Request,
    lines: int = 100,
) -> dict[str, str]:
    _check_plugin_id(plugin_id)
    pm = _get_plugin_manager(request)
    if pm is not None:
        text = pm.get_plugin_logs(plugin_id, lines)
    else:
        log_file = _plugins_dir() / plugin_id / "logs" / f"{plugin_id}.log"
        if log_file.is_file():
            all_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            text = "\n".join(all_lines[-lines:])
        else:
            text = f"No logs found for plugin '{plugin_id}'"
    return {"ok": True, "data": {"logs": text}}


@router.get("/{plugin_id}/icon")
async def get_plugin_icon(plugin_id: str) -> Response:
    """Serve the plugin's icon file (png/svg/jpg)."""
    _check_plugin_id(plugin_id)
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=make_error_response(PluginErrorCode.NOT_FOUND),
        )
    icon_name = _find_icon(plugin_dir)
    if icon_name is None:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(PluginErrorCode.NOT_FOUND, detail="无图标文件"),
        )
    icon_path = plugin_dir / icon_name
    data = icon_path.read_bytes()
    ext = icon_path.suffix.lower()
    media_map = {
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    return Response(content=data, media_type=media_map.get(ext, "application/octet-stream"))


@router.post("/{plugin_id}/open-folder")
async def open_plugin_folder(plugin_id: str) -> dict[str, str]:
    """Return the absolute path so frontend can open it via Tauri/OS."""
    _check_plugin_id(plugin_id)
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=make_error_response(PluginErrorCode.NOT_FOUND),
        )
    return {"ok": True, "data": {"path": str(plugin_dir.resolve())}}


@router.get("/{plugin_id}/export")
async def export_plugin(plugin_id: str) -> Response:
    """Export a plugin as a .zip file for sharing."""
    _check_plugin_id(plugin_id)
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=make_error_response(PluginErrorCode.NOT_FOUND),
        )

    _EXPORT_EXCLUDE_DIRS = {"logs", "deps", "__pycache__", ".env", "node_modules"}
    _EXPORT_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per file

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(plugin_dir.rglob("*")):
            if not file.is_file():
                continue
            rel = file.relative_to(plugin_dir)
            if any(part in _EXPORT_EXCLUDE_DIRS for part in rel.parts):
                continue
            if file.stat().st_size > _EXPORT_MAX_FILE_SIZE:
                continue
            arc_name = f"{plugin_id}/{rel}"
            zf.write(file, arc_name)
    buf.seek(0)
    filename = f"{plugin_id}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Hub / Marketplace ---

PLUGIN_CATEGORIES = [
    {"slug": "channel", "name": "Chat Providers", "name_zh": "聊天通道", "icon": "message-circle"},
    {"slug": "llm", "name": "AI Models", "name_zh": "AI 模型", "icon": "cpu"},
    {"slug": "knowledge", "name": "Productivity", "name_zh": "知识与效率", "icon": "book-open"},
    {"slug": "tool", "name": "Tools & Automation", "name_zh": "工具与自动化", "icon": "wrench"},
    {"slug": "memory", "name": "Memory", "name_zh": "记忆存储", "icon": "brain"},
    {"slug": "hook", "name": "Hooks & Extensions", "name_zh": "钩子与扩展", "icon": "git-branch"},
    {"slug": "skill", "name": "Skills", "name_zh": "技能", "icon": "star"},
    {"slug": "mcp", "name": "MCP Servers", "name_zh": "MCP 服务", "icon": "plug"},
]


@router.get("/hub/categories")
async def list_categories() -> dict[str, Any]:
    return {"ok": True, "data": PLUGIN_CATEGORIES}


@router.get("/hub/search")
async def hub_search(
    q: str = "",
    category: str = "",
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "query": q,
            "category": category,
            "results": [],
            "total": 0,
            "message": "插件市场即将上线",
        },
    }


@router.get("/health")
async def plugin_health(request: Request) -> dict[str, Any]:
    """Plugin system health summary for monitoring dashboards."""
    pm = _get_plugin_manager(request)
    if pm is None:
        return {
            "ok": True,
            "data": {"status": "unavailable", "loaded": 0, "failed": 0, "disabled": 0},
        }
    loaded = pm.list_loaded()
    failed = pm.list_failed()
    disabled_count = 0
    state = pm.state
    if state:
        loaded_ids = {p["id"] for p in loaded}
        failed_ids = set(failed)
        for entry in state.plugins.values():
            if (
                not entry.enabled
                and entry.plugin_id not in loaded_ids
                and entry.plugin_id not in failed_ids
            ):
                disabled_count += 1
    error_tracker = getattr(pm, "_error_tracker", None)
    auto_disabled = []
    if error_tracker is not None:
        for pid in list(getattr(error_tracker, "_disabled", set())):
            auto_disabled.append(pid)
    return {
        "ok": True,
        "data": {
            "status": "healthy" if not failed else "degraded",
            "loaded": len(loaded),
            "failed": len(failed),
            "disabled": disabled_count,
            "auto_disabled": auto_disabled,
            "failed_ids": list(failed.keys()),
        },
    }


@router.get("/updates")
async def check_updates(request: Request) -> dict[str, Any]:
    """Check for available plugin updates. Requires marketplace to be ready."""
    pm = _get_plugin_manager(request)
    installed: list[dict[str, str]] = []
    if pm is not None:
        for info in pm.list_loaded():
            installed.append({"id": info["id"], "version": info.get("version", "?")})
    return {
        "ok": True,
        "data": {
            "installed_count": len(installed),
            "updates_available": [],
            "message": "升级检查功能将在插件市场上线后可用",
        },
    }


@router.post("/{plugin_id}/update")
async def update_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    """Update a specific plugin to the latest version. Requires marketplace."""
    _check_plugin_id(plugin_id)
    return {
        "ok": False,
        "error": {
            "code": "NOT_IMPLEMENTED",
            "message": "一键升级功能将在插件市场上线后可用",
            "guidance": "当前请手动重新安装最新版本",
        },
    }


# --- Developer mode (live-edit local plugins via symlink) -------------------


_VALID_DEV_MODES = ("off", "symlink")


@router.get("/dev-mode")
async def get_dev_mode(request: Request) -> dict[str, Any]:
    """Return current global developer-mode setting.

    Affects how ``install_from_path`` materialises plugins:
      - ``off``     → copy files (default, stable for prod)
      - ``symlink`` → symlink the source dir so on-disk edits are seen
                       after a plain hot-reload, no reinstall required
    """
    pm = _get_plugin_manager(request)
    state_path = _plugin_state_path()
    state = pm.state if pm is not None else PluginState.load(state_path)
    return {
        "ok": True,
        "data": {
            "mode": state.dev_mode,
            "enabled": state.dev_mode_enabled,
            "supported_modes": list(_VALID_DEV_MODES),
        },
    }


class DevModeBody(BaseModel):
    mode: str = Field(..., min_length=1)


@router.put("/dev-mode")
async def set_dev_mode(body: DevModeBody, request: Request) -> dict[str, Any]:
    """Update global developer-mode setting and persist immediately."""
    mode = body.mode.strip().lower()
    if mode not in _VALID_DEV_MODES:
        raise HTTPException(
            status_code=400,
            detail=make_error_response(
                PluginErrorCode.CONFIG_INVALID,
                detail=f"mode must be one of {list(_VALID_DEV_MODES)}",
            ),
        )

    state_path = _plugin_state_path()
    pm = _get_plugin_manager(request)
    if pm is not None:
        pm.state.set_dev_mode(mode)
        pm.state.save(state_path)
        new_mode = pm.state.dev_mode
    else:
        state = PluginState.load(state_path)
        state.set_dev_mode(mode)
        state.save(state_path)
        new_mode = state.dev_mode

    return {
        "ok": True,
        "data": {
            "mode": new_mode,
            "enabled": new_mode != "off",
        },
    }


# --- Background-task diagnostics --------------------------------------------


@router.get("/{plugin_id}/tasks")
async def list_plugin_tasks(plugin_id: str, request: Request) -> dict[str, Any]:
    """List background tasks the plugin scheduled via ``api.spawn_task``.

    Useful for diagnosing leaks: tasks shown here are guaranteed to be
    cancelled and awaited during ``unload_plugin``; tasks created via raw
    ``asyncio.create_task`` are **not** tracked and will not appear.
    """
    _check_plugin_id(plugin_id)
    pm = _require_manager(request)
    loaded = pm.get_loaded(plugin_id)
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail=make_error_response(
                PluginErrorCode.NOT_FOUND,
                detail=f"plugin '{plugin_id}' is not currently loaded",
            ),
        )
    tasks = loaded.api.list_spawned_tasks()
    running = sum(1 for t in tasks if not t["done"])
    return {
        "ok": True,
        "data": {
            "plugin_id": plugin_id,
            "running": running,
            "total": len(tasks),
            "tasks": tasks,
        },
    }
