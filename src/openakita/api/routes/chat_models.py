"""
Models route: GET /api/models

Returns the list of currently available LLM endpoints and their status.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request

from ..schemas import ModelInfo

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_llm_client(agent: object):
    """Resolve LLMClient from Agent."""
    from openakita.core.agent import Agent

    actual = agent if isinstance(agent, Agent) else None
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

        # Check if the API key env var actually has a value
        key_env = getattr(provider.config, "api_key_env", "") or ""
        has_key = bool(key_env and os.environ.get(key_env, "").strip())

        models.append(
            ModelInfo(
                name=name,
                provider=getattr(provider.config, "provider", "unknown"),
                model=provider.model,
                status=status,
                has_api_key=has_key,
            ).model_dump()
        )

    return {"models": models}
