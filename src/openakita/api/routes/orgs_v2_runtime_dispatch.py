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

import inspect
import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from openakita.api.schemas.orgs_v2 import CancelRequest, CommandSubmit

from .orgs_v2_runtime import (
    _get_command_service,
    _get_manager,
    _get_runtime,
    _runtime_method_not_wired,
    router,
)
from .orgs_v2_stream import _build_streaming_response

logger = logging.getLogger(__name__)


def _to_dict(obj: Any) -> Any:
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def _coerce_attachment_info(raw: Any) -> Any | None:
    """Normalize a setup-center attachment JSON to the ChatRequest shape.

    Ported from the v1 ``api/routes/orgs.py`` command endpoint (upstream
    e2874585). Accepts both snake_case and camelCase keys so the desktop
    composer payload round-trips without a separate adapter.
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or raw.get("filename") or "").strip()
    if not name:
        return None
    try:
        from openakita.api.schemas import AttachmentInfo

        return AttachmentInfo(
            type=str(raw.get("type") or "file"),
            name=name,
            url=raw.get("url"),
            local_path=raw.get("local_path") or raw.get("localPath"),
            upload_id=raw.get("upload_id") or raw.get("uploadId"),
            size=raw.get("size"),
            mime_type=raw.get("mime_type") or raw.get("mimeType"),
        )
    except Exception:
        logger.debug("[OrgV2] failed to normalize attachment: %r", raw, exc_info=True)
        return None


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


def _plugin_tool_names(manifest: Any) -> set[str]:
    """Return tool names declared by a loaded plugin manifest."""

    provides = getattr(manifest, "provides", None)
    if not isinstance(provides, dict):
        return set()
    names: set[str] = set()
    for entry in provides.get("tools") or []:
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            name = entry.get("name") or entry.get("id")
        else:
            name = None
        if isinstance(name, str) and name:
            names.add(name)
    return names


async def _org_start_readiness(request: Request, org_id: str) -> dict[str, Any]:
    """Check workbench dependencies without mutating organization state."""

    org = _get_manager(request).get(org_id)
    if org is None:
        raise HTTPException(404, f"Organization not found: {org_id}")

    raw_nodes = getattr(org, "nodes", None)
    nodes = list(raw_nodes) if isinstance(raw_nodes, (list, tuple)) else []
    plugin_nodes: dict[str, list[Any]] = {}
    for node in nodes:
        origin = getattr(node, "plugin_origin", None)
        if not isinstance(origin, dict):
            continue
        plugin_id = str(origin.get("plugin_id") or "").strip()
        if plugin_id:
            plugin_nodes.setdefault(plugin_id, []).append(node)

    issues: list[dict[str, Any]] = []
    agent = getattr(request.app.state, "agent", None)
    plugin_manager = getattr(agent, "_plugin_manager", None) if agent is not None else None

    for plugin_id, owned_nodes in sorted(plugin_nodes.items()):
        loaded = None
        if plugin_manager is not None:
            getter = getattr(plugin_manager, "get_loaded", None)
            if callable(getter):
                loaded = getter(plugin_id)
            else:
                loaded = getattr(plugin_manager, "loaded_plugins", {}).get(plugin_id)
        if loaded is None:
            issues.append(
                {
                    "code": "plugin_not_loaded",
                    "plugin_id": plugin_id,
                    "node_ids": [str(getattr(node, "id", "")) for node in owned_nodes],
                }
            )
            continue

        declared_tools = _plugin_tool_names(getattr(loaded, "manifest", None))
        required_tools = {
            str(tool)
            for node in nodes
            for tool in (getattr(node, "external_tools", None) or [])
            if isinstance(tool, str) and tool in declared_tools
        }
        registered_tools = set(getattr(getattr(loaded, "api", None), "_registered_tools", []) or [])
        missing_tools = sorted(required_tools - registered_tools)
        if missing_tools:
            issues.append(
                {
                    "code": "plugin_tools_missing",
                    "plugin_id": plugin_id,
                    "missing_tools": missing_tools,
                }
            )

        instance = getattr(loaded, "instance", None)
        readiness_check = getattr(instance, "check_org_readiness", None)
        if callable(readiness_check):
            try:
                result = readiness_check()
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:  # noqa: BLE001 - readiness must fail closed
                logger.exception("Plugin readiness check failed: %s", plugin_id)
                issues.append(
                    {
                        "code": "plugin_readiness_failed",
                        "plugin_id": plugin_id,
                        "message": str(exc),
                    }
                )
            else:
                if isinstance(result, dict) and result.get("ready") is False:
                    requirements = result.get("missing_requirements") or []
                    issues.append(
                        {
                            "code": "plugin_requirements_missing",
                            "plugin_id": plugin_id,
                            "missing_requirements": [str(item) for item in requirements if item],
                        }
                    )

    return {"ready": not issues, "issues": issues}


def _org_not_ready(readiness: dict[str, Any]) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "org_start_not_ready",
            "message": "Organization dependencies are not ready.",
            "guidance": "Install, enable, and configure the required workbench plugins.",
            "issues": readiness["issues"],
        },
    )


async def _require_org_start_readiness(request: Request, org_id: str) -> None:
    readiness = await _org_start_readiness(request, org_id)
    if not readiness["ready"]:
        raise _org_not_ready(readiness)


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
    await _require_org_start_readiness(request, org_id)
    result = await _call_lifecycle(_get_runtime(request), "start", org_id)
    _sync_spec_status_after_lifecycle(request, org_id, "start")
    return result


@router.get("/{org_id}/start-readiness", summary="Check organization start readiness")
async def get_start_readiness(request: Request, org_id: str) -> dict[str, Any]:
    return await _org_start_readiness(request, org_id)


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
    await _require_org_start_readiness(request, org_id)
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

    await _require_org_start_readiness(request, org_id)
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

    # Input attachments (upstream e2874585): the composer may attach files.
    # Inline text-file contents / local paths into the execution ``content``
    # while keeping the original text as ``user_facing_content`` so the
    # console history bubble stays clean.
    user_facing_content = body.content
    run_content = body.content
    structured_attachments: list[dict[str, Any]] = []
    coerced = [
        att
        for att in (_coerce_attachment_info(item) for item in (body.attachments or [])[:20])
        if att is not None
    ]
    if coerced:
        try:
            from openakita.api.routes.chat import _enrich_org_content_with_attachments

            run_content = _enrich_org_content_with_attachments(body.content, coerced)
        except Exception:
            logger.warning("[OrgV2] failed to enrich org command attachments", exc_info=True)
        structured_attachments = [
            {
                "type": getattr(att, "type", "file"),
                "name": getattr(att, "name", ""),
                "url": getattr(att, "url", None),
                "local_path": getattr(att, "local_path", None),
                "upload_id": getattr(att, "upload_id", None),
                "size": getattr(att, "size", None),
                "mime_type": getattr(att, "mime_type", None),
                "uploadStatus": "uploaded",
            }
            for att in coerced
        ]

    try:
        return await svc.submit(
            OrgCommandRequest(
                org_id=org_id,
                content=run_content,
                target_node_id=body.target_node_id,
                source=source,
                origin_surface=OrgCommandSurface(body.origin_surface.value),
                # ``body.output_scope`` is now a non-optional schema field
                # (default ``INTERNAL``; exploratory v12 §10.1 fix), so the
                # ``None`` branch that used to leak into ``command_service``
                # is gone.
                output_scope=OrgOutputScope(body.output_scope.value),
                replace_existing=body.replace_existing,
                continue_previous=body.continue_previous,
                forward_to=forward,
                user_facing_content=user_facing_content,
                input_attachments=structured_attachments,
            )
        )
    except OrgCommandConflict as exc:
        raise HTTPException(
            getattr(exc, "status_code", 409),
            {
                "code": getattr(exc, "error_code", "org_command_conflict"),
                "message": str(exc),
                "command_id": getattr(exc, "command_id", None),
                "org_status": getattr(exc, "org_status", None),
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


# ---------------------------------------------------------------------------
# Sprint-9 (supervisor HTTP takeover): SSE alias under /api/v2/orgs.
#
# The original SSE route lives at ``/api/v2/orgs-spec/{id}/stream`` (see
# ``orgs_v2_stream.py``) because P-RC-3 split the spec / runtime URL
# namespaces. v17-v20 exploratory probes (``_v*_biz/b6_chaos.py::B6.5``)
# hit ``/api/v2/orgs/{id}/events/stream`` instead and 404'd, which then
# masqueraded as "SSE broken". This alias re-mounts the same body under
# the runtime router so both URL patterns work; the legacy ``/orgs-spec/``
# path is preserved so the frontend's ``v2Stream.ts`` does not have to
# change in this commit.
# ---------------------------------------------------------------------------


@router.get(
    "/{org_id}/events/stream",
    summary="Sprint-9 SSE alias of /api/v2/orgs-spec/{id}/stream",
)
async def stream_org_events(request: Request, org_id: str) -> StreamingResponse:
    return _build_streaming_response(request, org_id)


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
