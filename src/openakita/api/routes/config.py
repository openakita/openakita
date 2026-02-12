"""
Config routes: workspace info, env read/write, endpoints read/write, skills config.

These endpoints mirror the Tauri commands (workspace_read_file, workspace_update_env,
workspace_write_file) but exposed via HTTP so the desktop app can operate in "remote mode"
when connected to an already-running serve instance.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helpers ───────────────────────────────────────────────────────────


def _project_root() -> Path:
    """Return the project root (settings.project_root or cwd)."""
    try:
        from openakita.config import settings
        return Path(settings.project_root)
    except Exception:
        return Path.cwd()


def _parse_env(content: str) -> dict[str, str]:
    """Parse .env file content into a dict (same logic as Tauri bridge)."""
    env: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        env[key] = value
    return env


def _update_env_content(existing: str, entries: dict[str, str]) -> str:
    """Merge entries into existing .env content (preserves comments, order)."""
    lines = existing.splitlines()
    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in entries:
            value = entries[key]
            if value == "":
                # Empty value → delete key (skip line)
                updated_keys.add(key)
                continue
            new_lines.append(f"{key}={value}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # Append new keys that weren't in the existing content
    for key, value in entries.items():
        if key not in updated_keys and value != "":
            new_lines.append(f"{key}={value}")

    return "\n".join(new_lines) + "\n"


# ─── Pydantic models ──────────────────────────────────────────────────


class EnvUpdateRequest(BaseModel):
    entries: dict[str, str]


class EndpointsWriteRequest(BaseModel):
    content: dict  # Full JSON content of llm_endpoints.json


class SkillsWriteRequest(BaseModel):
    content: dict  # Full JSON content of skills.json


# ─── Routes ────────────────────────────────────────────────────────────


@router.get("/api/config/workspace-info")
async def workspace_info():
    """Return current workspace path and basic info."""
    root = _project_root()
    return {
        "workspace_path": str(root),
        "workspace_name": root.name,
        "env_exists": (root / ".env").exists(),
        "endpoints_exists": (root / "data" / "llm_endpoints.json").exists(),
    }


@router.get("/api/config/env")
async def read_env():
    """Read .env file content as key-value pairs."""
    env_path = _project_root() / ".env"
    if not env_path.exists():
        return {"env": {}, "raw": ""}
    content = env_path.read_text(encoding="utf-8")
    env = _parse_env(content)
    # Mask sensitive values for display (keys containing TOKEN, SECRET, PASSWORD, KEY)
    masked = {}
    sensitive_pattern = re.compile(r"(TOKEN|SECRET|PASSWORD|KEY|APIKEY)", re.IGNORECASE)
    for k, v in env.items():
        if sensitive_pattern.search(k) and v:
            masked[k] = v[:4] + "***" + v[-2:] if len(v) > 6 else "***"
        else:
            masked[k] = v
    return {"env": env, "masked": masked, "raw": content}


@router.post("/api/config/env")
async def write_env(body: EnvUpdateRequest):
    """Update .env file with key-value entries (merge, preserving comments)."""
    env_path = _project_root() / ".env"
    existing = ""
    if env_path.exists():
        existing = env_path.read_text(encoding="utf-8")
    new_content = _update_env_content(existing, body.entries)
    env_path.write_text(new_content, encoding="utf-8")
    logger.info(f"[Config API] Updated .env with {len(body.entries)} entries")
    return {"status": "ok", "updated_keys": list(body.entries.keys())}


@router.get("/api/config/endpoints")
async def read_endpoints():
    """Read data/llm_endpoints.json."""
    ep_path = _project_root() / "data" / "llm_endpoints.json"
    if not ep_path.exists():
        return {"endpoints": [], "raw": {}}
    try:
        data = json.loads(ep_path.read_text(encoding="utf-8"))
        return {"endpoints": data.get("endpoints", []), "raw": data}
    except Exception as e:
        return {"error": str(e), "endpoints": [], "raw": {}}


@router.post("/api/config/endpoints")
async def write_endpoints(body: EndpointsWriteRequest):
    """Write data/llm_endpoints.json."""
    ep_path = _project_root() / "data" / "llm_endpoints.json"
    ep_path.parent.mkdir(parents=True, exist_ok=True)
    ep_path.write_text(
        json.dumps(body.content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("[Config API] Updated llm_endpoints.json")
    return {"status": "ok"}


@router.post("/api/config/reload")
async def reload_config(request: Request):
    """Hot-reload LLM endpoints config from disk into the running agent.

    This should be called after writing llm_endpoints.json so the running
    service picks up changes without a full restart.
    """
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return {"status": "ok", "reloaded": False, "reason": "agent not initialized"}

    # Navigate: agent → brain → _llm_client
    brain = getattr(agent, "brain", None) or getattr(agent, "_local_agent", None)
    if brain and hasattr(brain, "brain"):
        brain = brain.brain  # agent wrapper → actual agent → brain
    llm_client = getattr(brain, "_llm_client", None) if brain else None
    if llm_client is None:
        # Try direct attribute on agent
        llm_client = getattr(agent, "_llm_client", None)

    if llm_client is None:
        return {"status": "ok", "reloaded": False, "reason": "llm_client not found"}

    try:
        success = llm_client.reload()
        if success:
            logger.info("[Config API] LLM endpoints reloaded successfully")
            return {
                "status": "ok",
                "reloaded": True,
                "endpoints": len(llm_client.endpoints),
            }
        else:
            return {"status": "ok", "reloaded": False, "reason": "reload returned false"}
    except Exception as e:
        logger.error(f"[Config API] Reload failed: {e}", exc_info=True)
        return {"status": "error", "reloaded": False, "reason": str(e)}


@router.post("/api/config/restart")
async def restart_service(request: Request):
    """触发服务优雅重启。

    流程：设置重启标志 → 触发 shutdown_event → serve() 主循环检测标志后重新初始化。
    前端应在调用后轮询 /api/health 直到服务恢复。
    """
    from openakita import config as cfg

    cfg._restart_requested = True
    shutdown_event = getattr(request.app.state, "shutdown_event", None)
    if shutdown_event is not None:
        logger.info("[Config API] Restart requested, triggering graceful shutdown for restart")
        shutdown_event.set()
        return {"status": "restarting"}
    else:
        logger.warning("[Config API] Restart requested but no shutdown_event available")
        cfg._restart_requested = False
        return {"status": "error", "message": "restart not available in this mode"}


@router.get("/api/config/skills")
async def read_skills_config():
    """Read data/skills.json (skill selection/allowlist)."""
    sk_path = _project_root() / "data" / "skills.json"
    if not sk_path.exists():
        return {"skills": {}}
    try:
        data = json.loads(sk_path.read_text(encoding="utf-8"))
        return {"skills": data}
    except Exception as e:
        return {"error": str(e), "skills": {}}


@router.post("/api/config/skills")
async def write_skills_config(body: SkillsWriteRequest):
    """Write data/skills.json."""
    sk_path = _project_root() / "data" / "skills.json"
    sk_path.parent.mkdir(parents=True, exist_ok=True)
    sk_path.write_text(
        json.dumps(body.content, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("[Config API] Updated skills.json")
    return {"status": "ok"}
