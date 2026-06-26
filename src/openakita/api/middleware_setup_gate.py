"""FastAPI middleware that enforces the first-run Setup flow.

When OpenAkita is reachable on the LAN but no web-access password has been
configured yet, **non-loopback** API callers must complete the Setup flow
before any other endpoint will respond. This prevents the "anyone on the
LAN can call internal APIs on a fresh install" footgun that used to be
hidden by the auto-generated password (printed to logs once and then
forgotten).

Decision delegated to :mod:`openakita.api.setup_state`. This module only
owns the path allowlist and the HTTP envelope of the gate response.

Ordering note: register this middleware so it runs *before*
:func:`openakita.api.auth.create_auth_middleware`. With FastAPI's LIFO
middleware stack, that means *add it last*. The auth middleware would
otherwise issue a 401 first, hiding the actionable 428 from the frontend.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from .auth import WebAccessConfig
from .setup_state import should_require_setup

logger = logging.getLogger(__name__)


# Exact paths that must always be reachable, even before setup is complete.
# Kept narrow on purpose: the SPA HTML shell, the health probe, and the two
# setup endpoints themselves. The setup-page POST handler delegates to
# :meth:`WebAccessConfig.change_password`, which is also exposed at
# ``/api/auth/change-password`` — we exempt it here so a loopback client can
# call either spelling without 428 noise.
SETUP_GATE_ALLOW_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/api/health",
        "/api/healthz",
        "/api/readyz",
        "/api/auth/setup",
        "/api/auth/setup-status",
        "/api/auth/change-password",
        "/api/logs/frontend",
    }
)

# Prefixes that the SPA / docs need before the user is even logged in.
# - ``/web/``      : static assets the SetupView itself loads.
# - ``/docs``      : OpenAPI swagger UI (still hidden from external network
#                    when ``ENV=production`` but harmless to allow here).
# - ``/redoc``     : same.
# - ``/openapi.json`` : the schema doc.
# - ``/user-docs/``: user documentation served as static files.
# - ``/static/``   : misc static assets.
# Note: ``/ws/`` is intentionally *not* listed — WebSocket connections do
# their own token authentication, and the setup gate is HTTP-only.
SETUP_GATE_ALLOW_PREFIXES: tuple[str, ...] = (
    "/web/",
    "/web",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/user-docs",
    "/static/",
)


def _is_allowed_path(path: str) -> bool:
    if path in SETUP_GATE_ALLOW_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in SETUP_GATE_ALLOW_PREFIXES)


def _is_api_path(path: str) -> bool:
    """Whether ``path`` looks like an API call (vs. SPA navigation)."""
    return path.startswith("/api/")


def create_setup_gate_middleware(web_access: WebAccessConfig):
    """Build the middleware closure bound to a specific ``WebAccessConfig``.

    Returns a coroutine suitable for ``app.middleware("http")(...)``. The
    closure also captures ``web_access`` by reference so a runtime password
    change is visible without re-creating the middleware.
    """

    async def setup_gate_middleware(request: Request, call_next):
        # CORS preflight: pass through unconditionally.
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        # Allowlist for SPA shell + setup endpoints + health probes.
        if _is_allowed_path(path):
            return await call_next(request)

        # No setup needed? Continue down the chain.
        if not should_require_setup(request, web_access):
            return await call_next(request)

        # At this point, a non-trusted-local caller hit a non-allowlisted
        # endpoint while the system has no password set.
        if _is_api_path(path):
            # Visible-to-frontend signal: 428 Precondition Required +
            # machine-readable body. The frontend SetupView listener picks
            # this up and switches to the setup screen.
            logger.info(
                "setup_gate blocked %s %s from %s — no password set",
                request.method,
                path,
                request.client.host if request.client else "unknown",
            )
            return JSONResponse(
                status_code=428,
                content={
                    "error": "setup_required",
                    "detail": "Web access password not configured. "
                    "Complete the setup flow before using the API.",
                    "setup_url": "/web/#/setup",
                },
            )

        # For non-API paths (SPA navigation, image fetches that slipped past
        # the allowlist, etc.) fall through to the normal handler — the SPA
        # will see its own ``/api/auth/setup-status`` call and route the user.
        return await call_next(request)

    return setup_gate_middleware
