"""Host-level REST API for plugin system dependencies.

Wraps the SDK's :class:`openakita_plugin_sdk.contrib.DependencyGate` so that
the UI Kit ``dep-gate.js`` widget can:

- ``GET  /api/plugins/_sdk/deps/check?ids=ffmpeg,whisper.cpp`` — current
  detection status.
- ``POST /api/plugins/_sdk/deps/install`` — server-sent events streaming
  installer stdout/stderr/exit. Body: ``{ "id": "ffmpeg", "method_index": 0 }``.
- ``GET  /api/plugins/_sdk/deps/audit-log?limit=50`` — last N install
  attempts (for transparency / debugging).

Security boundary
-----------------
- Allow-list lives in ``openakita_plugin_sdk.contrib.dep_catalog``; this
  router refuses any id not present there.
- ``method.requires_sudo`` blocks the request unless the host process has
  effective root (POSIX). Windows / macOS user installers (winget, brew)
  never need root and are unaffected.
- Every install attempt is appended to an audit log under
  ``data/plugin_deps_audit.jsonl`` with timestamp + outcome.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from ...config import settings
from ...plugins.sdk_loader import ensure_plugin_sdk_on_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins/_sdk/deps", tags=["plugin-deps"])

# One concurrent installer at a time, regardless of which dep — winget / brew /
# apt all serialise on the underlying package DB anyway, and serialising at
# the API layer prevents a runaway client from spawning N parallel installs.
_install_lock = asyncio.Lock()


def _audit_log_path() -> Path:
    return Path(settings.project_root) / "data" / "plugin_deps_audit.jsonl"


def _build_gate():
    """Construct a fresh ``DependencyGate`` over the built-in catalog.

    Importing here keeps server start-up free of SDK imports — the SDK is an
    optional dep of openakita and ``ensure_plugin_sdk_on_path`` covers the
    monorepo dev case before we touch it.
    """
    ensure_plugin_sdk_on_path()
    try:
        from openakita_plugin_sdk.contrib import DEP_CATALOG, DependencyGate
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "openakita_plugin_sdk_missing",
                "message": (
                    "openakita-plugin-sdk is not installed. "
                    "Install with `pip install \"openakita[plugins]\"` "
                    "or, in monorepo dev, `pip install -e ./openakita-plugin-sdk`."
                ),
                "import_error": str(exc),
            },
        ) from exc
    return DependencyGate(DEP_CATALOG)


def _serialise_dep(dep: Any) -> dict[str, Any]:
    """Public-safe view of a SystemDependency (no install argv leaked)."""
    methods_pub = []
    for m in dep.install_methods:
        methods_pub.append(
            {
                "platform": m.platform,
                "strategy": m.strategy,
                "description": m.description,
                "requires_sudo": m.requires_sudo,
                "requires_confirm": m.requires_confirm,
                "manual_url": m.manual_url,
                "estimated_seconds": m.estimated_seconds,
            }
        )
    return {
        "id": dep.id,
        "display_name": dep.display_name,
        "description": dep.description,
        "homepage": dep.homepage,
        "install_methods": methods_pub,
    }


def _audit(record: dict[str, Any]) -> None:
    """Append one JSON line to the install audit log. Never raises."""
    try:
        path = _audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.time(), **record}
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("Audit log write failed: %s", exc)


# ── /check ─────────────────────────────────────────────────────────────


@router.get("/catalog")
async def list_catalog() -> dict[str, Any]:
    """Return the full public catalog (no argv) for UI rendering."""
    gate = _build_gate()
    return {
        "platform": _current_platform(),
        "items": [_serialise_dep(dep) for dep in gate.catalog.values()],
    }


@router.get("/check")
async def check_deps(
    request: Request,
    ids: str | None = Query(default=None, description="Comma-separated dep ids"),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Return detection status for one or more dependencies."""
    gate = _build_gate()
    requested = [x.strip() for x in (ids or "").split(",") if x.strip()] or None

    if requested:
        unknown = [x for x in requested if not gate.known(x)]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail={"error": "unknown_dep_ids", "ids": unknown},
            )

    target_ids = requested or list(gate.catalog.keys())
    statuses = {dep_id: gate.check(dep_id, force=force).to_dict() for dep_id in target_ids}
    return {
        "platform": _current_platform(),
        "statuses": statuses,
    }


