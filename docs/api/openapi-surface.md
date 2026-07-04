# OpenAPI Surface Policy

> Single source of truth for what does and does not appear in
> `GET /openapi.json` (and consequently `/docs` Swagger UI) on the
> OpenAkita backend.

## Scope

The host backend (`src/openakita/api/server.py`) mounts three classes
of routers:

| Class | Source                                          | In `/openapi.json`? |
|-------|-------------------------------------------------|---------------------|
| Host  | `src/openakita/api/routes/*.py` (e.g. `health`, `orgs_v2`, `chat`, `sessions`) | Yes |
| Plugin management (control plane) | `src/openakita/api/routes/plugins.py` (`/api/plugins/list`, `/api/plugins/install`, `/api/plugins/{plugin_id}/_admin/*`, `/api/plugins/hub/*`, ...) | Yes |
| Per-plugin endpoints (data plane) | `plugins/<id>/...` deferred-mounted via `PluginManager._pending_plugin_routers` under `/api/plugins/{plugin_id}/...` (excluding `_admin/*`) | **No** (`include_in_schema=False`) |

## Why per-plugin endpoints are excluded

Per-plugin endpoints are mounted with `include_in_schema=False` at
`api/server.py` (see the deferred-mount loop in `create_app`). Two
reasons:

1. **Host resilience.** A single third-party plugin that ships a
   broken return-type annotation (for example `-> FileResponse`
   without a companion `response_class=` kwarg, under
   `from __future__ import annotations`) is sufficient to make
   Pydantic 2.12+ raise `PydanticUserError` during schema generation
   for the *whole* host. `GET /openapi.json` (and therefore `/docs`)
   would then return 500 even though every host endpoint is healthy.
   See `tmp_p10/_f2_fix_plan.md` for the full root-cause analysis
   and `tmp_p10/_f2_repro.py` for a 17-line standalone reproducer.

2. **No stable public contract.** Per-plugin endpoints are not part
   of the stable, versioned OpenAkita REST contract. The frontend
   always reaches them via explicit `/api/plugins/{id}/...` URLs
   shipped in the plugin's own UI bundle, never via OpenAPI-derived
   SDK codegen.

## What this means for clients

| You need...                                          | Use...                                                                 |
|------------------------------------------------------|------------------------------------------------------------------------|
| To enumerate / introspect host endpoints             | `/openapi.json` or `/docs`                                             |
| To list installed plugins or manage them             | `/api/plugins/list` (in schema)                                        |
| To call a specific plugin's endpoints at runtime     | `/api/plugins/{plugin_id}/<plugin-defined-path>` (NOT in schema; reach the URL directly) |

`/api/plugins/{plugin_id}` endpoints are still **fully reachable at
runtime** -- only their schema documentation is suppressed.

## Plugin author guidance

When you ship a router for your plugin and you return a starlette
`Response` subclass (`FileResponse`, `HTMLResponse`,
`StreamingResponse`, ...), prefer:

```python
@router.get("/serve/{rel_path}", response_class=FileResponse)
async def _serve(rel_path: str):
    return FileResponse(...)
```

over:

```python
@router.get("/serve/{rel_path}")
async def _serve(rel_path: str) -> FileResponse:  # avoid
    return FileResponse(...)
```

The `response_class=` form lets FastAPI bypass the type-hint walk
that, on Pydantic 2.12+ under PEP 563 (`from __future__ import
annotations`), breaks for response classes imported only inside
the enclosing register-factory closure.

Even though per-plugin endpoints are now excluded from
`/openapi.json` at the host level, the `response_class=` form is
still recommended -- it documents your intent, survives if the host
policy ever changes, and keeps `pytest`-level OpenAPI smoke tests
in your own plugin repo green.

## Regression sentinel

`tests/api/test_openapi_plugin_immunity.py` enforces the policy:

- A fake plugin router with the canonical Pydantic-2.12-breaking
  pattern is mounted via the host's deferred-mount path.
- `GET /openapi.json` must return 200.
- The fake plugin's path must NOT appear in `schema['paths']`.
- The fake plugin's endpoint must still be reachable at runtime
  (mount succeeded; only documentation is suppressed).

If a future change removes `include_in_schema=False` from the
deferred-mount loop, this sentinel fires immediately.

## References

- Root-cause analysis: `tmp_p10/_f2_fix_plan.md`
- Standalone reproducer: `tmp_p10/_f2_repro.py`
- Pydantic 2.12 break: `https://errors.pydantic.dev/2.12/u/class-not-fully-defined`
- Fix commit: `fix(api): exclude dynamic plugin routers from /openapi.json schema [smoke-F2 §B]`
- Sentinel commit: `test(api): regression sentinel for plugin-induced openapi 500 [smoke-F2]`
