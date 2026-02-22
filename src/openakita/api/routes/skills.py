"""
Skills route: GET /api/skills, POST /api/skills/config, GET /api/skills/marketplace

技能列表与配置管理。
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()

SKILLS_SH_API = "https://skills.sh/api/search"


@router.get("/api/skills")
async def list_skills(request: Request):
    """List all available skills with their config schemas.

    Returns ALL discovered skills (including disabled ones) with correct
    ``enabled`` status derived from ``data/skills.json`` allowlist.
    """
    import json
    from pathlib import Path

    try:
        from openakita.config import settings

        base_path = settings.project_root
    except Exception:
        base_path = Path.cwd()

    # Read external_allowlist from skills.json
    external_allowlist: set[str] | None = None
    try:
        cfg_path = base_path / "data" / "skills.json"
        if cfg_path.exists():
            raw = cfg_path.read_text(encoding="utf-8")
            cfg = json.loads(raw) if raw.strip() else {}
            al = cfg.get("external_allowlist", None)
            if isinstance(al, list):
                external_allowlist = {str(x).strip() for x in al if str(x).strip()}
    except Exception:
        pass

    # Load all skills via a fresh SkillLoader (not pruned by allowlist)
    try:
        from openakita.skills.loader import SkillLoader

        loader = SkillLoader()
        loader.load_all(base_path=base_path)
        all_skills = loader.registry.list_all()
    except Exception:
        # Fallback to agent's registry (only enabled skills)
        from openakita.core.agent import Agent

        agent = getattr(request.app.state, "agent", None)
        actual_agent = agent
        if not isinstance(agent, Agent):
            actual_agent = getattr(agent, "_local_agent", None)
        if actual_agent is None:
            return {"skills": []}
        registry = getattr(actual_agent, "skill_registry", None)
        if registry is None:
            return {"skills": []}
        all_skills = registry.list_all()

    skills = []
    for skill in all_skills:
        config = None
        parsed = getattr(skill, "_parsed_skill", None)
        if parsed and hasattr(parsed, "metadata"):
            config = getattr(parsed.metadata, "config", None) or None

        is_system = bool(skill.system)
        is_enabled = is_system or external_allowlist is None or skill.name in external_allowlist

        # Read install origin (.openakita-source) for marketplace matching
        source_url = None
        if skill.skill_path:
            try:
                origin_file = Path(skill.skill_path) / ".openakita-source"
                if origin_file.exists():
                    source_url = origin_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        skills.append({
            "name": skill.name,
            "description": skill.description,
            "system": is_system,
            "enabled": is_enabled,
            "category": skill.category,
            "tool_name": skill.tool_name,
            "config": config,
            "path": skill.skill_path,
            "source_url": source_url,
        })

    return {"skills": skills}


@router.post("/api/skills/config")
async def update_skill_config(request: Request):
    """Update skill configuration."""
    body = await request.json()
    skill_name = body.get("skill_name", "")
    config = body.get("config", {})

    # TODO: Apply config to the skill and persist to .env
    return {"status": "ok", "skill": skill_name, "config": config}


@router.post("/api/skills/install")
async def install_skill(request: Request):
    """安装技能（远程模式替代 Tauri openakita_install_skill 命令）。

    POST body: { "url": "github:user/repo/skill" }
    """
    import asyncio

    body = await request.json()
    url = body.get("url", "").strip()
    if not url:
        return {"error": "url is required"}

    try:
        from openakita.config import settings

        workspace_dir = str(settings.project_root)
    except Exception:
        workspace_dir = str(__import__("pathlib").Path.cwd())

    try:
        from openakita.setup_center.bridge import install_skill as _install_skill

        # install_skill 是同步函数（可能执行 git clone），放到线程中避免阻塞事件循环
        await asyncio.to_thread(_install_skill, workspace_dir, url)
        return {"status": "ok", "url": url}
    except Exception as e:
        logger.error(f"Skill install failed: {e}")
        return {"error": str(e)}


@router.post("/api/skills/reload")
async def reload_skills(request: Request):
    """热重载技能（安装新技能后、修改 SKILL.md 后调用）。

    POST body: { "skill_name": "optional-name" }
    如果 skill_name 为空或未提供，则重新扫描并加载所有技能。
    """
    from openakita.core.agent import Agent

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    skill_name = (body.get("skill_name") or "").strip()

    agent = getattr(request.app.state, "agent", None)
    actual_agent = agent
    if not isinstance(agent, Agent):
        actual_agent = getattr(agent, "_local_agent", None)

    if actual_agent is None:
        return {"error": "Agent not initialized"}

    loader = getattr(actual_agent, "skill_loader", None)
    registry = getattr(actual_agent, "skill_registry", None)
    if not loader or not registry:
        return {"error": "Skill loader/registry not available"}

    try:
        if skill_name:
            # 重载单个技能
            reloaded = loader.reload_skill(skill_name)
            if reloaded:
                return {"status": "ok", "reloaded": [skill_name]}
            else:
                return {"error": f"Skill '{skill_name}' not found or reload failed"}
        else:
            # 全量重新扫描：让 loader 发现新技能并重新加载
            from openakita.config import settings

            base_path = getattr(settings, "project_root", None)
            loaded_count = loader.load_all(base_path)
            total = len(registry.list_all())
            return {"status": "ok", "reloaded": "all", "loaded": loaded_count, "total": total}
    except Exception as e:
        logger.error(f"Skill reload failed: {e}")
        return {"error": str(e)}


@router.get("/api/skills/marketplace")
async def search_marketplace(q: str = "agent"):
    """Proxy to skills.sh search API (bypasses CORS for desktop app)."""
    from openakita.llm.providers.proxy_utils import (
        get_httpx_transport,
        get_proxy_config,
    )

    try:
        client_kwargs: dict = {"timeout": 15, "follow_redirects": True}

        # 复用项目的代理和 IPv4 设置
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
