"""Runtime control + Commands + Broadcast endpoints (P-RC-9 P9.7beta-3).

Mints cluster 3.3 of ``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md``
-- 8 endpoints (B34-B41) covering the org lifecycle verbs
(start / stop / pause / resume), user-command submit / poll /
cancel, and the org-level broadcast tool.

Wiring matrix:

* lifecycle (start/stop/pause/resume) -> :class:`OrgRuntime`
  (P9.6) via the ``_get_runtime`` helper. Methods are duck-typed
  on the runtime singleton; integration with the existing
  ``OrgLifecycleManager`` sibling lands in P9.7gamma.
* command submit -> :class:`OrgCommandService` (P9.4) via the
  ``_get_command_service`` helper. ``OrgCommandRequest`` is
  constructed from the request body using the Pydantic
  ``CommandSubmit`` shape (D-3 LOCKED).
* command poll / cancel -> ``OrgCommandService.get_status`` /
  ``OrgCommandService.cancel``.
* broadcast -> :class:`OrgRuntime`'s broadcast adapter.

ADR refs: ADR-0011 (D-3 layer separation; D-4 R4 granularity
ceiling preserved), ADR-0012 (no shim under v1).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

from openakita.api.schemas.orgs_v2 import CancelRequest, CommandSubmit

from .orgs_v2_runtime import (
    _get_command_service,
    _get_manager,
    _get_runtime,
    _runtime_method_not_wired,
    router,
)

logger = logging.getLogger(__name__)


def _to_dict(obj: Any) -> Any:
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


# ---------------------------------------------------------------------------
# B34-B37: lifecycle verbs (start / stop / pause / resume)
# ---------------------------------------------------------------------------


async def _call_lifecycle(rt: Any, verb: str, org_id: str) -> Any:
    method = getattr(rt, f"{verb}_org", None)
    if method is None:
        raise _runtime_method_not_wired(f"{verb}_org")
    try:
        result = await method(org_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _to_dict(result)


# v11 #2: ``OrgLifecycleManager`` mutates only an in-memory state map,
# whereas ``OrgManager.get(org_id)`` (and ``CommandService._refuse_unless_active``)
# read the persisted ``Organization.status`` field. Without a write-back
# the spec keeps reading "dormant" forever after a successful start, so
# the editor shows "active" in the toast while command submit returns
# 409 ``conversation_busy`` -- the exact regression v11 §10-#2 flagged
# as the blocker for the from-template -> start -> command happy path.
#
# The mapping below is the v1 parity contract (``OrgStatus`` enum has
# no ``stopped``; runtime STOPPED collapses to ``dormant`` on the spec
# side because both states refuse new commands and re-allow start).
_LIFECYCLE_TO_SPEC_STATUS: dict[str, str] = {
    "start": "active",
    "stop": "dormant",
    "pause": "paused",
    "resume": "active",
}


def _sync_spec_status_after_lifecycle(request: Request, org_id: str, verb: str) -> None:
    """Best-effort spec ``status`` write-back after a successful lifecycle verb.

    Failures are logged at WARNING and swallowed: the runtime side
    of the transition has already succeeded, so refusing to ack the
    HTTP call would be worse than letting the next ``GET /{id}``
    show a slightly stale spec status.
    """
    target = _LIFECYCLE_TO_SPEC_STATUS.get(verb)
    if target is None:
        return
    try:
        mgr = _get_manager(request)
    except HTTPException:
        # Manager subsystem missing -- leave the spec alone; the runtime
        # transition already succeeded so we surface the runtime envelope.
        return
    update_status = getattr(mgr, "update_status", None)
    if update_status is None:
        return
    try:
        update_status(org_id, target)
    except Exception as exc:  # noqa: BLE001 - sync is best-effort
        logger.warning(
            "[OrgLifecycle] failed to sync spec status after %s_org(%s): %s",
            verb,
            org_id,
            exc,
        )


@router.post("/{org_id}/start", summary="B34 start organization")
async def start_org(request: Request, org_id: str) -> Any:
    result = await _call_lifecycle(_get_runtime(request), "start", org_id)
    _sync_spec_status_after_lifecycle(request, org_id, "start")
    return result


@router.post("/{org_id}/stop", summary="B35 stop organization")
async def stop_org(request: Request, org_id: str) -> Any:
    result = await _call_lifecycle(_get_runtime(request), "stop", org_id)
    _sync_spec_status_after_lifecycle(request, org_id, "stop")
    return result


@router.post("/{org_id}/pause", summary="B36 pause organization")
async def pause_org(request: Request, org_id: str) -> Any:
    result = await _call_lifecycle(_get_runtime(request), "pause", org_id)
    _sync_spec_status_after_lifecycle(request, org_id, "pause")
    return result


@router.post("/{org_id}/resume", summary="B37 resume organization")
async def resume_org(request: Request, org_id: str) -> Any:
    """Resume a paused org back to ACTIVE.

    Source-state guard (v11 #5): the underlying lifecycle state machine
    historically allowed STOPPED -> ACTIVE because ``start_org`` and
    ``resume_org`` shared the same target transition table. Semantically
    a stopped org has drained its mailboxes and cancelled in-flight
    work; bringing it back online should go through ``start_org`` so
    the per-node spin-up path runs from scratch. We surface a 400
    illegal-transition envelope here instead of silently aliasing
    resume to start, mirroring how the rest of the dispatch surface
    speaks ``{code, ...}`` instead of plain strings.
    """
    rt = _get_runtime(request)
    state_fn = getattr(rt, "_state", None)
    current: str | None = None
    if state_fn is not None and hasattr(state_fn, "get_org_state"):
        try:
            current = state_fn.get_org_state(org_id)
        except Exception:  # noqa: BLE001 - best-effort pre-check; let lifecycle decide on error
            current = None
    if current is not None and current.upper() == "STOPPED":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "illegal_transition",
                "from": "stopped",
                "action": "resume",
                "hint": "use /start instead",
            },
        )
    result = await _call_lifecycle(rt, "resume", org_id)
    _sync_spec_status_after_lifecycle(request, org_id, "resume")
    return result


# ---------------------------------------------------------------------------
# B38-B40: user commands (submit / poll / cancel)
# ---------------------------------------------------------------------------


@router.post("/{org_id}/command", summary="B38 submit user command")
async def send_command(request: Request, org_id: str, body: CommandSubmit) -> dict[str, Any]:
    """``POST /command`` -- builds ``OrgCommandRequest`` and submits via the service."""
    from openakita.orgs import (
        ForwardTarget,
        OrgCommandConflict,
        OrgCommandError,
        OrgCommandRequest,
        OrgCommandSource,
        OrgCommandSurface,
        OrgOutputScope,
    )

    svc = _get_command_service(request)
    src_data = body.source or {}
    source = OrgCommandSource(
        channel=str(src_data.get("channel", "desktop")),
        chat_id=str(src_data.get("chat_id", "")),
        user_id=str(src_data.get("user_id", "desktop_user")),
        thread_id=src_data.get("thread_id"),
        client_id=str(src_data.get("client_id", "")),
        display_name=str(src_data.get("display_name", "")),
    )
    forward: list[Any] = []
    for item in (body.forward_to or [])[:8]:
        ft = ForwardTarget.from_dict(item) if hasattr(ForwardTarget, "from_dict") else None
        if ft is not None:
            forward.append(ft)
    try:
        return await svc.submit(
            OrgCommandRequest(
                org_id=org_id,
                content=body.content,
                target_node_id=body.target_node_id,
                source=source,
                origin_surface=OrgCommandSurface(body.origin_surface.value),
                output_scope=(
                    OrgOutputScope(body.output_scope.value) if body.output_scope else None
                ),
                replace_existing=body.replace_existing,
                continue_previous=body.continue_previous,
                forward_to=forward,
            )
        )
    except OrgCommandConflict as exc:
        raise HTTPException(
            getattr(exc, "status_code", 409),
            {
                "code": "org_command_conflict",
                "message": str(exc),
                "command_id": getattr(exc, "command_id", None),
            },
        ) from exc
    except OrgCommandError as exc:
        raise HTTPException(getattr(exc, "status_code", 400), str(exc)) from exc


@router.get("/{org_id}/commands/{command_id}", summary="B39 get command status")
def get_command_status(request: Request, org_id: str, command_id: str) -> dict[str, Any]:
    result = _get_command_service(request).get_status(org_id, command_id)
    if result is None:
        raise HTTPException(404, "Command not found")
    return result


@router.post("/{org_id}/commands/{command_id}/cancel", summary="B40 cancel command")
async def cancel_command(
    request: Request,
    org_id: str,
    command_id: str,
    body: CancelRequest | None = None,
) -> dict[str, Any]:
    svc = _get_command_service(request)
    try:
        result = await svc.cancel(org_id, command_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OrgCmd] cancel failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"cancel failed: {exc}") from exc
    if result is None:
        raise HTTPException(404, "Command not found")
    return result


# ---------------------------------------------------------------------------
# B41: org-level broadcast
# ---------------------------------------------------------------------------


@router.post("/{org_id}/broadcast", summary="B41 broadcast to organization")
async def broadcast_to_org(request: Request, org_id: str) -> dict[str, Any]:
    body = await request.json()
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, "content is required")
    rt = _get_runtime(request)
    broadcast = getattr(rt, "broadcast_to_org", None) or getattr(rt, "broadcast", None)
    if broadcast is None:
        raise _runtime_method_not_wired("broadcast")
    result = await broadcast(org_id, content)
    return {"result": result}
