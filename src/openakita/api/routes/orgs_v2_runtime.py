"""V2 organisation runtime API skeleton (P-RC-9 P9.7a-2c).

This router will host the bulk P9.7 mint -- the 83 v2 endpoints
(Group B per ``docs/revamp/P-RC-9-P9.7-ENDPOINT-INVENTORY.md``)
that wire the FastAPI HTTP layer to the six P9.1-P9.6
ADR-0011 subsystems (OrgBlackboard / ProjectStore /
NodeScheduler / OrgCommandService / OrgManager / OrgRuntime).

P9.7a-2c (this commit) is the **scaffold only**:

* APIRouter mounted on ``/api/v2/orgs`` -- the namespace the
  P-RC-3 Group A routers vacated in P9.7a-2a (relocated to
  ``/api/v2/orgs-spec``; see D-1 R3 LOCKED).
* Six Depends-free ``_get_*(request)`` helpers per D-4 LOCKED
  (``docs/revamp/P-RC-9-P9.7-DECISIONS.md``). Each lifts a
  subsystem off ``request.app.state`` and raises ``503`` if
  the subsystem is unbound -- mirrors the v1 ``orgs.py``
  pattern byte-for-byte. The helpers are intentionally
  free functions (not FastAPI ``Depends`` factories): the
  charter section 8 R4 risk note explicitly resists
  introducing a ``RestAuthProtocol`` for the seam.
* One stub endpoint ``GET /_p97/health`` -- a sanity probe
  the smoke test pins to confirm the router is wired before
  the bulk P9.7a-3 / P9.7beta endpoints land.

P9.7a-3 onwards extends this module with the 83 minted
endpoints, splitting into ``orgs_v2_manager.py`` /
``orgs_v2_projects.py`` (etc.) if this file's LOC exceeds
~1 200 (the ADR-0014 sub-cap floor).

ADR refs: ADR-0011 (D-3 layer separation; D-4 flat helpers
per R4 granularity ceiling), ADR-0012 (no shim under v1).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/orgs", tags=["v2:组织运行时"])


# ---------------------------------------------------------------------------
# Subsystem accessors (D-4 LOCKED: Depends-free request.app.state pattern)
#
# Mirrors v1 ``api/routes/orgs.py`` ``_get_manager`` etc. byte-for-byte.
# Six helpers map to the six ADR-0011 subsystems P9.1-P9.6 land. Each
# raises ``503`` if the subsystem is not bound on ``app.state`` -- v1
# does the same; the failure mode keeps parity-style debugging easy.
# ---------------------------------------------------------------------------


_NEXT_MILESTONE = "P9.7gamma"


def _subsystem_unavailable(subsystem: str, klass: str) -> HTTPException:
    """Return a structured 503 for an unwired subsystem.

    The exploratory v10 report (issue #1) flagged that the original
    "<Klass> not initialized" string leaked through to the desktop chat
    as raw text. A structured detail lets the frontend choose between a
    "Subsystem coming soon" banner and a full error dialog without
    having to regex the message.
    """
    return HTTPException(
        status_code=503,
        detail={
            "code": "subsystem_not_wired",
            "subsystem": subsystem,
            "message": (
                f"{klass} is registered but not yet connected. "
                "See PR-9.7 wiring."
            ),
            "next_milestone": _NEXT_MILESTONE,
        },
    )


def _runtime_method_not_wired(method_name: str) -> HTTPException:
    """Return a structured 503 for a missing method on ``OrgRuntime``.

    The exploratory v11 report (issue #1) flagged that several
    runtime endpoints (status / stats / broadcast / lifecycle verbs)
    still raised plain-string 503s like
    ``"OrgRuntime.get_status_snapshot not wired"``, whereas the v10
    follow-up Fix-12 only structured the subsystem-level guards and
    the ``orgs_v2_runtime_nodes._call_runtime_method`` family. This
    helper shares the same envelope shape as ``_subsystem_unavailable``
    so the frontend has a single contract: ``detail.code`` is always
    ``"subsystem_not_wired"`` and ``detail.subsystem`` is either a
    bare subsystem name or ``"runtime_method:<name>"`` for missing
    duck-typed runtime methods.
    """
    return HTTPException(
        status_code=503,
        detail={
            "code": "subsystem_not_wired",
            "subsystem": f"runtime_method:{method_name}",
            "message": (
                f"OrgRuntime.{method_name} is not yet connected. "
                "See PR-9.7gamma wiring."
            ),
            "next_milestone": _NEXT_MILESTONE,
        },
    )


def _get_runtime(request: Request) -> Any:
    """Lift ``OrgRuntime`` (P9.6) off ``request.app.state``."""
    rt = getattr(request.app.state, "org_runtime", None)
    if rt is None:
        raise _subsystem_unavailable("runtime", "OrgRuntime")
    return rt


def _get_manager(request: Request) -> Any:
    """Lift ``OrgManager`` (P9.5) off ``request.app.state``."""
    mgr = getattr(request.app.state, "org_manager", None)
    if mgr is None:
        raise _subsystem_unavailable("manager", "OrgManager")
    return mgr


def _get_command_service(request: Request) -> Any:
    """Lift ``OrgCommandService`` (P9.4) off ``request.app.state``."""
    svc = getattr(request.app.state, "org_command_service", None)
    if svc is None:
        raise _subsystem_unavailable("command_service", "OrgCommandService")
    return svc


def _scope_to_org(subsystem: Any, request: Request) -> Any:
    """Resolve a per-org backend from a scoped registry.

    The projects / blackboard routes call the store WITHOUT an org_id
    (it is in the URL path), but the real backends are per-org.     When the
    wired instance is an ``OrgScoped*`` registry, resolve the concrete
    per-org backend using the path ``org_id`` so org isolation holds.
    Plain instances (e.g. injected test doubles) are returned as-is.

    The check is an explicit ``isinstance`` against
    :class:`OrgScopedRegistry` rather than ``hasattr(obj, "for_org")``
    so a ``unittest.mock.Mock`` double (which auto-vivifies *any*
    attribute, ``for_org`` included) is NOT mistaken for a real
    registry and stays returned verbatim with its configured returns.
    """
    from openakita.orgs.scoped_subsystems import OrgScopedRegistry

    org_id = request.path_params.get("org_id")
    if isinstance(subsystem, OrgScopedRegistry) and org_id:
        return subsystem.for_org(org_id)
    return subsystem


def _get_blackboard(request: Request) -> Any:
    """Lift the per-org ``OrgBlackboard`` (P9.1) for the request path org."""
    bb = getattr(request.app.state, "org_blackboard", None)
    if bb is None:
        raise _subsystem_unavailable("blackboard", "OrgBlackboard")
    return _scope_to_org(bb, request)


def _get_project_store(request: Request) -> Any:
    """Lift the per-org ``ProjectStore`` (P9.2) for the request path org."""
    ps = getattr(request.app.state, "project_store", None)
    if ps is None:
        raise _subsystem_unavailable("project_store", "ProjectStore")
    return _scope_to_org(ps, request)


def _get_scheduler(request: Request) -> Any:
    """Lift ``NodeScheduler`` (P9.3) off ``request.app.state``."""
    sch = getattr(request.app.state, "node_scheduler", None)
    if sch is None:
        raise _subsystem_unavailable("scheduler", "NodeScheduler")
    return sch


# ---------------------------------------------------------------------------
# Stub endpoint -- sanity wiring probe (NOT a part of the 83 mint)
# ---------------------------------------------------------------------------


_SUBSYSTEMS: tuple[str, ...] = (
    "runtime",
    "manager",
    "command_service",
    "blackboard",
    "project_store",
    "scheduler",
)

_SUBSYSTEM_STATE_ATTRS: dict[str, str] = {
    "runtime": "org_runtime",
    "manager": "org_manager",
    "command_service": "org_command_service",
    "blackboard": "org_blackboard",
    "project_store": "project_store",
    "scheduler": "node_scheduler",
}

# Methods we expect to be live before declaring a subsystem fully
# "wired" (vs. only "registered"). The exploratory v10 report flagged
# several subsystems returning 503 from individual endpoints even
# though /_p97/health reported them as healthy, because health only
# checked whether ``app.state`` had a non-None reference. The frontend
# needs a stricter signal so it can hide UI sections whose endpoints
# would 503.
_SUBSYSTEM_REQUIRED_METHODS: dict[str, tuple[str, ...]] = {
    "runtime": ("get_status_snapshot", "freeze_node", "set_node_status"),
    "manager": ("list_orgs",),
    "command_service": ("submit",),
    "blackboard": ("publish",),
    "project_store": ("list_projects",),
    "scheduler": ("list_schedules",),
}


def _probe_subsystem(request: Request, name: str) -> dict[str, Any]:
    """Inspect a single subsystem's state for ``/_p97/health``."""
    attr = _SUBSYSTEM_STATE_ATTRS[name]
    instance = getattr(request.app.state, attr, None)
    registered = instance is not None
    required = _SUBSYSTEM_REQUIRED_METHODS.get(name, ())
    missing: list[str] = []
    if registered and required:
        for method in required:
            target = getattr(instance, method, None)
            if not callable(target):
                missing.append(method)
    wired = registered and not missing
    return {
        "name": name,
        "registered": registered,
        "wired": wired,
        "missing_methods": missing,
    }


@router.get("/_p97/health", summary="P9.7 router wiring sanity probe")
def p97_health(request: Request) -> dict[str, Any]:
    """Return a small envelope describing P9.7 wiring state.

    Backward-compatible: the legacy ``ok`` / ``subsystems`` /
    ``p97_phase`` fields stay; the per-subsystem ``details`` array
    gives ``{name, registered, wired, missing_methods}`` so the
    frontend (and exploratory probes) can decide whether to call the
    real endpoints. Does NOT touch any subsystem beyond a
    ``getattr(..., method) is callable`` check, so it remains safe on
    a pytest-only app where ``request.app.state`` is empty.
    """
    details = [_probe_subsystem(request, name) for name in _SUBSYSTEMS]
    all_wired = all(item["wired"] for item in details)
    return {
        "ok": True,
        "subsystems": list(_SUBSYSTEMS),
        "p97_phase": "alpha-2",
        "details": details,
        "all_wired": all_wired,
    }


# ---------------------------------------------------------------------------
# Sub-module aggregation -- P9.7beta endpoint clusters (B1-B83).
#
# Each sub-module imports ``router`` + the ``_get_*`` helpers from this
# module and registers its cluster of endpoints via decorators. Importing
# the sub-modules HERE (not in server.py) keeps the router-aggregation
# story in a single place; the side effects of the imports are the
# ``@router.<method>`` decorations.
# ---------------------------------------------------------------------------

from . import (
    orgs_v2_runtime_dispatch,  # noqa: E402, F401 -- side-effect import (B34-B41)
    orgs_v2_runtime_nodes,  # noqa: E402, F401 -- side-effect import (B18-B33)
    orgs_v2_runtime_ops,  # noqa: E402, F401 -- side-effect import (B54-B67)
    orgs_v2_runtime_orgs,  # noqa: E402, F401 -- side-effect import (B1-B17)
    orgs_v2_runtime_projects,  # noqa: E402, F401 -- side-effect import (B68-B83)
    orgs_v2_runtime_state,  # noqa: E402, F401 -- side-effect import (B42-B53)
)
