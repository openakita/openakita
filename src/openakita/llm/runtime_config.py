"""Runtime application of LLM endpoint configuration changes.

This module is the single place that turns an already-persisted endpoint
configuration change into live runtime state.  UI routes, chat tools, and other
writers should call this after they update ``llm_endpoints.json``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_brain(agent: Any) -> Any:
    brain = getattr(agent, "brain", None) or getattr(agent, "_local_agent", None)
    if brain and hasattr(brain, "brain"):
        brain = brain.brain
    return brain


def _resolve_main_client(agent: Any, brain: Any) -> Any:
    llm_client = getattr(brain, "_llm_client", None) if brain else None
    if llm_client is None:
        llm_client = getattr(agent, "_llm_client", None)
    return llm_client


def _fallback_runtime_ref(name: str) -> Any:
    """Best-effort lookup for non-API callers such as system_config tools."""
    try:
        from openakita import main
    except Exception:
        return None
    return getattr(main, name, None)


def apply_llm_runtime_config(
    *,
    agent: Any = None,
    gateway: Any = None,
    pool: Any = None,
    config_path: Path | None = None,
    reason: str = "llm_config",
) -> dict[str, Any]:
    """Apply persisted LLM endpoint config to live runtime components.

    The function is intentionally permissive: missing runtime objects are not a
    configuration failure.  If a component is absent, the next service startup
    or pooled Agent rebuild will pick up the persisted config.
    """
    if gateway is None:
        gateway = _fallback_runtime_ref("_message_gateway")
    if pool is None:
        pool = _fallback_runtime_ref("_desktop_pool")

    result: dict[str, Any] = {
        "status": "ok",
        "reloaded": False,
        "main_reloaded": False,
        "compiler_reloaded": False,
        "stt_reloaded": False,
        "pool_invalidated": False,
        "warnings": [],
    }

    def add_warning(message: str) -> None:
        result.setdefault("warnings", []).append(message)

    if pool is not None:
        try:
            if hasattr(pool, "notify_runtime_config_changed"):
                pool.notify_runtime_config_changed(reason)
                result["pool_invalidated"] = True
            elif hasattr(pool, "notify_skills_changed"):
                pool.notify_skills_changed()
                result["pool_invalidated"] = True
        except Exception as exc:
            add_warning(f"pool_invalidation_failed: {exc}")
            logger.warning("[LLM Runtime] pool invalidation failed: %s", exc)

    if agent is None:
        agent = _fallback_runtime_ref("_agent")
    if agent is None:
        result["reason"] = "agent_not_initialized"
        return result

    brain = _resolve_brain(agent)
    llm_client = _resolve_main_client(agent, brain)
    if llm_client is None:
        result["reason"] = "llm_client_not_found"
    else:
        try:
            if config_path is not None and getattr(llm_client, "_config_path", None) != config_path:
                llm_client._config_path = config_path
            main_reloaded = bool(llm_client.reload())
            result["reloaded"] = main_reloaded
            result["main_reloaded"] = main_reloaded
            result["endpoints"] = len(getattr(llm_client, "endpoints", []) or [])
            if not main_reloaded:
                result["status"] = "failed"
                result["reason"] = "main_reload_returned_false"
        except Exception as exc:
            result["status"] = "failed"
            result["reason"] = str(exc)
            logger.error("[LLM Runtime] main reload failed: %s", exc, exc_info=True)

    if brain and hasattr(brain, "reload_compiler_client"):
        try:
            result["compiler_reloaded"] = bool(brain.reload_compiler_client())
        except Exception as exc:
            add_warning(f"compiler_reload_failed: {exc}")
            logger.warning("[LLM Runtime] compiler reload failed: %s", exc)

    if gateway is not None and getattr(gateway, "stt_client", None):
        try:
            from openakita.llm.config import load_endpoints_config

            _, _, stt_eps, _ = load_endpoints_config(config_path)
            gateway.stt_client.reload(stt_eps)
            result["stt_reloaded"] = True
        except Exception as exc:
            add_warning(f"stt_reload_failed: {exc}")
            logger.warning("[LLM Runtime] STT reload failed: %s", exc)

    if result["status"] == "ok" and result.get("warnings"):
        result["status"] = "partial"

    logger.info("[LLM Runtime] config applied: %s", result)
    return result
