"""Node lifecycle + schedules + identity + MCP endpoints (P-RC-9 P9.7beta-2).

Mints cluster 3.2 of ``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md``
-- 16 endpoints (B18-B33) covering node schedules CRUD, node identity
markdown files, node MCP config JSON, node status controllers
(freeze/unfreeze/offline/online), and node observability snapshots
(status / thinking / prompt-preview / dismiss).

Wiring matrix:

* schedules + identity + MCP -> :class:`OrgManager` (P9.5)
  via the ``_get_manager`` helper. Identity / MCP are file-IO
  endpoints; v2 manager exposes ``get_org_dir`` so the v2 route
  can read / write the ``identity/`` and ``mcp_config.json``
  artefacts without reaching v1 OrgManager.
* status controllers + observability snapshots ->
  :class:`OrgRuntime` (P9.6) via the ``_get_runtime`` helper.
  Route calls the duck-typed methods; integration with the
  P9.6 NodeStatusController sibling lands in P9.7gamma when
  the contract test suite pins the exact call shape.

ADR refs: ADR-0011 (D-3 layer separation; D-4 R4 granularity
ceiling preserved), ADR-0012 (no shim under v1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from openakita.api.schemas.orgs_v2 import NodeRegister

from .orgs_v2_runtime import _get_manager, _get_runtime, router

_IDENTITY_FILES: tuple[str, ...] = ("SOUL.md", "AGENT.md", "ROLE.md")


def _node_dir(mgr: Any, org_id: str, node_id: str) -> Path:
    """Compute ``<org_dir>/nodes/<node_id>/`` for file-IO endpoints."""
    return Path(mgr.get_org_dir(org_id)) / "nodes" / node_id


def _require_org_and_node(mgr: Any, org_id: str, node_id: str) -> None:
    """v1 oracle: 404 if the org OR the node is missing."""
    org = mgr.get(org_id)
    if org is None:
        raise HTTPException(404, f"Organization not found: {org_id}")
    if hasattr(org, "get_node") and org.get_node(node_id) is None:
        raise HTTPException(404, f"Node not found: {node_id}")


def _call_runtime_method(rt: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    """Duck-call a method on ``OrgRuntime`` with a structured 503 guard.

    Mirrors :func:`orgs_v2_runtime_dispatch._call_lifecycle`: when the
    runtime singleton predates a given subsystem method (e.g. P9.7gamma
    has not landed yet), surface a 503 with the missing-method name so
    the frontend can degrade the corresponding panel instead of seeing
    a 500 AttributeError.
    """
    method = getattr(rt, method_name, None)
    if method is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "subsystem_not_wired",
                "subsystem": f"runtime_method:{method_name}",
                "message": (
                    f"OrgRuntime.{method_name} is not yet connected. "
                    "See PR-9.7gamma wiring."
                ),
                "next_milestone": "P9.7gamma",
            },
        )
    return method(*args, **kwargs)


_TRIGGER_TYPE_TO_SCHEDULE_TYPE: dict[str, str] = {
    "cron": "cron",
    "interval": "interval",
    "once": "once",
    "schedule": "interval",
}


def _normalize_schedule_body(body: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested ``trigger: {type, expr|run_at|seconds}`` payloads.

    Older OrgEditor/test clients POST::

        {"trigger": {"type": "cron", "expr": "0 9 * * *"}, "prompt": "..."}

    while the persisted ``NodeSchedule`` dataclass expects flat
    ``schedule_type / cron / run_at / interval_s`` fields. The legacy
    behaviour silently dropped the nested object and left every trigger
    field unset, producing schedules that never fire. We translate the
    nested shape into the flat one when present; flat payloads pass
    through untouched for backward compatibility. Returns a *new* dict
    so the caller's input is not mutated.
    """
    payload = dict(body)
    trigger = payload.pop("trigger", None)
    if isinstance(trigger, dict):
        t_type = str(trigger.get("type") or "").lower()
        schedule_type = _TRIGGER_TYPE_TO_SCHEDULE_TYPE.get(t_type, t_type)
        if schedule_type and "schedule_type" not in payload:
            payload["schedule_type"] = schedule_type
        expr = trigger.get("expr") or trigger.get("cron")
        if expr is not None and "cron" not in payload:
            payload["cron"] = expr
        run_at = trigger.get("run_at") or trigger.get("when")
        if run_at is not None and "run_at" not in payload:
            payload["run_at"] = run_at
        seconds = trigger.get("seconds") or trigger.get("interval_s")
        if seconds is not None and "interval_s" not in payload:
            try:
                payload["interval_s"] = int(seconds)
            except (TypeError, ValueError):
                pass

    # Legacy flat-flat shape used by the OrgEditor v1 form and several
    # contract tests (e.g. test_b19_create_schedule_returns_201):
    #
    #     {"type": "cron", "expression": "0 0 * * *"}
    #
    # When the canonical ``schedule_type`` is absent we map the top-level
    # ``type`` / ``expression`` / ``seconds`` / ``run_at`` keys to the
    # canonical flat schema. The same normalisation rules as the nested
    # ``trigger`` branch apply.
    if "schedule_type" not in payload and "type" in payload:
        t_type = str(payload.get("type") or "").lower()
        schedule_type = _TRIGGER_TYPE_TO_SCHEDULE_TYPE.get(t_type, t_type)
        if schedule_type:
            payload["schedule_type"] = schedule_type
            payload.pop("type", None)
    if "cron" not in payload and "expression" in payload:
        payload["cron"] = payload.pop("expression")
    if "interval_s" not in payload and "seconds" in payload:
        try:
            payload["interval_s"] = int(payload.pop("seconds"))
        except (TypeError, ValueError):
            payload.pop("seconds", None)
    if "run_at" not in payload and "when" in payload:
        payload["run_at"] = payload.pop("when")
    return payload


