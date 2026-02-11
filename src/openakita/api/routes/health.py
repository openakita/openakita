"""
Health check routes: GET /api/health, POST /api/health/check

POST /api/health/check 使用 dry_run=True 模式执行只读检测，
不会修改 provider 的健康状态和冷静期计数，避免干扰正在运行的 Agent。
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


async def _check_endpoint_readonly(name: str, provider) -> HealthResult:
    """Check an endpoint in dry_run mode: test connectivity without modifying provider state."""
    t0 = time.time()
    try:
        await provider.health_check(dry_run=True)
        latency = round((time.time() - t0) * 1000)
        return HealthResult(
            name=name,
            status="healthy",
            latency_ms=latency,
            last_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as e:
        latency = round((time.time() - t0) * 1000)
        return HealthResult(
            name=name,
            status="unhealthy",
            latency_ms=latency,
            error=str(e)[:500],
            consecutive_failures=getattr(provider, "consecutive_cooldowns", 0),
            cooldown_remaining=getattr(provider, "cooldown_remaining", 0),
            is_extended_cooldown=getattr(provider, "is_extended_cooldown", False),
            last_checked_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )


@router.post("/api/health/check")
async def health_check(request: Request, body: HealthCheckRequest):
    """
    Check health of a specific LLM endpoint or all endpoints.

    Uses dry_run mode: sends a real test request but does NOT modify
    the provider's healthy/cooldown state, ensuring no interference
    with ongoing Agent LLM calls.
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
        result = await _check_endpoint_readonly(body.endpoint_name, provider)
        results.append(result)
    else:
        # Check all endpoints
        for name, provider in llm_client._providers.items():
            result = await _check_endpoint_readonly(name, provider)
            results.append(result)

    return {"results": [r.model_dump() for r in results]}
