"""Shared query service — backs both the REST surface (``plugin.py``
routes) and the Agent tools (``_handle_tool``).

Design constraints inherited from §9 of the plan:

* Every numeric argument flows through :func:`_clamp`, so a misbehaving
  LLM that hands in ``limit=99999`` cannot turn the query into a full
  table scan.
* Functions are kept thin: they translate validated args into
  ``FinpulseTaskManager`` calls and shape the response. No business
  logic that belongs to the pipeline lives here — ``fin_pulse_create``
  delegates straight to the injected ``pipeline`` / ``dispatch``
  services.
* Every function returns plain JSON-serialisable dicts; the Agent tool
  handler wraps them in a string via ``json.dumps`` before returning
  to the host Brain.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

__all__ = [
    "_clamp",
    "_clamp_float",
    "create_task",
    "cancel_task",
    "get_status",
    "list_tasks",
    "get_settings",
    "set_settings",
    "search_news",
]


# ── Validation helpers ────────────────────────────────────────────────


def _clamp(v: Any, lo: int, hi: int, default: int) -> int:
    """Coerce ``v`` into an integer clamped to ``[lo, hi]``.

    Returns ``default`` when ``v`` is missing / unparseable so a stray
    ``None`` never propagates into the SQL ``LIMIT`` clause.
    """

    if v is None:
        return default
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, iv))


def _clamp_float(
    v: Any, lo: float, hi: float, default: float | None = None
) -> float | None:
    """Float counterpart of :func:`_clamp`. ``default=None`` lets the
    caller distinguish "not supplied" from a clamped value so the SQL
    layer can drop the predicate entirely.
    """

    if v is None:
        return default
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, fv))


# ── Secret redaction (mirror of plugin.py helper) ────────────────────


_SECRET_KEYS = ("api_key", "token", "webhook", "secret", "password")


def _redact(cfg: dict[str, str]) -> dict[str, str]:
    """Mask values whose key looks secretive — identical policy to the
    REST ``GET /config`` helper in ``plugin.py``.
    """

    redacted: dict[str, str] = {}
    for k, v in cfg.items():
        if any(s in k.lower() for s in _SECRET_KEYS) and v:
            redacted[k] = "***"
        else:
            redacted[k] = v
    return redacted


# ── Task creation / lifecycle ─────────────────────────────────────────


_CREATE_MODES = frozenset({"ingest", "daily_brief", "hot_radar"})


async def create_task(
    *,
    tm: Any,
    pipeline: Any | None,
    dispatch: Any | None,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Create and execute a fin-pulse task synchronously (ingest /
    daily_brief / hot_radar). Returns a structured envelope compatible
    with the REST surface so the Agent tool handler can simply
    ``json.dumps`` the result.
    """

    mode = str(args.get("mode") or "").strip()
    if mode not in _CREATE_MODES:
        return {
            "ok": False,
            "error": "invalid_mode",
            "hint": f"mode must be one of {sorted(_CREATE_MODES)}",
        }
    if tm is None:
        return {"ok": False, "error": "task_manager_unavailable"}
    if pipeline is None:
        return {"ok": False, "error": "pipeline_unavailable"}

    params = args.get("params") if isinstance(args.get("params"), dict) else {}
    # Allow top-level shortcuts so a LLM call site does not have to
    # nest every knob under "params".
    merged: dict[str, Any] = {**(params or {})}
    for shortcut in (
        "sources",
        "since_hours",
        "top_k",
        "lang",
        "session",
        "rules_text",
        "targets",
        "min_score",
        "limit",
        "cooldown_s",
        "title",
    ):
        if shortcut in args and shortcut not in merged:
            merged[shortcut] = args[shortcut]

    if mode == "ingest":
        since_hours = _clamp(merged.get("since_hours"), 1, 72, 24)
        sources = merged.get("sources")
        if sources is not None and not isinstance(sources, list):
            return {"ok": False, "error": "sources must be a list or omitted"}
        task = await tm.create_task(
            mode="ingest",
            params={"sources": sources, "since_hours": since_hours},
            status="running",
        )
        summary = await pipeline.ingest(
            sources=sources, since_hours=since_hours, task_id=task["id"]
        )
        return {"ok": True, "mode": mode, "task_id": task["id"], "summary": summary}

    if mode == "daily_brief":
        session = str(merged.get("session") or "")
        if session not in {"morning", "noon", "evening"}:
            return {
                "ok": False,
                "error": "session must be morning|noon|evening",
            }
        since_hours = _clamp(merged.get("since_hours"), 1, 72, 12)
        top_k = _clamp(merged.get("top_k"), 1, 60, 20)
        lang = str(merged.get("lang") or "zh") or "zh"
        task = await tm.create_task(
            mode="daily_brief",
            params={
                "session": session,
                "since_hours": since_hours,
                "top_k": top_k,
                "lang": lang,
            },
            status="running",
        )
        result = await pipeline.run_daily_brief(
            session=session,
            since_hours=since_hours,
            top_k=top_k,
            lang=lang,
            task_id=task["id"],
        )
        return {"ok": True, "mode": mode, "task_id": task["id"], "digest": result}

    # mode == "hot_radar"
    rules_text = merged.get("rules_text")
    if not isinstance(rules_text, str) or not rules_text.strip():
        return {"ok": False, "error": "rules_text required for hot_radar"}
    targets_raw = merged.get("targets") or []
    if not isinstance(targets_raw, list) or not targets_raw:
        return {"ok": False, "error": "targets must be a non-empty list"}
    clean_targets: list[dict[str, str]] = []
    for t in targets_raw:
        if not isinstance(t, dict):
            continue
        ch = str(t.get("channel") or "").strip()
        ci = str(t.get("chat_id") or "").strip()
        if ch and ci:
            clean_targets.append({"channel": ch, "chat_id": ci})
    if not clean_targets:
        return {"ok": False, "error": "no usable targets (need channel + chat_id)"}
    if dispatch is None:
        return {"ok": False, "error": "dispatch_unavailable"}
    since_hours = _clamp(merged.get("since_hours"), 1, 168, 24)
    limit = _clamp(merged.get("limit"), 1, 500, 100)
    min_score = _clamp_float(merged.get("min_score"), 0.0, 10.0, None)
    cooldown_s = _clamp_float(merged.get("cooldown_s"), 0.0, 86_400.0, 600.0) or 600.0
    title = merged.get("title")
    task = await tm.create_task(
        mode="hot_radar",
        params={
            "targets": clean_targets,
            "since_hours": since_hours,
            "limit": limit,
            "min_score": min_score,
            "cooldown_s": cooldown_s,
            "title": title,
        },
        status="running",
    )
    result = await pipeline.run_hot_radar(
        dispatch,
        rules_text=rules_text,
        targets=clean_targets,
        since_hours=since_hours,
        limit=limit,
        min_score=min_score,
        title=title if isinstance(title, str) else None,
        cooldown_s=cooldown_s,
        task_id=task["id"],
    )
    return {"ok": True, "mode": mode, "task_id": task["id"], "result": result}


