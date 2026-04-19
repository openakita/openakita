"""
Skills route: GET /api/skills, POST /api/skills/config, GET /api/skills/marketplace

Skill list and configuration management.

This module is only responsible for HTTP adaptation + its own list cache. All operations affecting skill
visibility/content (install/uninstall/reload/content-update/allowlist-change) call ``Agent.propagate_skill_change``
uniformly after completing disk side effects, which is responsible for:
  - Clearing parser/loader cache
  - Re-scanning skill directory
  - Re-applying allowlist
  - Rebuilding SkillCatalog and ``_skill_catalog_text``
  - Syncing handler mappings
  - Notifying AgentInstancePool to reclaim old instances
  - Broadcasting ``SkillEvent`` (HTTP cache invalidation + WebSocket broadcast via event callbacks)

The API layer no longer performs half-complete refreshes, avoiding state inconsistencies across paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)


router = APIRouter()

SKILLS_SH_API = "https://skills.sh/api/search"


_skills_cache: dict | None = None
"""Module-level cache for GET /api/skills response.
Populated on first request, invalidated via the cross-layer on-change callback
registered at the bottom of this module."""


def _invalidate_skills_cache() -> None:
    """Clear the cached skill list so the next GET /api/skills re-scans disk."""
    global _skills_cache
    _skills_cache = None


def _resolve_agent(request: Request):
    """Return real Agent instance (unwrap possible thin wrapper / _local_agent)."""
    from openakita.core.agent import Agent

    agent = getattr(request.app.state, "agent", None)
    if isinstance(agent, Agent):
        return agent
    return getattr(agent, "_local_agent", None)


async def _propagate(request: Request, action: str, *, rescan: bool = True) -> None:
    """Call Agent's unified refresh entry in a worker thread, avoiding event loop blocking."""
    agent = _resolve_agent(request)
    if agent is None or not hasattr(agent, "propagate_skill_change"):
        return
    try:
        await asyncio.to_thread(agent.propagate_skill_change, action, rescan=rescan)
    except Exception as e:
        logger.warning("propagate_skill_change(%s) failed: %s", action, e)


async def _auto_translate_new_skills(request: Request, install_url: str) -> None:
    """Auto-generate i18n translations for skills lacking translations after installation (write to agents/openai.yaml).

    Translation failures do not affect installation results, only logged.
    """
    try:
        actual_agent = _resolve_agent(request)
        if actual_agent is None:
            return

        brain = getattr(actual_agent, "brain", None)
        registry = getattr(actual_agent, "skill_registry", None)
        if not brain or not registry:
            return

        from openakita.skills.i18n import auto_translate_skill

        for skill in registry.list_all():
            if skill.name_i18n:
                continue
            if not skill.skill_path:
                continue
            skill_dir = Path(skill.skill_path).parent
            if not skill_dir.exists():
                continue
            await auto_translate_skill(
                skill_dir,
                skill.name,
                skill.description,
                brain,
            )
    except Exception as e:
        logger.warning(f"Auto-translate after install failed: {e}")


