"""
Context window utility functions

Shared logic extracted from agent.py / context_manager.py:
- estimate_tokens: Chinese/English-aware token estimation
- get_max_context_tokens: Calculate available context tokens from endpoint config
- get_raw_context_window: Retrieve the raw context_window value for the endpoint
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONTEXT_TOKENS = 160000


def estimate_tokens(text: str) -> int:
    """Estimate the token count of text (Chinese/English-aware).

    Chinese: roughly 1.5 characters per token; English: roughly 4 characters per token.
    """
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    total_chars = len(text)
    english_chars = total_chars - chinese_chars
    chinese_tokens = chinese_chars / 1.5
    english_tokens = english_chars / 4
    return max(int(chinese_tokens + english_tokens), 1)


def get_raw_context_window(brain: Any) -> int:
    """Retrieve the raw context_window value from the current endpoint config.

    Args:
        brain: Brain instance

    Returns:
        context_window value; returns 0 on failure
    """
    try:
        info = brain.get_current_model_info()
        ep_name = info.get("name", "")
        for ep in brain._llm_client.endpoints:
            if ep.name == ep_name:
                return getattr(ep, "context_window", 0) or 0
    except Exception:
        pass
    return 0


def get_max_context_tokens(
    brain: Any,
    conversation_id: str | None = None,
) -> int:
    """Calculate available context tokens based on endpoint configuration.

    Priority:
    1. Endpoint's context_window (falls back to 200000 if missing or 0)
    2. Subtract max_tokens output reserve and a 5% buffer
    3. If retrieval fails entirely, fall back to DEFAULT_MAX_CONTEXT_TOKENS (160K)
    """
    FALLBACK_CONTEXT_WINDOW = 200000

    from ..config import settings

    try:
        info = brain.get_current_model_info(conversation_id=conversation_id)
        ep_name = info.get("name", "")
        for ep in brain._llm_client.endpoints:
            if ep.name == ep_name:
                ctx = getattr(ep, "context_window", 0) or 0
                if ctx <= 0:
                    ctx = FALLBACK_CONTEXT_WINDOW
                if settings.context_max_window > 0:
                    ctx = min(ctx, settings.context_max_window)
                output_reserve = ep.max_tokens or 4096
                output_reserve = min(output_reserve, ctx // 3)
                result = int((ctx - output_reserve) * 0.95)
                if result < 1024:
                    return max(int(ctx * 0.5), 1024)
                return result
        return DEFAULT_MAX_CONTEXT_TOKENS
    except Exception:
        return DEFAULT_MAX_CONTEXT_TOKENS
