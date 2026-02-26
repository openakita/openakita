"""
Skills route: GET /api/skills, POST /api/skills/config, GET /api/skills/marketplace

技能列表与配置管理。
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()

SKILLS_SH_API = "https://skills.sh/api/search"


def _read_external_allowlist() -> tuple[Path, set[str] | None]:
    """Read external_allowlist from data/skills.json.

    Returns (base_path, allowlist). allowlist is None when the file doesn't
    exist or has no external_allowlist key (meaning "all external skills enabled").
    """
    import json

    try:
        from openakita.config import settings
        base_path = settings.project_root
    except Exception:
        base_path = Path.cwd()

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
    return base_path, external_allowlist


def _apply_allowlist_and_rebuild_catalog(request: Request) -> int:
    """Re-read skills.json allowlist, prune agent's loader & registry, rebuild catalog.

    Call this after any operation that changes loaded skills or the allowlist.
    Returns the number of pruned external skills.
    """
    from openakita.core.agent import Agent

    agent = getattr(request.app.state, "agent", None)
    actual_agent = agent
    if not isinstance(agent, Agent):
        actual_agent = getattr(agent, "_local_agent", None)
    if actual_agent is None:
        return 0

    _, external_allowlist = _read_external_allowlist()

    loader = getattr(actual_agent, "skill_loader", None)
    removed = 0
    if loader:
        effective = loader.compute_effective_allowlist(external_allowlist)
        removed = loader.prune_external_by_allowlist(effective)

    catalog = getattr(actual_agent, "skill_catalog", None)
    if catalog:
        catalog.invalidate_cache()
        new_text = catalog.generate_catalog()
        if hasattr(actual_agent, "_skill_catalog_text"):
            actual_agent._skill_catalog_text = new_text

    return removed


@router.get("/api/skills")
async def list_skills(request: Request):
    """List all available skills with their config schemas.

    Returns ALL discovered skills (including disabled ones) with correct
    ``enabled`` status derived from ``data/skills.json`` allowlist.
    """
    from pathlib import Path

    base_path, external_allowlist = _read_external_allowlist()

    # Load all skills via a fresh SkillLoader (not pruned by allowlist)
    try:
        from openakita.skills.loader import SkillLoader

        loader = SkillLoader()
        loader.load_all(base_path=base_path)
        all_skills = loader.registry.list_all()
        effective_allowlist = loader.compute_effective_allowlist(external_allowlist)
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
        effective_allowlist = external_allowlist

    skills = []
    for skill in all_skills:
        config = None
        parsed = getattr(skill, "_parsed_skill", None)
        if parsed and hasattr(parsed, "metadata"):
            config = getattr(parsed.metadata, "config", None) or None

        is_system = bool(skill.system)
        is_enabled = is_system or effective_allowlist is None or skill.name in effective_allowlist

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
    安装完成后自动重新加载技能并应用 allowlist。
    """
    import asyncio

    from openakita.core.agent import Agent

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

        await asyncio.to_thread(_install_skill, workspace_dir, url)
    except FileNotFoundError as e:
        missing = getattr(e, "filename", None) or "外部命令"
        logger.error("Skill install missing dependency: %s", e, exc_info=True)
        return {
            "error": (
                f"安装失败：未找到可执行命令 `{missing}`。"
                "请先安装 Git 并确保在 PATH 中，或改用 GitHub 简写/单个 SKILL.md 链接。"
            )
        }
    except Exception as e:
        logger.error("Skill install failed: %s", e, exc_info=True)
        return {"error": str(e)}

    # 安装成功后：重新加载技能到 agent 运行时，并应用 allowlist
    try:
        agent = getattr(request.app.state, "agent", None)
        actual_agent = agent
        if not isinstance(agent, Agent):
            actual_agent = getattr(agent, "_local_agent", None)

        if actual_agent is not None:
            loader = getattr(actual_agent, "skill_loader", None)
            if loader:
                base_path, _ = _read_external_allowlist()
                loader.load_all(base_path)
            _apply_allowlist_and_rebuild_catalog(request)
    except Exception as e:
        logger.warning(f"Post-install reload failed (skill was installed): {e}")

    return {"status": "ok", "url": url}


@router.post("/api/skills/reload")
async def reload_skills(request: Request):
    """热重载技能（安装新技能后、修改 SKILL.md 后、切换启用/禁用后调用）。

    POST body: { "skill_name": "optional-name" }
    如果 skill_name 为空或未提供，则重新扫描并加载所有技能。
    全量重载后会重新读取 data/skills.json 的 allowlist 并裁剪禁用技能。
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
            reloaded = loader.reload_skill(skill_name)
            if reloaded:
                _apply_allowlist_and_rebuild_catalog(request)
                return {"status": "ok", "reloaded": [skill_name]}
            else:
                return {"error": f"Skill '{skill_name}' not found or reload failed"}
        else:
            base_path, _ = _read_external_allowlist()
            loaded_count = loader.load_all(base_path)

            pruned = _apply_allowlist_and_rebuild_catalog(request)
            total = len(registry.list_all())
            return {
                "status": "ok",
                "reloaded": "all",
                "loaded": loaded_count,
                "pruned": pruned,
                "total": total,
            }
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