@router.get("/api/skills")
async def list_skills(request: Request):
    """List all available skills with their config schemas.

    Returns ALL discovered skills (including disabled ones) with correct
    ``enabled`` status derived from ``data/skills.json`` allowlist.

    Uses a module-level cache to avoid re-scanning disk on every request.
    The cache is invalidated by install/uninstall/reload/edit operations via
    the cross-layer on-change callback.
    """
    global _skills_cache
    if _skills_cache is not None:
        return _skills_cache

    from openakita.skills.allowlist_io import read_allowlist

    skills_json_path, external_allowlist = read_allowlist()
    # Base for generating relative_path still needs project root directory
    try:
        from openakita.config import settings

        base_path = Path(settings.project_root)
    except Exception:
        base_path = skills_json_path.parent.parent

    try:
        from openakita.skills.loader import SkillLoader

        loader = SkillLoader()
        await asyncio.to_thread(loader.load_all, base_path)
        all_skills = loader.registry.list_all()
        effective_allowlist = loader.compute_effective_allowlist(external_allowlist)
    except Exception:
        actual_agent = _resolve_agent(request)
        if actual_agent is None:
            return {"skills": []}
        registry = getattr(actual_agent, "skill_registry", None)
        if registry is None:
            return {"skills": []}
        all_skills = registry.list_all()
        effective_allowlist = external_allowlist

    skills = []
    for skill in all_skills:
        config = None
        parsed = getattr(skill, "_parsed_skill", None)
        if parsed and hasattr(parsed, "metadata"):
            config = getattr(parsed.metadata, "config", None) or None

        is_system = bool(skill.system)
        sid = getattr(skill, "skill_id", skill.name)
        is_enabled = is_system or effective_allowlist is None or sid in effective_allowlist

        relative_path = None
        if skill.skill_path:
            try:
                relative_path = str(Path(skill.skill_path).relative_to(base_path))
            except (ValueError, TypeError):
                relative_path = sid

        skills.append(
            {
                "skill_id": sid,
                "capability_id": getattr(skill, "capability_id", ""),
                "namespace": getattr(skill, "namespace", ""),
                "origin": getattr(skill, "origin", "project"),
                "visibility": getattr(skill, "visibility", "public"),
                "permission_profile": getattr(skill, "permission_profile", ""),
                "name": skill.name,
                "description": skill.description,
                "name_i18n": skill.name_i18n or None,
                "description_i18n": skill.description_i18n or None,
                "system": is_system,
                "enabled": is_enabled,
                "category": skill.category,
                "tool_name": skill.tool_name,
                "config": config,
                "path": relative_path,
                "source_url": getattr(skill, "source_url", None),
            }
        )

    def _sort_key(s: dict) -> tuple:
        enabled = s.get("enabled", False)
        system = s.get("system", False)
        if enabled and not system:
            tier = 0
        elif enabled and system:
            tier = 1
        else:
            tier = 2
        return (tier, s.get("name", ""))

    skills.sort(key=_sort_key)

    result = {"skills": skills}
    _skills_cache = result
    return result


@router.post("/api/skills/config")
async def update_skill_config(request: Request):
    """Persist skill configuration to data/skill_configs.json."""
    body = await request.json()
    skill_name = body.get("skill_name", "")
    config_values = body.get("config", {})

    if not skill_name:
        raise HTTPException(status_code=400, detail="skill_name is required")

    try:
        from openakita.config import settings

        config_file = settings.project_root / "data" / "skill_configs.json"
    except Exception:
        config_file = Path.cwd() / "data" / "skill_configs.json"

    existing: dict = {}
    if config_file.exists():
        try:
            raw = config_file.read_text(encoding="utf-8")
            existing = json.loads(raw) if raw.strip() else {}
        except Exception:
            pass

    existing[skill_name] = config_values
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return {"status": "ok", "skill": skill_name, "config": config_values}


