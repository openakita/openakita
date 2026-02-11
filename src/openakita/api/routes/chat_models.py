"""
Models route: GET /api/models

返回当前可用的 LLM 端点列表及其状态。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ..schemas import ModelInfo

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_llm_client(agent: object):
    """Resolve LLMClient from Agent or MasterAgent."""
    from openakita.core.agent import Agent

    actual = agent
    if not isinstance(agent, Agent):
        actual = getattr(agent, "_local_agent", None)
    if actual is None:
        return None
    brain = getattr(actual, "brain", None)
    if brain is None:
        return None
    return getattr(brain, "_llm_client", None)


@router.get("/api/models")
async def list_models(request: Request):
    """List available LLM endpoints/models."""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return {"models": []}

    llm_client = _get_llm_client(agent)
    if llm_client is None:
        return {"models": []}

    models = []
    for name, provider in llm_client._providers.items():
        status = "healthy" if provider.is_healthy else "unhealthy"

        models.append(ModelInfo(
            name=name,
            provider=getattr(provider.config, "provider", "unknown"),
            model=provider.model,
            status=status,
        ).model_dump())

    return {"models": models}
