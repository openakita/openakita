"""
Workspace management routes: list, create, switch workspaces.

These endpoints expose the multi-workspace capabilities that were previously
only available via Tauri IPC (list_workspaces, create_workspace,
set_current_workspace).  Web and mobile clients can now manage workspaces
through HTTP.

The workspace registry (state.json) and workspace directories live under
``~/.openakita/`` (or ``OPENAKITA_ROOT``).  The Python backend always runs
in a *single* workspace (determined by ``settings.project_root`` / cwd).
Switching workspace triggers a graceful restart so the backend re-initialises
with the new workspace as its root.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helpers ───────────────────────────────────────────────────────────


def _openakita_home() -> Path:
    env_root = os.environ.get("OPENAKITA_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    return Path.home() / ".openakita"


def _state_file_path() -> Path:
    return _openakita_home() / "state.json"


def _workspaces_dir() -> Path:
    return _openakita_home() / "workspaces"


def _read_state() -> dict:
    """Read state.json.  Keys use camelCase (written by Rust with serde rename_all)."""
    path = _state_file_path()
    if not path.exists():
        return {"workspaces": [], "currentWorkspaceId": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"workspaces": [], "currentWorkspaceId": None}


def _write_state(state: dict) -> None:
    path = _state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _workspace_dir(ws_id: str) -> Path:
    return _workspaces_dir() / ws_id


def _ensure_scaffold(ws_dir: Path) -> None:
    """Create minimal workspace directory structure.

    Mirrors the Rust ``ensure_workspace_scaffold`` logic: creates data/,
    identity/, .env, and copies template files from the repo/package.
    """
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "data").mkdir(exist_ok=True)
    (ws_dir / "identity").mkdir(exist_ok=True)

    env_path = ws_dir / ".env"
    if not env_path.exists():
        env_path.write_text(
            "# OpenAkita workspace environment (managed by Setup Center)\n"
            "#\n"
            "# - Only keys you explicitly set in Setup Center are written here.\n"
            "# - Clearing a value removes the key from this file.\n"
            "# - For the full template, see examples/.env.example\n"
            "\n",
            encoding="utf-8",
        )

    _copy_template_files(ws_dir)


def _copy_template_files(ws_dir: Path) -> None:
    """Copy identity and config templates into a new workspace.

    Tries two source resolution strategies:
    1. Repo-relative paths (dev mode, when running from source)
    2. Package-relative paths (installed mode, wheel/pip install)
    """
    repo_root = _find_repo_root()
    pkg_root = Path(__file__).resolve().parent.parent.parent  # openakita package

    identity_templates = {
        "identity/SOUL.md": "identity/SOUL.md.example",
        "identity/AGENT.md": "identity/AGENT.md.example",
        "identity/USER.md": "identity/USER.md.example",
        "identity/MEMORY.md": "identity/MEMORY.md.example",
    }

    for dest_rel, src_rel in identity_templates.items():
        dest = ws_dir / dest_rel
        if dest.exists():
            continue
        src = _resolve_template(repo_root, pkg_root, src_rel)
        if src and src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    _copy_dir_templates(ws_dir, repo_root, pkg_root, "identity/personas")
    _copy_dir_templates(ws_dir, repo_root, pkg_root, "identity/prompts")
    _copy_dir_templates(ws_dir, repo_root, pkg_root, "identity/runtime")

    llm_dest = ws_dir / "data" / "llm_endpoints.json"
    if not llm_dest.exists():
        src = _resolve_template(repo_root, pkg_root, "data/llm_endpoints.json.example")
        if src and src.exists():
            shutil.copy2(src, llm_dest)


def _copy_dir_templates(ws_dir: Path, repo_root: Path | None,
                        pkg_root: Path, rel_dir: str) -> None:
    src_dir = None
    if repo_root:
        candidate = repo_root / rel_dir
        if candidate.is_dir():
            src_dir = candidate
    if not src_dir:
        candidate = pkg_root / rel_dir
        if candidate.is_dir():
            src_dir = candidate
    if not src_dir:
        return

    dest_dir = ws_dir / rel_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.iterdir():
        if not f.is_file():
            continue
        name = f.name
        if name.endswith(".example"):
            name = name[: -len(".example")]
        dest = dest_dir / name
        if not dest.exists():
            shutil.copy2(f, dest)


def _resolve_template(repo_root: Path | None, pkg_root: Path,
                      rel_path: str) -> Path | None:
    if repo_root:
        candidate = repo_root / rel_path
        if candidate.exists():
            return candidate
    candidate = pkg_root / rel_path
    if candidate.exists():
        return candidate
    return None


def _find_repo_root() -> Path | None:
    """Walk up from the current settings.project_root to find the repo root."""
    try:
        from openakita.config import settings
        p = settings.project_root.resolve()
    except Exception:
        p = Path.cwd().resolve()

    for _ in range(10):
        if (p / "identity").is_dir() and (p / "src" / "openakita").is_dir():
            return p
        if (p / "pyproject.toml").exists() and (p / "identity").is_dir():
            return p
        parent = p.parent
        if parent == p:
            break
        p = parent
    return None


_WS_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


# ─── Routes ────────────────────────────────────────────────────────────


@router.get("/api/workspaces")
async def list_workspaces():
    """List all workspaces from the central state.json registry."""
    state = _read_state()
    current_id = state.get("currentWorkspaceId")
    result = []
    for ws in state.get("workspaces", []):
        ws_id = ws.get("id", "")
        ws_path = str(_workspace_dir(ws_id))
        result.append({
            "id": ws_id,
            "name": ws.get("name", ws_id),
            "path": ws_path,
            "isCurrent": ws_id == current_id,
        })

    if not result:
        try:
            from openakita.config import settings
            pr = settings.project_root.resolve()
            ws_dir = _workspaces_dir().resolve()
            try:
                rel = pr.relative_to(ws_dir)
                ws_id = rel.parts[0] if rel.parts else "default"
            except ValueError:
                ws_id = "default"
            result.append({
                "id": ws_id,
                "name": ws_id,
                "path": str(pr),
                "isCurrent": True,
            })
        except Exception:
            pass

    return {"workspaces": result, "current_workspace_id": current_id}


@router.get("/api/workspaces/current")
async def get_current_workspace():
    """Return info about the workspace the backend is currently running in."""
    try:
        from openakita.config import settings
        pr = settings.project_root.resolve()
    except Exception:
        pr = Path.cwd().resolve()

    state = _read_state()
    current_id = state.get("currentWorkspaceId")

    ws_dir = _workspaces_dir().resolve()
    try:
        rel = pr.relative_to(ws_dir)
        derived_id = rel.parts[0] if rel.parts else None
    except ValueError:
        derived_id = None

    effective_id = current_id or derived_id or "default"

    return {
        "id": effective_id,
        "path": str(pr),
        "name": effective_id,
    }


class CreateWorkspaceRequest(BaseModel):
    id: str
    name: str = ""
    set_current: bool = False


@router.post("/api/workspaces")
async def create_workspace(body: CreateWorkspaceRequest, request: Request):
    """Create a new workspace with scaffold, optionally set as current."""
    ws_id = body.id.strip().lower()

    if not _WS_ID_RE.match(ws_id):
        return {"status": "error", "message": f"Invalid workspace ID: must match {_WS_ID_RE.pattern}"}

    ws_dir = _workspace_dir(ws_id)
    if ws_dir.exists():
        return {"status": "error", "message": f"Workspace directory already exists: {ws_id}"}

    state = _read_state()
    existing_ids = {w.get("id") for w in state.get("workspaces", [])}
    if ws_id in existing_ids:
        return {"status": "error", "message": f"Workspace ID already registered: {ws_id}"}

    try:
        _ensure_scaffold(ws_dir)
    except Exception as e:
        return {"status": "error", "message": f"Failed to create workspace scaffold: {e}"}

    ws_name = body.name.strip() or ws_id
    state.setdefault("workspaces", []).append({"id": ws_id, "name": ws_name})

    if body.set_current:
        state["currentWorkspaceId"] = ws_id

    _write_state(state)

    logger.info(f"[Workspaces] Created workspace '{ws_id}' (name={ws_name}, set_current={body.set_current})")

    return {
        "status": "ok",
        "workspace": {
            "id": ws_id,
            "name": ws_name,
            "path": str(ws_dir),
            "isCurrent": body.set_current,
        },
    }


class SwitchWorkspaceRequest(BaseModel):
    id: str


@router.post("/api/workspaces/switch")
async def switch_workspace(body: SwitchWorkspaceRequest, request: Request):
    """Switch to a different workspace and trigger a graceful backend restart.

    The sequence:
    1. Update currentWorkspaceId in state.json
    2. Ensure target workspace directory has a valid scaffold
    3. Change process cwd to the target workspace
    4. Trigger graceful restart (same mechanism as /api/config/restart)

    The frontend should poll /api/health after calling this endpoint.
    """
    ws_id = body.id.strip()
    state = _read_state()

    registered_ids = {w.get("id") for w in state.get("workspaces", [])}
    if ws_id not in registered_ids:
        ws_dir = _workspace_dir(ws_id)
        if not ws_dir.is_dir():
            return {"status": "error", "message": f"Workspace not found: {ws_id}"}

    ws_dir = _workspace_dir(ws_id)
    try:
        _ensure_scaffold(ws_dir)
    except Exception as e:
        return {"status": "error", "message": f"Failed to prepare workspace: {e}"}

    state["currentWorkspaceId"] = ws_id
    _write_state(state)

    os.chdir(ws_dir)

    from openakita import config as cfg
    cfg._restart_requested = True
    shutdown_event = getattr(request.app.state, "shutdown_event", None)
    if shutdown_event is not None:
        logger.info(f"[Workspaces] Switching to workspace '{ws_id}', triggering restart")
        shutdown_event.set()
        return {"status": "restarting", "workspace_id": ws_id}
    else:
        cfg._restart_requested = False
        os.chdir(cfg.settings.project_root)
        return {"status": "error", "message": "Restart not available in this mode"}
