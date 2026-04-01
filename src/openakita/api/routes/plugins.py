"""Plugin management REST API."""

from __future__ import annotations

import asyncio
import json
import logging
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...config import settings
from ...plugins import installer
from ...plugins.installer import PluginInstallError
from ...plugins.manifest import ManifestError, parse_manifest
from ...plugins.state import PluginState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


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
            detail="Plugin manager is not available",
        )
    return pm


class InstallBody(BaseModel):
    source: str = Field(..., min_length=1)


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
                **meta, **loaded_info,
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
    failed_ids = {pid for pid, _ in pm.list_failed()}
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
        return {"plugins": plugins, "failed": failed}
    except Exception as e:
        logger.exception("Failed to list plugins")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/install")
async def install_plugin(body: InstallBody, request: Request) -> dict[str, str]:
    plugins_dir = _plugins_dir()
    src = body.source.strip()
    try:
        if src.startswith(("http://", "https://")):
            plugin_id = await asyncio.to_thread(installer.install_from_url, src, plugins_dir)
        else:
            plugin_id = await asyncio.to_thread(
                installer.install_from_path, Path(src), plugins_dir
            )
    except (PluginInstallError, ValueError, OSError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("Unexpected error installing plugin from %s", src)
        raise HTTPException(status_code=500, detail=str(e)) from e

    pm = _get_plugin_manager(request)
    if pm is not None:
        try:
            await pm.reload_plugin(plugin_id)
        except Exception as e:
            logger.warning("Plugin '%s' installed but failed to hot-load: %s", plugin_id, e)

    return {"plugin_id": plugin_id}


@router.delete("/{plugin_id}")
async def uninstall_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    plugins_dir = _plugins_dir()
    state_path = _plugin_state_path()
    pm = _get_plugin_manager(request)
    if pm:
        await pm.unload_plugin(plugin_id)
        pm.state.remove_plugin(plugin_id)
        pm.state.save(state_path)
    else:
        state = PluginState.load(state_path)
        state.remove_plugin(plugin_id)
        state.save(state_path)

    await asyncio.to_thread(installer.uninstall, plugin_id, plugins_dir)
    return {"ok": True}


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    pm = _require_manager(request)
    await pm.enable_plugin(plugin_id)
    return {"ok": True}


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    pm = _require_manager(request)
    await pm.disable_plugin(plugin_id)
    return {"ok": True}


def _plugin_config_path(plugin_id: str) -> Path:
    return _plugins_dir() / plugin_id / "config.json"


@router.get("/{plugin_id}/config")
async def get_plugin_config(plugin_id: str) -> dict[str, Any]:
    path = _plugin_config_path(plugin_id)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Plugin config read failed for %s: %s", plugin_id, e)
        raise HTTPException(status_code=500, detail="Invalid plugin config file") from e


@router.put("/{plugin_id}/config")
async def update_plugin_config(
    plugin_id: str,
    body: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    path = _plugin_config_path(plugin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=500, detail="Existing config is invalid JSON") from e
    current.update(body)
    path.write_text(
        json.dumps(current, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return current


@router.get("/{plugin_id}/readme")
async def get_plugin_readme(plugin_id: str) -> dict[str, str]:
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(status_code=404, detail="Plugin not found")
    readme = _read_readme(plugin_dir)
    return {"readme": readme}


@router.get("/{plugin_id}/schema")
async def get_plugin_config_schema(plugin_id: str) -> dict[str, Any]:
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(status_code=404, detail="Plugin not found")
    schema = _read_config_schema(plugin_dir)
    if schema is None:
        return {"schema": None}
    return {"schema": schema}


class PermissionGrantBody(BaseModel):
    permissions: list[str] = Field(..., min_length=1)
    reload: bool = Field(True, description="Reload plugin after granting permissions")


@router.post("/{plugin_id}/permissions/grant")
async def grant_permissions(
    plugin_id: str, body: PermissionGrantBody, request: Request
) -> dict[str, Any]:
    """Grant permissions to a plugin and optionally reload it."""
    pm = _require_manager(request)
    pm.approve_permissions(plugin_id, body.permissions)
    if body.reload:
        await pm.reload_plugin(plugin_id)
    return {"ok": True, "granted": body.permissions}


class PermissionRevokeBody(BaseModel):
    permissions: list[str] = Field(..., min_length=1)
    reload: bool = Field(True, description="Reload plugin after revoking permissions")


@router.post("/{plugin_id}/permissions/revoke")
async def revoke_permissions(
    plugin_id: str, body: PermissionRevokeBody, request: Request
) -> dict[str, Any]:
    """Revoke permissions from a plugin and optionally reload it."""
    pm = _require_manager(request)
    pm.revoke_permissions(plugin_id, body.permissions)
    if body.reload:
        await pm.reload_plugin(plugin_id)
    return {"ok": True, "revoked": body.permissions}


@router.get("/{plugin_id}/permissions")
async def get_plugin_permissions(plugin_id: str, request: Request) -> dict[str, Any]:
    """Get detailed permission info for a plugin."""
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(status_code=404, detail="Plugin not found")
    try:
        manifest = parse_manifest(plugin_dir)
    except ManifestError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    from ...plugins.manifest import BASIC_PERMISSIONS, ADVANCED_PERMISSIONS, SYSTEM_PERMISSIONS

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
        perm_details.append({
            "permission": p,
            "level": level,
            "granted": p in granted or p in BASIC_PERMISSIONS,
        })

    return {
        "plugin_id": plugin_id,
        "permission_level": manifest.max_permission_level,
        "permissions": perm_details,
    }


@router.post("/{plugin_id}/reload")
async def reload_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
    """Reload a plugin (useful after granting permissions or changing config)."""
    pm = _require_manager(request)
    await pm.reload_plugin(plugin_id)
    return {"ok": True}


@router.get("/{plugin_id}/logs")
async def get_plugin_logs(
    plugin_id: str,
    request: Request,
    lines: int = 100,
) -> dict[str, str]:
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
    return {"logs": text}


@router.get("/{plugin_id}/icon")
async def get_plugin_icon(plugin_id: str) -> Response:
    """Serve the plugin's icon file (png/svg/jpg)."""
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(status_code=404, detail="Plugin not found")
    icon_name = _find_icon(plugin_dir)
    if icon_name is None:
        raise HTTPException(status_code=404, detail="No icon file")
    icon_path = plugin_dir / icon_name
    data = icon_path.read_bytes()
    ext = icon_path.suffix.lower()
    media_map = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    return Response(content=data, media_type=media_map.get(ext, "application/octet-stream"))


@router.post("/{plugin_id}/open-folder")
async def open_plugin_folder(plugin_id: str) -> dict[str, str]:
    """Return the absolute path so frontend can open it via Tauri/OS."""
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(status_code=404, detail="Plugin not found")
    return {"path": str(plugin_dir.resolve())}


@router.get("/{plugin_id}/export")
async def export_plugin(plugin_id: str) -> Response:
    """Export a plugin as a .zip file for sharing."""
    plugin_dir = _plugins_dir() / plugin_id
    if not plugin_dir.is_dir():
        raise HTTPException(status_code=404, detail="Plugin not found")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(plugin_dir.rglob("*")):
            if file.is_file():
                arc_name = f"{plugin_id}/{file.relative_to(plugin_dir)}"
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
    {"slug": "channel", "name": "Chat Providers", "icon": "message-circle"},
    {"slug": "llm", "name": "AI Models", "icon": "cpu"},
    {"slug": "knowledge", "name": "Productivity", "icon": "book-open"},
    {"slug": "tool", "name": "Tools & Automation", "icon": "wrench"},
    {"slug": "memory", "name": "Memory", "icon": "brain"},
    {"slug": "hook", "name": "Hooks & Extensions", "icon": "git-branch"},
    {"slug": "skill", "name": "Skills", "icon": "star"},
    {"slug": "mcp", "name": "MCP Servers", "icon": "plug"},
]


@router.get("/hub/categories")
async def list_categories() -> list[dict]:
    return PLUGIN_CATEGORIES


@router.get("/hub/search")
async def hub_search(
    q: str = "",
    category: str = "",
) -> dict[str, Any]:
    return {
        "query": q,
        "category": category,
        "results": [],
        "total": 0,
        "message": "Plugin marketplace coming soon",
    }