async def cancel_task(*, tm: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Flip a task to ``canceled``. Idempotent — returns ``ok=True``
    even when the task is already in a terminal state so the Brain
    never retries forever.
    """

    if tm is None:
        return {"ok": False, "error": "task_manager_unavailable"}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "task_id required"}
    existing = await tm.get_task(task_id)
    if existing is None:
        return {"ok": False, "error": "not_found", "task_id": task_id}
    await tm.update_task_safe(task_id, status="canceled")
    return {"ok": True, "task_id": task_id, "status": "canceled"}


async def get_status(*, tm: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Return the full task row so the caller can inspect params,
    progress, and the error envelope without another round-trip.
    """

    if tm is None:
        return {"ok": False, "error": "task_manager_unavailable"}
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "task_id required"}
    row = await tm.get_task(task_id)
    if row is None:
        return {"ok": False, "error": "not_found", "task_id": task_id}
    return {"ok": True, "task": row}


async def list_tasks(*, tm: Any, args: dict[str, Any]) -> dict[str, Any]:
    """List recent tasks. ``limit`` is clamped to ``[1, 200]`` as per
    the plan's §9.2 contract; callers that ask for more silently get
    capped instead of an error.
    """

    if tm is None:
        return {"ok": False, "error": "task_manager_unavailable"}
    mode = args.get("mode")
    if mode is not None and not isinstance(mode, str):
        mode = str(mode)
    status = args.get("status")
    if status is not None and not isinstance(status, str):
        status = str(status)
    limit = _clamp(args.get("limit"), 1, 200, 50)
    offset = _clamp(args.get("offset"), 0, 10_000, 0)
    items, total = await tm.list_tasks(
        mode=mode or None,
        status=status or None,
        offset=offset,
        limit=limit,
    )
    return {"ok": True, "items": items, "total": total, "limit": limit, "offset": offset}


# ── Settings (config) ────────────────────────────────────────────────