@router.post("/api/skills/install")
async def install_skill(request: Request):
    """Install skill (remote mode replacement for Tauri openakita_install_skill command).

    POST body: { "url": "github:user/repo/skill" }

    Upon completion:
      1. Upsert newly installed skill_id into external_allowlist in data/skills.json
         (only when field exists; preserves "undeclared=all enabled" semantics when file absent)
      2. Fully refresh runtime cache and Agent Pool via ``propagate_skill_change``.
    """
    from openakita.skills.allowlist_io import upsert_skill_ids

    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return {"error": "url is required"}

    try:
        from openakita.config import settings

        workspace_dir = str(settings.project_root)
    except Exception:
        workspace_dir = str(Path.cwd())

    try:
        from openakita.setup_center.bridge import install_skill as _install_skill

        await asyncio.to_thread(_install_skill, workspace_dir, url)
    except FileNotFoundError as e:
        missing = getattr(e, "filename", None) or "external command"
        logger.error("Skill install missing dependency: %s", e, exc_info=True)
        return {
            "error": (
                f"Installation failed: executable command `{missing}` not found. "
                "Please install Git and ensure it's in PATH, or use GitHub shorthand/single SKILL.md link instead."
            )
        }
    except Exception as e:
        logger.error("Skill install failed: %s", e, exc_info=True)
        return {"error": str(e)}

    # Identify newly added skill directory (directory containing most recently modified SKILL.md)
    install_warning = None
    new_skill_id: str | None = None
    try:
        from openakita.setup_center.bridge import _resolve_skills_dir

        skills_dir = _resolve_skills_dir(workspace_dir)
        candidates = sorted(
            (d for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            from openakita.skills.parser import SkillParser

            parser = SkillParser()
            try:
                parser.parse_directory(candidates[0])
                new_skill_id = candidates[0].name
            except Exception as parse_err:
                import shutil

                skill_dir_name = candidates[0].name
                logger.error(
                    "Installed skill %s has invalid SKILL.md, removing: %s",
                    skill_dir_name,
                    parse_err,
                )
                shutil.rmtree(str(candidates[0]), ignore_errors=True)
                return {
                    "error": (
                        f"Skill files downloaded but SKILL.md format invalid, cannot load: {parse_err}. "
                        "Skill may be incompatible with OpenAkita format, automatically cleaned up."
                    )
                }
    except Exception as ve:
        install_warning = str(ve)
        logger.warning("Post-install validation skipped: %s", ve)

    # If skills.json already has external_allowlist, auto-upsert new skill to avoid immediate pruning.
    # Skip if allowlist field absent (all enabled semantics).
    if new_skill_id:
        try:
            upsert_skill_ids({new_skill_id})
        except Exception as e:
            logger.warning("Failed to upsert %s into skills.json: %s", new_skill_id, e)

    # Unified refresh entry -- re-scan disk + re-apply allowlist + rebuild catalog + notify Pool
    await _propagate(request, "install")

    # Auto-translate (optional, does not block successful return)
    try:
        await _auto_translate_new_skills(request, url)
    except Exception as e:
        logger.debug("Auto-translate skipped: %s", e)

    result: dict = {"status": "ok", "url": url}
    if install_warning:
        result["warning"] = install_warning
    if new_skill_id:
        result["skill_id"] = new_skill_id
    return result


@router.post("/api/skills/uninstall")
async def uninstall_skill(request: Request):
    """Uninstall skill.

    POST body: { "skill_id": "skill-directory-name" }
    """
    from openakita.skills.allowlist_io import remove_skill_ids

    body = await request.json()
    skill_id = (body.get("skill_id") or "").strip()
    if not skill_id:
        return {"error": "skill_id is required"}

    try:
        from openakita.config import settings

        workspace_dir = str(settings.project_root)
    except Exception:
        workspace_dir = str(Path.cwd())

    try:
        from openakita.setup_center.bridge import uninstall_skill as _uninstall_skill

        await asyncio.to_thread(_uninstall_skill, workspace_dir, skill_id)
    except Exception as e:
        logger.error("Skill uninstall failed: %s", e, exc_info=True)
        return {"error": str(e)}

    # Remove from allowlist (silently skip if file absent or field missing)
    try:
        remove_skill_ids({skill_id})
    except Exception as e:
        logger.warning("Failed to remove %s from skills.json: %s", skill_id, e)

    await _propagate(request, "uninstall")

    return {"status": "ok", "skill_id": skill_id}


@router.post("/api/skills/reload")
async def reload_skills(request: Request):
    """Hot reload skills (call after installing new skill, modifying SKILL.md, or toggling enable/disable).

    POST body: { "skill_name": "optional-name" }
    If skill_name is empty or not provided, re-scan and load all skills.
    """
    agent = _resolve_agent(request)
    if agent is None:
        return {"error": "Agent not initialized"}

    loader = getattr(agent, "skill_loader", None)
    registry = getattr(agent, "skill_registry", None)
    if not loader or not registry:
        return {"error": "Skill loader/registry not available"}

    body = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    skill_name = (body.get("skill_name") or "").strip()

    try:
        if skill_name:
            reloaded = await asyncio.to_thread(loader.reload_skill, skill_name)
            if not reloaded:
                return {"error": f"Skill '{skill_name}' not found or reload failed"}
            await _propagate(request, "reload", rescan=False)
            return {"status": "ok", "reloaded": [skill_name]}

        await _propagate(request, "reload", rescan=True)
        total = len(registry.list_all())
        return {
            "status": "ok",
            "reloaded": "all",
            "total": total,
        }
    except Exception as e:
        logger.error(f"Skill reload failed: {e}")
        return {"error": str(e)}


@router.get("/api/skills/content/{skill_name:path}")
async def get_skill_content(skill_name: str, request: Request):
    """Read raw SKILL.md content of a single skill.

    Returns { content, path, system } for frontend display and editing.
    System built-in skills marked system=true; frontend decides whether to allow editing based on this.
    """
    from openakita.skills.loader import SkillLoader

    try:
        from openakita.config import settings

        base_path = Path(settings.project_root)
    except Exception:
        base_path = Path.cwd()

    actual_agent = _resolve_agent(request)

    skill = None
    if actual_agent:
        loader = getattr(actual_agent, "skill_loader", None)
        if loader:
            skill = loader.get_skill(skill_name)

    if not skill:
        try:
            tmp_loader = SkillLoader()
            tmp_loader.load_all(base_path=base_path)
            skill = tmp_loader.get_skill(skill_name)
        except Exception:
            pass

    if not skill:
        return {"error": f"Skill '{skill_name}' not found"}

    try:
        content = skill.path.read_text(encoding="utf-8")
    except Exception as e:
        return {"error": f"Failed to read SKILL.md: {e}"}

    safe_path = skill_name
    try:
        safe_path = str(Path(skill.path).relative_to(base_path))
    except (ValueError, TypeError):
        pass

    return {
        "content": content,
        "path": safe_path,
        "system": skill.metadata.system,
    }


@router.put("/api/skills/content/{skill_name:path}")
async def update_skill_content(skill_name: str, request: Request):
    """Update skill's SKILL.md content and hot reload.

    PUT body: { "content": "full SKILL.md content" }
    """
    from openakita.skills.parser import skill_parser

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    new_content = body.get("content", "")
    if not new_content.strip():
        return {"error": "content is required"}

    actual_agent = _resolve_agent(request)

    skill = None
    loader = None
    if actual_agent:
        loader = getattr(actual_agent, "skill_loader", None)
        if loader:
            skill = loader.get_skill(skill_name)

    if not skill:
        return {"error": f"Skill '{skill_name}' not found"}

    if skill.metadata.system:
        return {"error": "Cannot edit system (built-in) skills"}

    try:
        parsed = skill_parser.parse_content(new_content, skill.path)
    except Exception as e:
        return {"error": f"Invalid SKILL.md format: {e}"}

    try:
        skill.path.write_text(new_content, encoding="utf-8")
    except Exception as e:
        return {"error": f"Failed to write SKILL.md: {e}"}

    reloaded = False
    if loader:
        try:
            result = await asyncio.to_thread(loader.reload_skill, skill_name)
            if result:
                await _propagate(request, "content_update", rescan=False)
                reloaded = True
        except Exception as e:
            logger.warning(f"Skill reload after edit failed: {e}")

    return {
        "status": "ok",
        "reloaded": reloaded,
        "name": parsed.metadata.name,
        "description": parsed.metadata.description,
    }


@router.get("/api/skills/marketplace")
async def search_marketplace(q: str = "agent"):
    """Proxy to skills.sh search API (bypasses CORS for desktop app)."""
    from openakita.llm.providers.proxy_utils import (
        get_httpx_transport,
        get_proxy_config,
    )

    try:
        client_kwargs: dict = {
            "timeout": 15,
            "follow_redirects": True,
            "trust_env": False,
        }

        proxy = get_proxy_config()
        if proxy:
            client_kwargs["proxy"] = proxy

        transport = get_httpx_transport()
        if transport:
            client_kwargs["transport"] = transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(SKILLS_SH_API, params={"q": q})
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("skills.sh API error: %s", e)
        return {"skills": [], "count": 0, "error": str(e)}


# ──────────────────────────────────────────────────────────────────────
# Cross-layer event subscribers
#
# ``Agent.propagate_skill_change`` is the entry point for all refresh paths.
# Its final step calls ``notify_skills_changed(action)``; here register two side effects:
#   1. Clear GET /api/skills module cache so frontend gets latest list on next GET
#   2. Broadcast ``skills:changed`` event via WebSocket for real-time UI refresh
#
# AgentInstancePool version bumping already completed inside ``propagate_skill_change``,
# do NOT repeat pool notification here, avoiding version increment twice per change.
# ──────────────────────────────────────────────────────────────────────


def _broadcast_ws_event(action: str) -> None:
    """WebSocket broadcast (fire-and-forget)."""
    try:
        from openakita.api.routes.websocket import broadcast_event

        asyncio.ensure_future(broadcast_event("skills:changed", {"action": action}))
    except Exception:
        pass


def _on_skills_changed_api(action: str) -> None:
    """API layer side effects triggered by ``skills.events.notify_skills_changed``."""
    _invalidate_skills_cache()
    _broadcast_ws_event(action)


try:
    from openakita.skills.events import register_on_change

    register_on_change(_on_skills_changed_api)
except Exception:
    pass
