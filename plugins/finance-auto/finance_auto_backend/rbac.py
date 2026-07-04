"""Tiny RBAC helper for finance-auto routes (EX-P1-2 fix-round-3).

The collaboration service already owns the heavy lifting
(``CollaborationService.check_permission``).  This module adds two
small things on top so individual route handlers stay one-liner-y:

* ``current_user_id(...)`` — FastAPI dependency that pulls the
  caller's user id from either the ``X-OpenAkita-User-Id`` header
  or a plain ``user_id`` query string, falling back to the v0.2
  ``"local"`` sentinel (which CollaborationService treats as an
  admin shortcut to keep single-user mode working).  This is
  deliberately *additive* — no existing route is required to read
  the header, but every write route we care about can take it as
  an optional ``Depends`` argument.

* ``require_permission(resource, action)`` — returns a FastAPI
  dependency that resolves a CollaborationService against the
  shared connection, calls ``check_permission`` for the caller,
  and raises ``HTTPException(403, "rbac_denied")`` when it fails.
  Audit-logs every denial via the standard logger so operators
  can spot brute-force-y patterns in the host journal.

Why a helper instead of inlining the call in every route?
  - Centralised audit-log line format (one search string).
  - Single source of truth for "where does the user come from?"
    Today: header / query / 'local'.  Tomorrow: session middleware
    contributed by the host.  Routes don't need to change again.
  - The RBAC tests can monkey-patch ``current_user_id`` once
    instead of injecting headers into every fixture.

The wrapper is intentionally NOT mounted as a global FastAPI
``Depends`` because we want the existing v0.2 "no user context"
calls to keep working when the seed permissions don't list the
caller's role; the dependency raises only when the resource/action
pair is actually configured to deny.

EX-P1-2 territory note: this file lives entirely inside the
plugin's backend tree (``plugins/finance-auto/finance_auto_backend/
rbac.py``) so it doesn't violate the routes.py freeze rule — we
import the dependency from each ``*_routes.py`` directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request
from starlette.requests import HTTPConnection

if TYPE_CHECKING:  # avoid circular import at runtime
    from .routes import FinanceAutoService
    from .services.collaboration import CollaborationService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User-id resolution
# ---------------------------------------------------------------------------

USER_ID_HEADER = "X-OpenAkita-User-Id"
"""Header carrying the caller's user id (set by the host shell)."""

LOCAL_USER = "local"
"""Sentinel meaning "no user context"; CollaborationService treats this
as admin so v0.2 single-user mode keeps working."""


def current_user_id(request: Request) -> str:
    """Resolve the caller's user_id.

    Lookup order (first non-empty wins):

    1. ``X-OpenAkita-User-Id`` header — set by the host shell after a
       real login.
    2. ``?user_id=...`` query string — convenient for curl + tests.
    3. Body field ``actor_id`` / ``actor_user_id`` is intentionally
       NOT consulted here because that's the *target* of the action
       (e.g. the user being assigned a role), not the *caller*.  The
       collaboration service layer continues to read the body field
       where it makes sense.
    4. Fallback to the literal ``"local"`` sentinel.
    """
    raw = (request.headers.get(USER_ID_HEADER) or "").strip()
    if raw:
        return raw
    qs = (request.query_params.get("user_id") or "").strip()
    if qs:
        return qs
    return LOCAL_USER


# ---------------------------------------------------------------------------
# Service accessor — pulled out so a test can swap in a fake conn.
# ---------------------------------------------------------------------------


def _collab_for(service: "FinanceAutoService") -> "CollaborationService":
    """Lazily instantiate a CollaborationService against the shared
    aiosqlite connection.  Imported lazily to avoid the circular
    import between ``routes`` and ``rbac``.
    """
    from .services.collaboration import CollaborationService

    return CollaborationService(service.db.conn)


# ---------------------------------------------------------------------------
# Permission-check dependency factory
# ---------------------------------------------------------------------------


