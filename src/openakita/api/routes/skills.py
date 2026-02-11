"""
Skills route: GET /api/skills, POST /api/skills/config

技能列表与配置管理。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/skills")
async def list_skills(request: Request):
    """List all available skills with their config schemas."""
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

    skills = []
    for skill in registry.list_all():
        # config 存储在 ParsedSkill.metadata 中
        config = None
        parsed = getattr(skill, "_parsed_skill", None)
        if parsed and hasattr(parsed, "metadata"):
            config = getattr(parsed.metadata, "config", None) or None

        skills.append({
            "name": skill.name,
            "description": skill.description,
            "system": skill.system,
            "enabled": True,  # 在 registry 中的技能都是已启用的
            "category": skill.category,
            "tool_name": skill.tool_name,
            "config": config,
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