def _require_schedule_trigger_field(body: dict[str, Any]) -> None:
    """Reject schedules whose trigger fields are all missing.

    Without this guard a request body with ``schedule_type=interval``
    but no ``cron / run_at / interval_s`` would silently produce a
    schedule that the scheduler never fires; the v10 exploratory pass
    captured this as ``E3.8`` "schedule_type 被改写为 interval, cron /
    interval_s / run_at 全为 null". We surface that here as 422.
    """
    schedule_type = str(body.get("schedule_type") or "").lower()
    if not schedule_type:
        # ``NodeSchedule.from_dict`` defaults to ``INTERVAL``; we still
        # want at least one trigger field so the schedule can fire.
        schedule_type = "interval"
    cron = body.get("cron")
    interval_s = body.get("interval_s")
    run_at = body.get("run_at")
    if schedule_type == "cron" and not cron:
        raise HTTPException(422, "cron schedule requires 'cron' expression")
    if schedule_type == "once" and not run_at:
        raise HTTPException(422, "once schedule requires 'run_at' timestamp")
    if schedule_type == "interval" and not interval_s and not run_at and not cron:
        raise HTTPException(
            422,
            "schedule requires one of 'cron', 'run_at', or 'interval_s'",
        )


# ---------------------------------------------------------------------------
# B18-B21: schedules CRUD (delegates to OrgManager v2)
# ---------------------------------------------------------------------------


@router.get("/{org_id}/nodes/{node_id}/schedules", summary="B18 list node schedules")
def list_node_schedules(request: Request, org_id: str, node_id: str) -> list[dict[str, Any]]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    return [
        s.to_dict() if hasattr(s, "to_dict") else s for s in mgr.get_node_schedules(org_id, node_id)
    ]


