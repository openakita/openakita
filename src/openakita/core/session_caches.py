"""Centralised session cache reset hooks.

The lifecycle of a chat session may need to clear several module-level
caches to avoid bleeding state across conversations (e.g. WebFetch URL
cache, ReasoningEngine read-only tool cache, browser navigation memory).

Mirrors the consolidation pattern from the Claude Code reference under
``commands/clear/caches.ts``: one call point, documented effects.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def clear_session_caches(
    agent: Any | None = None, *, conversation_id: str | None = None
) -> dict[str, bool]:
    """Clear caches that are scoped to the active task / session.

    Returns a dict describing which subsystems were cleared so the API can
    surface this to the user (e.g. "已清理：WebFetch、工具缓存、浏览器导航记录").
    """
    cleared: dict[str, bool] = {}

    try:
        from ..tools.handlers.web_fetch import clear_web_fetch_cache

        clear_web_fetch_cache()
        cleared["web_fetch"] = True
    except Exception as exc:
        logger.debug("clear_web_fetch_cache failed: %s", exc)
        cleared["web_fetch"] = False

    try:
        from .domain_allowlist import get_domain_allowlist

        cid = conversation_id or (
            getattr(agent, "_current_conversation_id", "")
            or getattr(agent, "_current_session_id", "")
            or ""
        )
        if cid:
            get_domain_allowlist().clear(cid)
            cleared["domain_rules"] = True
    except Exception as exc:
        logger.debug("domain allowlist clear failed: %s", exc)

    if agent is not None:
        try:
            engine = getattr(agent, "reasoning_engine", None)
            cache = getattr(engine, "_readonly_tool_cache", None) if engine else None
            if isinstance(cache, dict):
                cache.clear()
                cleared["readonly_tool_cache"] = True
        except Exception as exc:
            logger.debug("readonly tool cache clear failed: %s", exc)
        try:
            if hasattr(agent, "_last_browser_navigate_url"):
                setattr(agent, "_last_browser_navigate_url", "")
                cleared["browser_navigation_memory"] = True
        except Exception:
            pass
        try:
            if hasattr(agent, "_last_link_diagnostic"):
                setattr(agent, "_last_link_diagnostic", None)
                cleared["last_link_diagnostic"] = True
        except Exception:
            pass
        try:
            ctx = getattr(agent, "context_manager", None)
            summaries = getattr(ctx, "_previous_summaries", None) if ctx else None
            if isinstance(summaries, dict):
                summaries.clear()
                cleared["context_summaries"] = True
        except Exception:
            pass

    return cleared
