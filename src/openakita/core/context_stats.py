"""Runtime context usage snapshots.

This module centralizes the "how full is the current context window?" calculation
so API routes and streaming events do not each re-estimate it slightly differently.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ContextUsageSnapshot:
    """A lightweight runtime snapshot of current context usage."""

    context_tokens: int
    context_limit: int
    remaining_tokens: int
    percent: float
    updated_at: float
    source: str = "estimated"
    conversation_id: str | None = None
    endpoint_name: str | None = None
    provider: str | None = None
    model: str | None = None
    raw_context_window: int | None = None
    effective_context_window: int | None = None
    output_reserve: int | None = None
    context_pressure: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Backward-compatible aliases consumed by older frontend code.
        data["history_context_tokens"] = self.context_tokens
        data["history_context_limit"] = self.context_limit
        return data


def _actual_agent(agent: Any) -> Any:
    return getattr(agent, "_local_agent", agent) if agent is not None else None


def _reasoning_engine(agent: Any) -> Any:
    return getattr(agent, "reasoning_engine", None) or (
        agent if hasattr(agent, "_context_manager") else None
    )


def _context_manager(agent: Any) -> Any:
    re = _reasoning_engine(agent)
    return (
        getattr(agent, "context_manager", None)
        or getattr(agent, "_context_manager", None)
        or getattr(re, "_context_manager", None)
    )


def _brain_from(agent: Any, ctx_mgr: Any = None) -> Any:
    return (
        getattr(agent, "_brain", None)
        or getattr(agent, "brain", None)
        or getattr(ctx_mgr, "_brain", None)
    )


def _call_with_optional_conversation(fn: Any, conversation_id: str | None) -> Any:
    try:
        return fn(conversation_id=conversation_id)
    except TypeError:
        return fn()


def _current_model_info(agent: Any, conversation_id: str | None = None) -> dict[str, Any]:
    ctx_mgr = _context_manager(agent)
    brain = _brain_from(agent, ctx_mgr)
    if brain is None or not hasattr(brain, "get_current_model_info"):
        return {}
    try:
        info = brain.get_current_model_info(conversation_id=conversation_id)
        return dict(info or {}) if isinstance(info, dict) else {}
    except TypeError:
        try:
            info = brain.get_current_model_info()
            return dict(info or {}) if isinstance(info, dict) else {}
        except Exception:
            return {}
    except Exception:
        return {}


def _last_messages(agent: Any) -> list[dict]:
    re = _reasoning_engine(agent)
    messages = getattr(re, "_last_working_messages", None)
    if messages:
        return list(messages)
    ctx = getattr(agent, "_context", None)
    messages = getattr(ctx, "messages", None)
    return list(messages or [])


def _estimate_tokens(ctx_mgr: Any, messages: list[dict]) -> int:
    if not messages or ctx_mgr is None or not hasattr(ctx_mgr, "estimate_messages_tokens"):
        return 0
    try:
        value = ctx_mgr.estimate_messages_tokens(messages)
        return max(int(value or 0), 0)
    except Exception:
        return 0


def _max_context_tokens(ctx_mgr: Any, conversation_id: str | None) -> int:
    if ctx_mgr is None or not hasattr(ctx_mgr, "get_max_context_tokens"):
        return 0
    try:
        value = _call_with_optional_conversation(ctx_mgr.get_max_context_tokens, conversation_id)
        return max(int(value or 0), 0)
    except Exception:
        return 0


def resolve_context_limit(agent: Any, conversation_id: str | None = None) -> dict[str, Any]:
    """Resolve the effective context limit and related model metadata."""

    actual = _actual_agent(agent)
    if actual is None:
        return {"context_limit": 0}

    ctx_mgr = _context_manager(actual)
    model_info = _current_model_info(actual, conversation_id)
    context_limit = _max_context_tokens(ctx_mgr, conversation_id)

    raw_context_window: int | None = None
    effective_context_window: int | None = None
    output_reserve: int | None = None
    brain = _brain_from(actual, ctx_mgr)
    endpoint_name = str(model_info.get("name") or model_info.get("endpoint_name") or "").strip()
    if brain is not None and endpoint_name:
        try:
            from ..config import settings
            from ..llm.types import DEFAULT_CONTEXT_WINDOW

            for ep in getattr(getattr(brain, "_llm_client", None), "endpoints", []) or []:
                if getattr(ep, "name", "") != endpoint_name:
                    continue
                raw = int(getattr(ep, "context_window", 0) or 0)
                if raw <= 0:
                    raw = int(DEFAULT_CONTEXT_WINDOW)
                raw_context_window = raw
                effective = min(raw, settings.context_max_window) if settings.context_max_window > 0 else raw
                effective_context_window = int(effective)
                reserve = int(getattr(ep, "max_tokens", 0) or 4096)
                output_reserve = min(reserve, max(effective_context_window // 3, 0))
                break
        except Exception:
            pass

    return {
        "context_limit": context_limit,
        "endpoint_name": endpoint_name or None,
        "provider": model_info.get("provider"),
        "model": model_info.get("model"),
        "raw_context_window": raw_context_window,
        "effective_context_window": effective_context_window,
        "output_reserve": output_reserve,
    }


def _snapshot_from_dict(data: dict[str, Any]) -> ContextUsageSnapshot | None:
    try:
        ctx = int(data.get("context_tokens") or data.get("history_context_tokens") or 0)
        limit = int(data.get("context_limit") or data.get("history_context_limit") or 0)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    remaining = max(limit - ctx, 0)
    return ContextUsageSnapshot(
        context_tokens=max(ctx, 0),
        context_limit=limit,
        remaining_tokens=remaining,
        percent=round((max(ctx, 0) / limit) * 100, 1) if limit else 0,
        updated_at=float(data.get("updated_at") or time.time()),
        source=str(data.get("source") or "cached"),
        conversation_id=data.get("conversation_id"),
        endpoint_name=data.get("endpoint_name") or data.get("name"),
        provider=data.get("provider"),
        model=data.get("model"),
        raw_context_window=data.get("raw_context_window"),
        effective_context_window=data.get("effective_context_window"),
        output_reserve=data.get("output_reserve"),
        context_pressure=data.get("context_pressure"),
        usage=dict(data.get("usage") or {}),
    )


def update_context_snapshot(
    agent: Any,
    conversation_id: str | None = None,
    messages: list[dict] | None = None,
    *,
    usage: dict[str, Any] | None = None,
    pressure: dict[str, Any] | None = None,
    source: str = "estimated",
) -> ContextUsageSnapshot | None:
    """Estimate and store the latest context usage snapshot on runtime objects."""

    actual = _actual_agent(agent)
    if actual is None:
        return None

    ctx_mgr = _context_manager(actual)
    if messages is None:
        messages = _last_messages(actual)
    context_tokens = _estimate_tokens(ctx_mgr, messages)
    limit_info = resolve_context_limit(actual, conversation_id)
    context_limit = int(limit_info.get("context_limit") or 0)
    if context_limit <= 0:
        return None

    re = _reasoning_engine(actual)
    if pressure is None:
        pressure = getattr(re, "_last_context_pressure", None)
    remaining = max(context_limit - context_tokens, 0)
    snapshot = ContextUsageSnapshot(
        context_tokens=context_tokens,
        context_limit=context_limit,
        remaining_tokens=remaining,
        percent=round((context_tokens / context_limit) * 100, 1) if context_limit else 0,
        updated_at=time.time(),
        source=source,
        conversation_id=conversation_id,
        endpoint_name=limit_info.get("endpoint_name"),
        provider=limit_info.get("provider"),
        model=limit_info.get("model"),
        raw_context_window=limit_info.get("raw_context_window"),
        effective_context_window=limit_info.get("effective_context_window"),
        output_reserve=limit_info.get("output_reserve"),
        context_pressure=dict(pressure) if isinstance(pressure, dict) else None,
        usage=dict(usage or {}),
    )

    actual._last_context_usage_snapshot = snapshot
    if re is not None:
        re._last_context_usage_snapshot = snapshot
    return snapshot


def get_context_snapshot(
    agent: Any,
    conversation_id: str | None = None,
    *,
    allow_estimate: bool = True,
) -> ContextUsageSnapshot | None:
    """Return the latest context snapshot, estimating one if needed."""

    actual = _actual_agent(agent)
    if actual is None:
        return None
    re = _reasoning_engine(actual)

    for owner in (actual, re):
        snapshot = getattr(owner, "_last_context_usage_snapshot", None) if owner is not None else None
        if isinstance(snapshot, ContextUsageSnapshot):
            if conversation_id is None or snapshot.conversation_id in (None, conversation_id):
                return snapshot
        if isinstance(snapshot, dict):
            parsed = _snapshot_from_dict(snapshot)
            if parsed and (conversation_id is None or parsed.conversation_id in (None, conversation_id)):
                return parsed

    cached = getattr(actual, "_last_usage_summary", None)
    if isinstance(cached, dict):
        parsed = _snapshot_from_dict(cached)
        if parsed:
            pressure = getattr(re, "_last_context_pressure", None)
            if isinstance(pressure, dict) and parsed.context_pressure is None:
                parsed.context_pressure = dict(pressure)
            return parsed

    if not allow_estimate:
        return None
    return update_context_snapshot(actual, conversation_id, source="fallback_estimate")


def merge_context_snapshot_into_usage(
    usage_data: dict[str, Any] | None,
    snapshot: ContextUsageSnapshot | None,
) -> dict[str, Any] | None:
    """Merge context fields into an existing usage payload."""

    if snapshot is None:
        return usage_data
    data = usage_data if usage_data is not None else {}
    snap = snapshot.to_dict()
    for key in (
        "context_tokens",
        "context_limit",
        "history_context_tokens",
        "history_context_limit",
        "remaining_tokens",
        "percent",
        "updated_at",
        "source",
        "endpoint_name",
        "provider",
        "model",
        "raw_context_window",
        "effective_context_window",
        "output_reserve",
        "context_pressure",
    ):
        value = snap.get(key)
        if value is not None:
            data[key] = value
    return data