# ── /install (SSE) ─────────────────────────────────────────────────────


class InstallRequest(BaseModel):
    id: str = Field(..., description="Dependency id from /catalog")
    method_index: int = Field(default=0, ge=0)


def _current_platform() -> str:
    ensure_plugin_sdk_on_path()
    try:
        from openakita_plugin_sdk.contrib import current_platform

        return current_platform()
    except ImportError:
        return "unknown"


def _is_root() -> bool:
    """POSIX-only effective root check; Windows always returns False."""
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return False


def _format_sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


async def _install_stream(req: InstallRequest, client_ip: str) -> AsyncIterator[bytes]:
    gate = _build_gate()
    if not gate.known(req.id):
        yield _format_sse({"phase": "error", "dep_id": req.id, "line": "unknown_dep"})
        _audit({"action": "install", "dep_id": req.id, "result": "unknown"})
        return

    methods = gate.list_install_methods(req.id)
    if not methods:
        yield _format_sse(
            {
                "phase": "skip",
                "dep_id": req.id,
                "line": f"No automated installer for {req.id} on this OS",
                "extra": {"reason": "no_method"},
            }
        )
        _audit({"action": "install", "dep_id": req.id, "result": "no_method"})
        return

    if req.method_index >= len(methods):
        yield _format_sse(
            {
                "phase": "error",
                "dep_id": req.id,
                "line": f"method_index {req.method_index} out of range",
            }
        )
        return

    method = methods[req.method_index]
    if method.requires_sudo and not _is_root():
        yield _format_sse(
            {
                "phase": "error",
                "dep_id": req.id,
                "line": (
                    f"Installer for {req.id} via {method.strategy} requires root. "
                    "Run the recommended command manually as your packaging policy permits."
                ),
                "extra": {"reason": "needs_sudo", "command_hint": " ".join(method.command or ())},
            }
        )
        _audit(
            {
                "action": "install",
                "dep_id": req.id,
                "method": method.strategy,
                "result": "needs_sudo",
                "client_ip": client_ip,
            }
        )
        return

    if _install_lock.locked():
        yield _format_sse(
            {
                "phase": "error",
                "dep_id": req.id,
                "line": "Another dependency install is already running. Try again in a moment.",
                "extra": {"reason": "busy"},
            }
        )
        return

    async with _install_lock:
        _audit(
            {
                "action": "install_start",
                "dep_id": req.id,
                "method": method.strategy,
                "platform": method.platform,
                "client_ip": client_ip,
            }
        )
        outcome = "unknown"
        last_rc: int | None = None
        try:
            async for event in gate.install(req.id, method_index=req.method_index):
                yield _format_sse(event.to_dict())
                if event.phase == "exit":
                    last_rc = event.return_code
                if event.phase == "done":
                    outcome = "success"
                if event.phase == "error":
                    outcome = "failure"
        except Exception as exc:
            logger.exception("Install stream crashed for %s", req.id)
            yield _format_sse(
                {"phase": "error", "dep_id": req.id, "line": f"internal_error: {exc}"}
            )
            outcome = "crash"
        finally:
            _audit(
                {
                    "action": "install_end",
                    "dep_id": req.id,
                    "method": method.strategy,
                    "result": outcome,
                    "return_code": last_rc,
                    "client_ip": client_ip,
                }
            )


@router.post("/install")
async def install_dep(req: InstallRequest, request: Request) -> StreamingResponse:
    """Stream install events for one dependency (SSE).

    The response uses ``text/event-stream``; clients should consume with
    ``EventSource`` or ``fetch + ReadableStream``.
    """
    client_ip = request.client.host if request.client else "unknown"
    return StreamingResponse(
        _install_stream(req, client_ip=client_ip),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
        },
    )


# ── /audit-log ─────────────────────────────────────────────────────────


@router.get("/audit-log")
async def get_audit_log(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    """Return the last *limit* audit entries, newest last."""
    path = _audit_log_path()
    if not path.exists():
        return {"entries": []}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"audit log read failed: {exc}") from exc

    entries = []
    for raw in lines[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return {"entries": entries}
