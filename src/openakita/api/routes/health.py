"""
Health check routes: GET /api/health, POST /api/health/check
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request

from ..schemas import HealthCheckRequest, HealthResult

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/health")
async def health(request: Request):
    """Basic health check - returns 200 if server is running."""
    return {
        "status": "ok",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "agent_initialized": hasattr(request.app.state, "agent") and request.app.state.agent is not None,
    }


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


@router.post("/api/health/check")
async def health_check(request: Request, body: HealthCheckRequest):
    """
    Check health of a specific LLM endpoint or IM channel.
    Returns detailed health status.
    """
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return {"error": "Agent not initialized"}

    llm_client = _get_llm_client(agent)
    if llm_client is None:
        return {"error": "LLM client not available"}

    results = []

    if body.endpoint_name:
        # Check specific endpoint
        provider = llm_client._providers.get(body.endpoint_name)
        if not provider:
            return {"error": f"Endpoint not found: {body.endpoint_name}"}

        t0 = time.time()
        try:
            await provider.health_check()
            latency = round((time.time() - t0) * 1000)
            results.append(HealthResult(
                name=body.endpoint_name,
                status="healthy",
                latency_ms=latency,
                last_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
        except Exception as e:
            latency = round((time.time() - t0) * 1000)
            results.append(HealthResult(
                name=body.endpoint_name,
                status="unhealthy",
                latency_ms=latency,
                error=str(e)[:500],
                consecutive_failures=getattr(provider, "consecutive_cooldowns", 0),
                cooldown_remaining=getattr(provider, "cooldown_remaining", 0),
                is_extended_cooldown=getattr(provider, "is_extended_cooldown", False),
                last_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
    else:
        # Check all endpoints
        for name, provider in llm_client._providers.items():
            t0 = time.time()
            try:
                await provider.health_check()
                latency = round((time.time() - t0) * 1000)
                results.append(HealthResult(
                    name=name,
                    status="healthy",
                    latency_ms=latency,
                    last_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                ))
            except Exception as e:
                latency = round((time.time() - t0) * 1000)
                results.append(HealthResult(
                    name=name,
                    status="unhealthy",
                    latency_ms=latency,
                    error=str(e)[:500],
                    consecutive_failures=getattr(provider, "consecutive_cooldowns", 0),
                    cooldown_remaining=getattr(provider, "cooldown_remaining", 0),
                    is_extended_cooldown=getattr(provider, "is_extended_cooldown", False),
                    last_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                ))

    return {"results": [r.model_dump() for r in results]}