@router.post(
    "/{org_id}/nodes/{node_id}/schedules", status_code=201, summary="B19 create node schedule"
)
async def create_node_schedule(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    from openakita.orgs import NodeSchedule

    raw_body = await request.json()
    if not isinstance(raw_body, dict):
        raise HTTPException(422, "schedule body must be a JSON object")
    body = _normalize_schedule_body(raw_body)
    _require_schedule_trigger_field(body)
    schedule = NodeSchedule.from_dict(body) if hasattr(NodeSchedule, "from_dict") else body
    result = mgr.add_node_schedule(org_id, node_id, schedule)
    return result.to_dict() if hasattr(result, "to_dict") else result


@router.put("/{org_id}/nodes/{node_id}/schedules/{schedule_id}", summary="B20 update node schedule")
async def update_node_schedule(
    request: Request, org_id: str, node_id: str, schedule_id: str
) -> dict[str, Any]:
    mgr = _get_manager(request)
    body = await request.json()
    result = mgr.update_node_schedule(org_id, node_id, schedule_id, body)
    if result is None:
        raise HTTPException(404, f"Schedule not found: {schedule_id}")
    return result.to_dict() if hasattr(result, "to_dict") else result


@router.delete(
    "/{org_id}/nodes/{node_id}/schedules/{schedule_id}",
    summary="B21 delete node schedule",
)
def delete_node_schedule(
    request: Request, org_id: str, node_id: str, schedule_id: str
) -> dict[str, Any]:
    if not _get_manager(request).delete_node_schedule(org_id, node_id, schedule_id):
        raise HTTPException(404, f"Schedule not found: {schedule_id}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# B22-B23: node identity files
# ---------------------------------------------------------------------------


@router.get("/{org_id}/nodes/{node_id}/identity", summary="B22 get node identity")
def get_node_identity(request: Request, org_id: str, node_id: str) -> dict[str, str | None]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    base = _node_dir(mgr, org_id, node_id) / "identity"
    out: dict[str, str | None] = {}
    for name in _IDENTITY_FILES:
        p = base / name
        out[name] = p.read_text(encoding="utf-8") if p.is_file() else None
    return out


@router.put("/{org_id}/nodes/{node_id}/identity", summary="B23 update node identity")
async def update_node_identity(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    body = await request.json()
    base = _node_dir(mgr, org_id, node_id) / "identity"
    base.mkdir(parents=True, exist_ok=True)
    for name in _IDENTITY_FILES:
        if name in body:
            p = base / name
            content = body[name]
            if content is None or content == "":
                p.unlink(missing_ok=True)
            else:
                p.write_text(content, encoding="utf-8")
    return {"ok": True}


# ---------------------------------------------------------------------------
# B24-B25: node MCP config
# ---------------------------------------------------------------------------


@router.get("/{org_id}/nodes/{node_id}/mcp", summary="B24 get node MCP config")
def get_node_mcp(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    p = _node_dir(mgr, org_id, node_id) / "mcp_config.json"
    if not p.is_file():
        return {"mode": "inherit"}
    return json.loads(p.read_text(encoding="utf-8"))


@router.put("/{org_id}/nodes/{node_id}/mcp", summary="B25 update node MCP config")
async def update_node_mcp(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    body = await request.json()
    p = _node_dir(mgr, org_id, node_id) / "mcp_config.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ---------------------------------------------------------------------------
# B26-B29: node status controllers (freeze / unfreeze / offline / online)
# ---------------------------------------------------------------------------


@router.post("/{org_id}/nodes/{node_id}/freeze", summary="B26 freeze node")
async def freeze_node(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    rt = _get_runtime(request)
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    reason = body.get("reason", "user action")
    result = await _call_runtime_method(rt, "freeze_node", org_id, node_id, reason=reason)
    return {"ok": True, "result": result}


@router.post("/{org_id}/nodes/{node_id}/unfreeze", summary="B27 unfreeze node")
async def unfreeze_node(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    rt = _get_runtime(request)
    result = await _call_runtime_method(rt, "unfreeze_node", org_id, node_id)
    return {"ok": True, "result": result}


@router.post("/{org_id}/nodes/{node_id}/offline", summary="B28 set node offline")
async def set_node_offline(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    rt = _get_runtime(request)
    await _call_runtime_method(rt, "set_node_status", org_id, node_id, "offline")
    return {"ok": True, "status": "offline"}


@router.post("/{org_id}/nodes/{node_id}/online", summary="B29 set node online")
async def set_node_online(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    rt = _get_runtime(request)
    await _call_runtime_method(rt, "set_node_status", org_id, node_id, "idle")
    return {"ok": True, "status": "idle"}


# ---------------------------------------------------------------------------
# B30-B33: dismiss / thinking / prompt-preview / status
# ---------------------------------------------------------------------------


@router.delete("/{org_id}/nodes/{node_id}/dismiss", summary="B30 dismiss ephemeral node")
async def dismiss_node(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    rt = _get_runtime(request)
    ok = await _call_runtime_method(rt, "dismiss_node", org_id, node_id, by="user")
    if not ok:
        raise HTTPException(400, "Cannot dismiss this node (non-ephemeral or missing)")
    return {"ok": True}


@router.get("/{org_id}/nodes/{node_id}/thinking", summary="B31 get node thinking timeline")
def get_node_thinking(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    rt = _get_runtime(request)
    return _call_runtime_method(rt, "get_node_thinking", org_id, node_id)


@router.get("/{org_id}/nodes/{node_id}/prompt-preview", summary="B32 preview assembled node prompt")
def preview_node_prompt(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    rt = _get_runtime(request)
    return _call_runtime_method(rt, "preview_node_prompt", org_id, node_id)


@router.get("/{org_id}/nodes/{node_id}/status", summary="B33 get node status snapshot")
def get_node_status(request: Request, org_id: str, node_id: str) -> dict[str, Any]:
    mgr = _get_manager(request)
    _require_org_and_node(mgr, org_id, node_id)
    rt = _get_runtime(request)
    return _call_runtime_method(rt, "get_node_status_snapshot", org_id, node_id)


# NodeRegister is re-exported for future POST node-create endpoints that
# may land in beta-x or gamma; the import keeps the schemas/orgs_v2 layer
# discoverable from this sub-module without forcing an immediate consumer.
__all__ = ["NodeRegister"]
