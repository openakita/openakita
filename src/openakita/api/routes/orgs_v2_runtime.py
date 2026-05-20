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


def _get_runtime(request: Request) -> Any:
    """Lift ``OrgRuntime`` (P9.6) off ``request.app.state``."""
    rt = getattr(request.app.state, "org_runtime", None)
    if rt is None:
        raise HTTPException(503, "OrgRuntime not initialized")
    return rt


def _get_manager(request: Request) -> Any:
    """Lift ``OrgManager`` (P9.5) off ``request.app.state``."""
    mgr = getattr(request.app.state, "org_manager", None)
    if mgr is None:
        raise HTTPException(503, "OrgManager not initialized")
    return mgr


def _get_command_service(request: Request) -> Any:
    """Lift ``OrgCommandService`` (P9.4) off ``request.app.state``."""
    svc = getattr(request.app.state, "org_command_service", None)
    if svc is None:
        raise HTTPException(503, "OrgCommandService not initialized")
    return svc


def _get_blackboard(request: Request) -> Any:
    """Lift ``OrgBlackboard`` (P9.1) off ``request.app.state``."""
    bb = getattr(request.app.state, "org_blackboard", None)
    if bb is None:
        raise HTTPException(503, "OrgBlackboard not initialized")
    return bb


def _get_project_store(request: Request) -> Any:
    """Lift ``ProjectStore`` (P9.2) off ``request.app.state``."""
    ps = getattr(request.app.state, "project_store", None)
    if ps is None:
        raise HTTPException(503, "ProjectStore not initialized")
    return ps


def _get_scheduler(request: Request) -> Any:
    """Lift ``NodeScheduler`` (P9.3) off ``request.app.state``."""
    sch = getattr(request.app.state, "node_scheduler", None)
    if sch is None:
        raise HTTPException(503, "NodeScheduler not initialized")
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


@router.get("/_p97/health", summary="P9.7 router wiring sanity probe")
def p97_health() -> dict[str, Any]:
    """Return a tiny envelope confirming the P9.7 router is mounted.

    Does NOT touch any subsystem -- the probe must work even when
    ``request.app.state`` is empty (e.g. a pytest-only app),
    otherwise the test gate cannot smoke the wiring before the
    real endpoints land.
    """
    return {
        "ok": True,
        "subsystems": list(_SUBSYSTEMS),
        "p97_phase": "alpha-2",
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
    orgs_v2_runtime_orgs,  # noqa: E402, F401 -- side-effect import (B1-B17)
    orgs_v2_runtime_state,  # noqa: E402, F401 -- side-effect import (B42-B53)
)