def require_permission(
    resource: str,
    action: str,
):
    """Return a FastAPI dependency enforcing
    ``(resource, action)`` against the caller's role.

    The resulting dependency consumes:

    * ``request: Request`` — to extract the user id (see
      ``current_user_id``).
    * Path parameter ``org_id`` — auto-pulled by FastAPI from the
      mounted route's URL.  The dependency reads it via
      ``request.path_params`` so a route doesn't have to declare a
      duplicate parameter.

    On failure: raises ``HTTPException(403, {"error": "rbac_denied",
    "resource": ..., "action": ..., "user_id": ...})`` and logs a
    line at WARNING level containing the same fields.

    On success: returns the user_id string so the route handler
    can capture it via ``user_id: str = Depends(require_permission(...))``.

    Callers must inject a per-request ``service`` via FastAPI's
    standard dependency tree — see ``_get_service_for_request`` below
    which is wired in each ``register_*_endpoints`` site.
    """

    async def _dep(
        request: Request,
        user_id: str = Depends(current_user_id),
    ) -> str:
        # ``service`` is attached to ``request.state`` by the
        # route-registration helper (see ``attach_service_for_rbac``
        # below).  This keeps the dependency decoupled from any
        # specific ``register_*_endpoints`` signature.
        service: "FinanceAutoService" | None = getattr(
            request.state, "finance_auto_service", None
        )
        if service is None:
            # If the route was wired without ``attach_service_for_rbac``
            # we fall open (legacy behaviour) but warn — the next
            # release should make this a hard 500.
            logger.warning(
                "finance-auto rbac: no service attached to request.state "
                "for %s %s; failing open for backward compatibility",
                request.method, request.url.path,
            )
            return user_id

        collab = _collab_for(service)
        org_id = request.path_params.get("org_id") or None
        # Period is optional — used by check_permission for
        # ``scope=assigned`` evaluation but pure resource/action
        # lookup ignores it.
        period_id = (
            request.path_params.get("period_id")
            or request.query_params.get("period_id")
        )
        try:
            allowed = await collab.check_permission(
                user_id=user_id,
                resource=resource,
                action=action,
                org_id=org_id,
                period_id=period_id,
            )
        except Exception as exc:  # noqa: BLE001 — never blow up the
            # route on RBAC infra errors; log + fail open with a
            # clear breadcrumb.
            logger.warning(
                "finance-auto rbac: check_permission errored for "
                "user=%s resource=%s action=%s org_id=%s: %s",
                user_id, resource, action, org_id, exc,
            )
            return user_id
        if not allowed:
            logger.warning(
                "finance-auto rbac: DENY user=%s resource=%s "
                "action=%s org_id=%s period_id=%s",
                user_id, resource, action, org_id, period_id,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "rbac_denied",
                    "resource": resource,
                    "action": action,
                    "user_id": user_id,
                    "org_id": org_id,
                },
            )
        return user_id

    return _dep


# ---------------------------------------------------------------------------
# Service attachment middleware-ish helper
# ---------------------------------------------------------------------------


def attach_service_for_rbac(app_or_router, service: "FinanceAutoService") -> None:
    """Install a tiny request middleware that puts ``service`` on
    ``request.state.finance_auto_service`` so ``require_permission``
    can find it without depending on the route signature.

    Idempotent — calling twice replaces the prior middleware-equivalent
    (FastAPI doesn't dedupe, but our middleware is a no-op the second
    time around because we re-bind the same attribute).

    Designed for FastAPI ``APIRouter`` and ``FastAPI`` app instances;
    in practice the plugin ``build_router`` wires it onto the router
    once it's constructed.
    """
    # We use a route-level dependency rather than a real middleware to
    # avoid mucking with the existing PluginManager mount semantics.
    # An identical effect is achieved by injecting the service into
    # ``request.state`` via a tiny per-request dependency that fires
    # before the route's own dependencies (FastAPI evaluates the
    # router-level dependencies before the path-level ones).

    async def _bind(connection: HTTPConnection) -> None:
        # ``HTTPConnection`` is the common base of HTTP ``Request`` and
        # WebSocket ``WebSocket`` so this dependency fires for both
        # transport types without raising a parameter-injection error
        # on the WS endpoint (which doesn't carry a ``Request``).
        connection.state.finance_auto_service = service

    # ``router.dependencies`` is the canonical way to attach a
    # router-scoped pre-dependency.  FastAPI/Starlette ignores its
    # return value, which suits us — _bind is purely a side-effect.
    try:
        deps = getattr(app_or_router, "dependencies", None)
        if deps is None:
            return
        deps.append(Depends(_bind))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "finance-auto rbac: failed to attach service to %r: %s",
            app_or_router, exc,
        )


__all__ = [
    "LOCAL_USER",
    "USER_ID_HEADER",
    "attach_service_for_rbac",
    "current_user_id",
    "require_permission",
]