async def get_settings(*, tm: Any, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the full config map with secret-looking keys redacted.

    ``args`` is accepted for signature symmetry with the other helpers
    and intentionally ignored — the Brain may pass ``{}`` or nothing.
    """

    if tm is None:
        return {"ok": False, "error": "task_manager_unavailable"}
    cfg = await tm.get_all_config()
    return {"ok": True, "config": _redact(cfg)}


async def set_settings(*, tm: Any, args: dict[str, Any]) -> dict[str, Any]:
    """Apply a flat key → string config patch. Keys outside the
    pre-seeded schema are allowed so user-managed webhooks can land
    without a code change; values are stringified defensively.
    """

    if tm is None:
        return {"ok": False, "error": "task_manager_unavailable"}
    updates = args.get("updates")
    if not isinstance(updates, dict) or not updates:
        return {"ok": False, "error": "updates must be a non-empty object"}
    flat: dict[str, str] = {}
    for k, v in updates.items():
        if not isinstance(k, str) or not k:
            continue
        flat[k] = v if isinstance(v, str) else str(v)
    if not flat:
        return {"ok": False, "error": "no usable updates"}
    await tm.set_configs(flat)
    return {"ok": True, "applied": sorted(flat.keys())}


# ── Search ────────────────────────────────────────────────────────────


def _since_from_days(days: int, *, now: datetime | None = None) -> str:
    """Translate ``days`` back-window to the ISO8601 string the
    articles table stores. Using UTC keeps the contract identical
    between the REST surface and the Agent tool.
    """

    base = now or datetime.now(timezone.utc)
    since = base - timedelta(days=days)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


async def search_news(
    *,
    tm: Any,
    args: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Search the articles index by keyword, source, and time window.

    The ``q`` parameter accepts ``+must`` / ``!exclude`` syntax, but V1.0
    treats it as a plain LIKE term — the compiler integration is already available via
    :mod:`finpulse_frequency`, but wiring it in would change the
    semantics of the REST ``GET /articles?q=`` endpoint. A future
    commit can flip a flag here without touching the handler.
    """

    if tm is None:
        return {"ok": False, "error": "task_manager_unavailable"}
    q = args.get("q")
    if q is not None and not isinstance(q, str):
        q = str(q)
    source_id = args.get("source_id")
    if source_id is not None and not isinstance(source_id, str):
        source_id = str(source_id)
    days = _clamp(args.get("days"), 1, 90, 1)
    limit = _clamp(args.get("limit"), 1, 200, 50)
    offset = _clamp(args.get("offset"), 0, 10_000, 0)
    min_score = _clamp_float(args.get("min_score"), 0.0, 10.0, None)
    since = _since_from_days(days, now=now)
    items, total = await tm.list_articles(
        source_id=source_id or None,
        since=since,
        q=(q or None),
        min_score=min_score,
        sort=str(args.get("sort") or "time"),
        offset=offset,
        limit=limit,
    )
    return {
        "ok": True,
        "items": items,
        "total": total,
        "window": {"days": days, "since": since},
        "limit": limit,
        "offset": offset,
    }


# ── Registry helper (used by plugin.py) ───────────────────────────────


def build_tool_dispatch(
    *, tm: Any, pipeline: Any | None, dispatch: Any | None
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Return a name → coroutine dispatch table wrapping the module
    functions with their dependencies pre-bound. ``plugin.py``'s
    ``_handle_tool`` just looks up the tool name and calls the
    coroutine — keeping the router shape trivially testable.
    """

    async def _create(a: dict[str, Any]) -> dict[str, Any]:
        return await create_task(tm=tm, pipeline=pipeline, dispatch=dispatch, args=a)

    async def _cancel(a: dict[str, Any]) -> dict[str, Any]:
        return await cancel_task(tm=tm, args=a)

    async def _status(a: dict[str, Any]) -> dict[str, Any]:
        return await get_status(tm=tm, args=a)

    async def _list(a: dict[str, Any]) -> dict[str, Any]:
        return await list_tasks(tm=tm, args=a)

    async def _get_settings(a: dict[str, Any]) -> dict[str, Any]:
        return await get_settings(tm=tm, args=a)

    async def _set_settings(a: dict[str, Any]) -> dict[str, Any]:
        return await set_settings(tm=tm, args=a)

    async def _search(a: dict[str, Any]) -> dict[str, Any]:
        return await search_news(tm=tm, args=a)

    return {
        "fin_pulse_create": _create,
        "fin_pulse_cancel": _cancel,
        "fin_pulse_status": _status,
        "fin_pulse_list": _list,
        "fin_pulse_settings_get": _get_settings,
        "fin_pulse_settings_set": _set_settings,
        "fin_pulse_search_news": _search,
    }


def serialize_tool_result(payload: Any) -> str:
    """Best-effort JSON encoder for tool results. The host Brain
    accepts either a dict or a string; returning a *string* keeps the
    encoded form stable across adapters (Claude, GPT, Qwen) that parse
    structured tool results differently.
    """

    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"ok": False, "error": "unserialisable_result"})
