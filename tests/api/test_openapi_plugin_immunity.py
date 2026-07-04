"""Regression sentinel: plugin-induced /openapi.json 500.

Reproduces the exact Pydantic 2.12 ForwardRef('FileResponse') break
pattern from tmp_p10/_f2_repro.py and routes it through the same
`PluginManager._pending_plugin_routers` deferred-mount path that
real plugins use during startup. Asserts the host's /openapi.json
endpoint survives.

Originally fixed by smoke-F2 §B (commit `fix(api): exclude dynamic
plugin routers from /openapi.json schema`). If a future change ever
removes the `include_in_schema=False` guard at
``src/openakita/api/server.py`` (the deferred-mount loop in
``create_app``), this sentinel fires.
"""

from __future__ import annotations  # CONDITION 1 -- PEP 563

from fastapi import APIRouter
from fastapi.testclient import TestClient

from openakita.api.server import create_app


def _build_broken_plugin_router() -> APIRouter:
    """Return a router that mimics the SDK 0.6.x ``upload_preview`` pattern.

    All three Pydantic 2.12 break conditions are deliberately reproduced:
      1. PEP 563 -- enforced by the ``from __future__`` import above; every
         annotation in this module is a string at import time.
      2. ``FileResponse`` is imported only inside the register factory, so
         the closure-defined endpoint''"'"'s ``__globals__`` does NOT see it.
      3. The decorator omits ``response_class=`` -- FastAPI''"'"' default
         path then walks the ``-> FileResponse`` return annotation, which
         on Pydantic >=2.12 raises ``PydanticUserError``.
    """
    router = APIRouter()

    def _register(r: APIRouter) -> None:
        # Local-only import -- _serve.__globals__ won''"'"'t see FileResponse.
        from fastapi.responses import FileResponse  # noqa: F401

        @r.get("/serve")  # NB: no response_class= kwarg
        async def _serve(rel_path: str) -> FileResponse:  # type: ignore[name-defined]
            # The body never runs in the test; only the openapi walk matters.
            return FileResponse(rel_path)  # pragma: no cover

    _register(router)
    return router


def test_plugin_router_with_broken_annotation_does_not_break_openapi():
    """The host /openapi.json must return 200 even when a plugin router
    declared with the SDK 0.6.x broken pattern is mounted via the same
    deferred-mount loop real plugins use.
    """
    app = create_app(agent=None)

    # Simulate ``api/server.py`` deferred-mount loop in isolation:
    # mount the broken router under /api/plugins/{plugin_id} with the
    # same include_in_schema=False guard the fix established.
    plugin_id = "broken-fixture-plugin"
    app.include_router(
        _build_broken_plugin_router(),
        prefix=f"/api/plugins/{plugin_id}",
        include_in_schema=False,
    )

    client = TestClient(app, raise_server_exceptions=False)

    # (1) /openapi.json must NOT 500.
    resp = client.get("/openapi.json")
    assert resp.status_code == 200, (
        f"/openapi.json returned {resp.status_code}; "
        f"plugin-induced regression. body[:300]={resp.text[:300]!r}"
    )

    # (2) The broken endpoint path must NOT appear in schema (excluded).
    schema = resp.json()
    broken_path = f"/api/plugins/{plugin_id}/serve"
    assert broken_path not in schema["paths"], (
        f"Plugin-side endpoint {broken_path!r} leaked into /openapi.json "
        f"schema; the include_in_schema=False guard was removed."
    )

    # (3) The endpoint is still REACHABLE -- mount succeeded, only the
    # schema documentation is suppressed.
    runtime_resp = client.get(broken_path, params={"rel_path": "x"})
    # 404 is fine (file does not exist); 500 would indicate the route
    # itself failed to mount.
    assert runtime_resp.status_code != 500, (
        f"Plugin endpoint mount failed: {broken_path} -> 500 ({runtime_resp.text[:200]!r})"
    )
