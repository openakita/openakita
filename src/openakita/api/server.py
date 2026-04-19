"""
FastAPI HTTP API server for OpenAkita.

Integrated into `openakita serve`, providing:
- Chat (SSE streaming)
- Models list
- Health check
- Skills management
- File upload

Default port: 18900
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import openakita._ensure_utf8  # noqa: F401  # Windows UTF-8 encoding safeguard

from .auth import WebAccessConfig, create_auth_middleware
from .routes import (
    agents,
    bug_report,
    chat,
    chat_models,
    config,
    feishu_onboard,
    files,
    health,
    hub,
    identity,
    im,
    logs,
    mcp,
    memory,
    orgs,
    qqbot_onboard,
    scheduler,
    sessions,
    skills,
    token_stats,
    upload,
    wechat_onboard,
    wecom_onboard,
    workspace_io,
)

try:
    from .routes import plugins as plugins_routes
except ImportError:
    plugins_routes = None
    logging.getLogger(__name__).debug("Plugin routes not available")
from .routes import (
    auth as auth_routes,
)
from .routes import (
    websocket as ws_routes,
)

logger = logging.getLogger(__name__)

API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", "18900"))


def is_port_free(host: str, port: int) -> bool:
    """Check whether a port is available (fast single check)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def wait_for_port_free(host: str, port: int, timeout: float = 30.0) -> bool:
    """Wait for a port to become available. Returns True if the port is free.

    Used in restart scenarios to wait for the old process to release the TCP port
    (avoids TIME_WAIT race conditions).
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if is_port_free(host, port):
            return True
        time.sleep(0.5)
    return False


def _find_web_dist() -> Path | None:
    """Locate the web frontend dist directory.

    Search order:
    1. openakita/web/ (pip wheel install & PyInstaller bundle)
    2. apps/setup-center/dist-web/ (development)
    """
    # Inside the installed package
    pkg_web = Path(__file__).parent.parent / "web"
    if (pkg_web / "index.html").exists():
        return pkg_web

    # Development: relative to project root
    dev_web = Path(__file__).parent.parent.parent.parent / "apps" / "setup-center" / "dist-web"
    if (dev_web / "index.html").exists():
        return dev_web

    return None


def _find_docs_dist() -> Path | None:
    """Locate bundled user docs dist directory.

    Search order:
    1. openakita/docs_dist/ (pip wheel install)
    2. docs-site/.vitepress/dist/ (development)
    """
    pkg_docs = Path(__file__).parent.parent / "docs_dist"
    if (pkg_docs / "index.html").exists():
        return pkg_docs

    dev_docs = Path(__file__).parent.parent.parent.parent / "docs-site" / ".vitepress" / "dist"
    if (dev_docs / "index.html").exists():
        return dev_docs

    return None


def _deploy_docs(data_dir: Path, app_version: str) -> Path | None:
    """Deploy bundled docs to data/docs/v{version}/ if not already present.

    Historical versions are never deleted so users can switch between them.
    """
    import json
    import shutil

    bundled = _find_docs_dist()
    if not bundled:
        return None

    docs_root = data_dir / "docs"
    version_clean = app_version.split("+")[0]
    version_dir = docs_root / f"v{version_clean}"

    if not (version_dir / "index.html").exists():
        version_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundled, version_dir, dirs_exist_ok=True)
        logger.info(f"Deployed user docs v{version_clean} → {version_dir}")

    versions_file = docs_root / "versions.json"
    try:
        versions = json.loads(versions_file.read_text("utf-8")) if versions_file.exists() else []
    except Exception:
        versions = []
    if version_clean not in versions:
        versions.insert(0, version_clean)
        versions_file.write_text(json.dumps(versions, indent=2), encoding="utf-8")

    return docs_root


def _mount_web_frontend(app: FastAPI) -> None:
    """Mount the web frontend static files if available.

    Uses StaticFiles for /web/* with html=True for SPA fallback (index.html).
    """
    import mimetypes

    from fastapi.staticfiles import StaticFiles

    # On some Windows systems the registry maps .js to text/plain, causing
    # browsers to reject ES module scripts.  Ensure correct MIME types are
    # registered before StaticFiles serves any content.
    _mime_overrides = {
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".wasm": "application/wasm",
        ".svg": "image/svg+xml",
    }
    for ext, mime in _mime_overrides.items():
        mimetypes.add_type(mime, ext)

    web_dist = _find_web_dist()
    if not web_dist:
        logger.debug("Web frontend not found, skipping static file mount")
        return

    logger.info(f"Mounting web frontend from {web_dist}")
    app.mount("/web", StaticFiles(directory=str(web_dist), html=True), name="web-frontend")


def create_app(
    agent: Any = None,
    shutdown_event: asyncio.Event | None = None,
    session_manager: Any = None,
    gateway: Any = None,
    orchestrator: Any = None,
    agent_pool: Any = None,
) -> FastAPI:
    """Create the FastAPI application with all routes mounted."""

    from openakita import get_version_string

    tags_metadata = [
        {"name": "Authentication", "description": "Login, logout, token refresh"},
        {"name": "Chat", "description": "Chat interactions, message control"},
        {"name": "Agents", "description": "Agent profiles, bot management, collaboration topology"},
        {"name": "Models", "description": "Available models / endpoint list"},
        {"name": "Config", "description": "Workspace config, environment variables, endpoint management"},
        {"name": "Skills", "description": "Skill marketplace, installation, configuration"},
        {"name": "MCP", "description": "MCP server connections and tool management"},
        {"name": "Memory", "description": "Long-term memory CRUD and vector search"},
        {"name": "Sessions", "description": "Session history management"},
        {"name": "Files", "description": "File browsing and upload"},
        {"name": "Identity", "description": "AI identity definition file management"},
        {"name": "Scheduled Tasks", "description": "Scheduled task dispatch"},
        {"name": "Instant Messaging", "description": "IM channels and messages"},
        {"name": "Hub", "description": "Agent/Skill import/export and marketplace"},
        {"name": "Workspace", "description": "Backup, import/export"},
        {"name": "Health Check", "description": "Service health, diagnostics, debugging"},
        {"name": "Statistics", "description": "Token usage statistics"},
        {"name": "Logs", "description": "Service log queries"},
        {"name": "Feedback", "description": "Bug reports and feature suggestions"},
        {"name": "WebSocket", "description": "Real-time event push"},
        {"name": "System", "description": "Root path, shutdown, and other system operations"},
    ]

    app = FastAPI(
        title="OpenAkita API",
        description=(
            "OpenAkita Agent Platform HTTP API\n\n"
            "Provides chat, agent management, skill configuration, MCP tools, "
            "scheduled tasks, and more.\n\n"
            "- Swagger UI: `/docs`\n"
            "- ReDoc: `/redoc`"
        ),
        version=get_version_string(),
        openapi_tags=tags_metadata,
    )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(request, exc: RequestValidationError):
        """Return Pydantic validation errors as a flat string detail
        so the frontend never receives raw error objects."""
        msgs = []
        for err in exc.errors():
            loc = " → ".join(str(part) for part in err.get("loc", []))
            msg = err.get("msg", "validation error")
            msgs.append(f"{loc}: {msg}" if loc else msg)
        return JSONResponse(
            status_code=422,
            content={"detail": "; ".join(msgs) if msgs else "Validation error"},
        )

    # Web access authentication — registered BEFORE CORS so that in Starlette's
    # middleware stack (last-added = outermost) CORS wraps auth, ensuring all
    # responses (including 401) carry proper CORS headers.
    try:
        from openakita.config import settings

        data_dir = Path(settings.project_root) / "data"
    except Exception:
        data_dir = Path.cwd() / "data"
    web_access_config = WebAccessConfig(data_dir)
    app.state.web_access_config = web_access_config

    auth_mw = create_auth_middleware(web_access_config)
    app.middleware("http")(auth_mw)

    # CORS configuration (outermost middleware — added last)
    # NOTE: allow_origins=["*"] is incompatible with allow_credentials=True per
    # the browser spec.  When no explicit origins are configured we fall back to
    # allow_origin_regex which matches any origin, achieving the same permissive
    # behaviour while satisfying the spec.
    cors_origins = os.environ.get("CORS_ORIGINS", "").strip()
    cors_kwargs: dict[str, Any] = {
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if cors_origins:
        origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
        # Always include Capacitor mobile origins so mobile apps work
        # regardless of what the user configured in CORS_ORIGINS.
        for cap_origin in ("http://localhost", "capacitor://localhost"):
            if cap_origin not in origins:
                origins.append(cap_origin)
        cors_kwargs["allow_origins"] = origins
    else:
        cors_kwargs["allow_origin_regex"] = r".*"
    app.add_middleware(CORSMiddleware, **cors_kwargs)

    # Store references in app state
    app.state.agent = agent
    app.state.shutdown_event = shutdown_event
    app.state.session_manager = session_manager
    app.state.gateway = gateway
    app.state.orchestrator = orchestrator
    app.state.agent_pool = agent_pool

    if agent is not None:
        pm = getattr(agent, "_plugin_manager", None)
        if pm is not None:
            pm._host_refs["api_app"] = app
            pending = pm._host_refs.pop("_pending_plugin_routers", [])
            for plugin_id, router in pending:
                try:
                    app.include_router(router, prefix=f"/api/plugins/{plugin_id}")
                    logger.info("Mounted pending plugin routes for '%s'", plugin_id)
                except Exception as e:
                    logger.warning(
                        "Failed to mount pending routes for plugin '%s': %s", plugin_id, e
                    )

            pending_ui = pm._host_refs.pop("_pending_plugin_ui_mounts", [])
            for plugin_id, ui_dist_dir in pending_ui:
                try:
                    pm._do_mount_plugin_ui(app, plugin_id, ui_dist_dir)
                except Exception as e:
                    logger.warning("Failed to mount pending UI for plugin '%s': %s", plugin_id, e)

    # Initialize OrgManager & OrgRuntime
    from openakita.orgs.manager import OrgManager
    from openakita.orgs.runtime import OrgRuntime
    from openakita.orgs.templates import ensure_builtin_templates

    org_manager = OrgManager(data_dir)
    ensure_builtin_templates(data_dir / "org_templates")
    app.state.org_manager = org_manager
    org_runtime = OrgRuntime(org_manager)
    app.state.org_runtime = org_runtime

    # Mount routes
    app.include_router(auth_routes.router, tags=["Authentication"])
    app.include_router(agents.router, tags=["Agents"])
    app.include_router(bug_report.router, tags=["Feedback"])
    app.include_router(chat.router, tags=["Chat"])
    app.include_router(chat_models.router, tags=["Models"])
    app.include_router(config.router, tags=["Config"])
    app.include_router(feishu_onboard.router, tags=["Feishu OAuth"])
    app.include_router(qqbot_onboard.router, tags=["QQ OAuth"])
    app.include_router(wechat_onboard.router, tags=["WeChat OAuth"])
    app.include_router(wecom_onboard.router, tags=["WeCom OAuth"])
    app.include_router(files.router, tags=["Files"])
    app.include_router(health.router, tags=["Health Check"])
    app.include_router(im.router, tags=["Instant Messaging"])
    app.include_router(logs.router, tags=["Logs"])
    app.include_router(mcp.router, tags=["MCP"])
    app.include_router(memory.router, tags=["Memory"])
    app.include_router(scheduler.router, tags=["Scheduled Tasks"])
    app.include_router(sessions.router, tags=["Sessions"])
    app.include_router(skills.router, tags=["Skills"])
    app.include_router(token_stats.router, tags=["Statistics"])
    app.include_router(upload.router, tags=["Files"])
    app.include_router(workspace_io.router, tags=["Workspace"])
    app.include_router(ws_routes.router, tags=["WebSocket"])
    app.include_router(hub.router, tags=["Hub"])
    app.include_router(identity.router, tags=["Identity"])
    app.include_router(orgs.router, tags=["Org Orchestration"])
    app.include_router(orgs.inbox_router, tags=["Org Inbox"])
    if plugins_routes is not None:
        app.include_router(plugins_routes.router)

    @app.get("/", tags=["System"])
    async def root():
        # If web frontend is available, redirect to it
        web_dist = _find_web_dist()
        if web_dist:
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url="/web/")
        return {
            "service": "openakita",
            "api_version": "1.0.0",
            "status": "running",
        }

    # ── Serve uploaded avatar files ──
    from fastapi.staticfiles import StaticFiles as _StaticFiles

    from openakita.config import settings as _settings

    _avatar_dir = _settings.data_dir / "avatars"
    _avatar_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/api/avatars", _StaticFiles(directory=str(_avatar_dir)), name="avatars")

    # ── Serve plugin SDK shared web assets (bootstrap.js, etc.) ──
    # Bundled inside the openakita_plugin_sdk wheel under web/.
    # Plugin UIs reference: <script src="/api/plugins/_sdk/bootstrap.js"></script>
    # Resolution order:
    #   1) installed openakita_plugin_sdk package (production)
    #   2) repo-relative openakita-plugin-sdk/src/openakita_plugin_sdk/web (dev, monorepo)
    _sdk_web_dir: Path | None = None
    try:
        from importlib.resources import files as _res_files

        _candidate = _res_files("openakita_plugin_sdk").joinpath("web")
        if _candidate.is_dir():
            _sdk_web_dir = Path(str(_candidate))
    except (ImportError, ModuleNotFoundError):
        pass
    except Exception as _sdk_err:
        logger.warning("Failed to locate plugin SDK assets via importlib: %s", _sdk_err)

    if _sdk_web_dir is None:
        _here = Path(__file__).resolve()
        for _parent in _here.parents:
            _candidate_dir = (
                _parent / "openakita-plugin-sdk" / "src" / "openakita_plugin_sdk" / "web"
            )
            if _candidate_dir.is_dir():
                _sdk_web_dir = _candidate_dir
                break

    if _sdk_web_dir is not None:
        try:
            app.mount(
                "/api/plugins/_sdk",
                _StaticFiles(directory=str(_sdk_web_dir)),
                name="plugin-sdk-assets",
            )
            logger.info("Mounted plugin SDK assets from %s", _sdk_web_dir)
        except Exception as _sdk_err:
            logger.warning("Failed to mount plugin SDK assets: %s", _sdk_err)
    else:
        logger.warning(
            "Plugin SDK web/ directory not found; bootstrap.js disabled. "
            "Install openakita-plugin-sdk or run from a monorepo checkout."
        )

    # ── Serve versioned user docs ──
    from openakita import get_version_string as _get_ver

    _docs_ver = _get_ver().split("+")[0]
    _docs_root = _deploy_docs(data_dir, _docs_ver)
    if _docs_root:
        from fastapi.responses import RedirectResponse as _Redirect
        from fastapi.staticfiles import StaticFiles as _DocsStatic

        @app.get("/user-docs", include_in_schema=False)
        @app.get("/user-docs/", include_in_schema=False)
        async def _docs_redirect():
            return _Redirect(f"/user-docs/v{_docs_ver}/")

        app.mount(
            "/user-docs",
            _DocsStatic(directory=str(_docs_root), html=True),
            name="user-docs",
        )
        logger.info(f"Mounted user docs at /user-docs/ from {_docs_root}")

    # ── Serve web frontend static files ──
    _mount_web_frontend(app)

    @app.post("/api/shutdown", tags=["System"])
    async def shutdown(request: Request):
        """Gracefully shut down the OpenAkita service process.

        Only allowed from localhost for security.
        Uses the shared shutdown_event to trigger the same graceful cleanup
        path as SIGINT/SIGTERM (sessions saved, IM adapters stopped, etc.).
        """
        from .auth import get_client_ip

        trust_proxy = os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes")
        real_ip = get_client_ip(request, trust_proxy=trust_proxy)
        is_local = real_ip in ("127.0.0.1", "::1", "localhost") or (
            real_ip.startswith("::ffff:") and real_ip[7:] == "127.0.0.1"
        )
        if not is_local:
            return JSONResponse(
                status_code=403,
                content={"detail": "Shutdown only allowed from localhost"},
            )
        logger.info("Shutdown requested via API")
        if app.state.shutdown_event is not None:
            app.state.shutdown_event.set()
            return {"status": "shutting_down"}
        logger.warning("No shutdown_event available, shutdown request ignored")
        return {"status": "error", "message": "shutdown not available in this mode"}

    @app.on_event("startup")
    async def _startup_org_runtime():
        loop = asyncio.get_running_loop()
        loop.slow_callback_duration = 0.5
        if hasattr(app.state, "org_runtime") and app.state.org_runtime:
            try:
                from openakita.core.engine_bridge import to_engine

                await to_engine(app.state.org_runtime.start())
            except Exception as e:
                logger.warning(f"OrgRuntime startup error (non-fatal): {e}")

        # Endpoint health check: detect stale/broken endpoints early
        try:
            _agent = getattr(app.state, "agent", None)
            _brain = getattr(_agent, "brain", None) if _agent else None
            _llm_client = getattr(_brain, "_llm_client", None) if _brain else None
            if _llm_client and hasattr(_llm_client, "startup_health_check"):
                _results = await _llm_client.startup_health_check()
                _ok = sum(1 for v in _results.values() if v == "ok")
                _fail = len(_results) - _ok
                if _fail:
                    logger.warning(
                        f"[Startup] Endpoint health check: {_ok} ok, {_fail} failed — "
                        f"{', '.join(f'{k}={v}' for k, v in _results.items() if v != 'ok')}"
                    )
                else:
                    logger.info(f"[Startup] All {_ok} endpoints healthy")
        except Exception as e:
            logger.debug(f"[Startup] Endpoint health check skipped: {e}")

        # Compiler endpoint health check
        try:
            _agent = getattr(app.state, "agent", None)
            _brain = getattr(_agent, "brain", None) if _agent else None
            _compiler_client = getattr(_brain, "_compiler_client", None) if _brain else None
            if _compiler_client and hasattr(_compiler_client, "startup_health_check"):
                comp_result = await _compiler_client.startup_health_check()
                comp_failed = {k: v for k, v in comp_result.items() if v != "ok"}
                if comp_failed:
                    for ep_name, status in comp_failed.items():
                        _brain._compiler_on_failure(f"startup: {ep_name}={status}")
                    logger.warning(
                        f"[Startup] Compiler health check failed: "
                        f"{', '.join(f'{k}={v}' for k, v in comp_failed.items())}. "
                        f"Compiler tasks will use main model."
                    )
                else:
                    logger.info(
                        f"[Startup] Compiler endpoints all healthy: {list(comp_result.keys())}"
                    )
        except Exception as e:
            logger.debug(f"[Startup] Compiler health check skipped: {e}")

    @app.on_event("shutdown")
    async def _shutdown_org_runtime():
        if hasattr(app.state, "org_runtime") and app.state.org_runtime:
            try:
                from openakita.core.engine_bridge import to_engine

                await to_engine(app.state.org_runtime.shutdown())
            except Exception as e:
                logger.warning(f"OrgRuntime shutdown error: {e}")

    return app


async def start_api_server(
    agent: Any = None,
    shutdown_event: asyncio.Event | None = None,
    session_manager: Any = None,
    gateway: Any = None,
    orchestrator: Any = None,
    agent_pool: Any = None,
    host: str = API_HOST,
    port: int = API_PORT,
    max_retries: int = 5,
) -> asyncio.Task:
    """
    Start the HTTP API server in a **dedicated background thread** with its
    own asyncio event loop ("API loop").

    The calling loop becomes the "engine loop" — it keeps running Agent,
    OrgRuntime, Scheduler, Gateway and all other heavy async work.  The API
    loop only handles HTTP request/response and WebSocket I/O, so it stays
    responsive even when the engine is saturated with LLM calls.

    Returns a proxy ``asyncio.Task`` in the engine loop.  Cancelling this
    task triggers a graceful uvicorn shutdown.

    Raises RuntimeError if the server cannot start after all retries.
    """
    import threading

    import uvicorn

    # Port pre-check: if the port is not available, wait for it to be released (handles TIME_WAIT etc.)
    if not is_port_free(host, port):
        logger.warning(f"Port {port} is currently in use, waiting for it to be released...")
        freed = await asyncio.to_thread(wait_for_port_free, host, port, 30.0)
        if not freed:
            raise RuntimeError(
                f"Port {port} is still in use after waiting 30s. "
                f"Another process may be occupying it."
            )
        logger.info(f"Port {port} is now available")

    engine_loop = asyncio.get_running_loop()

    app = create_app(
        agent=agent,
        shutdown_event=shutdown_event,
        session_manager=session_manager,
        gateway=gateway,
        orchestrator=orchestrator,
        agent_pool=agent_pool,
    )
    app.state.engine_loop = engine_loop

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        http="h11",
        log_config=None,
    )
    server = uvicorn.Server(config)

    # ── Launch uvicorn in a background thread ────────────────────────
    api_loop_holder: list[asyncio.AbstractEventLoop] = []
    thread_ready = threading.Event()
    thread_error: list[Exception] = []

    def _api_thread() -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            api_loop_holder.append(loop)
            thread_ready.set()
            loop.run_until_complete(server.serve())
        except Exception as exc:
            thread_error.append(exc)
        finally:
            thread_ready.set()
            try:
                loop = api_loop_holder[0] if api_loop_holder else None
                if loop and not loop.is_closed():
                    loop.close()
            except Exception:
                pass

    api_thread = threading.Thread(
        target=_api_thread,
        daemon=True,
        name="openakita-api",
    )
    api_thread.start()

    await asyncio.to_thread(thread_ready.wait)

    if thread_error:
        raise RuntimeError(f"API thread failed to start: {thread_error[0]}")

    api_loop = api_loop_holder[0] if api_loop_holder else None

    # ── Register loops for the cross-loop bridge ─────────────────────
    from openakita.core.engine_bridge import set_api_loop, set_engine_loop

    set_engine_loop(engine_loop)
    if api_loop is not None:
        set_api_loop(api_loop)

    from openakita import get_version_string

    logger.info(
        f"HTTP API server starting on http://{host}:{port} "
        f"(version: {get_version_string()}, dual-loop: {api_loop is not None})"
    )

    # ── Verify server is listening ───────────────────────────────────
    for attempt in range(max_retries):
        await asyncio.sleep(1.5)

        if not api_thread.is_alive():
            err = thread_error[0] if thread_error else RuntimeError("API thread died")
            raise RuntimeError(f"HTTP API server failed to start: {err}")

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect((host, port))
                logger.info(
                    f"HTTP API server confirmed listening on http://{host}:{port} "
                    f"(thread={api_thread.name})"
                )
                break
        except (ConnectionRefusedError, OSError, TimeoutError):
            if attempt < max_retries - 1:
                logger.debug(f"Server not yet listening (attempt {attempt + 1}), waiting...")
                continue

    # ── Proxy task — cancelling it triggers graceful shutdown ─────────
    async def _proxy() -> None:
        try:
            while api_thread.is_alive():
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            logger.info("API proxy task cancelled, shutting down uvicorn...")
            server.should_exit = True
            await asyncio.to_thread(api_thread.join, 5.0)

    proxy_task = asyncio.create_task(_proxy())
    return proxy_task


def update_agent(app: FastAPI, agent: Any) -> None:
    """Update the agent reference in the running app (e.g. after initialization)."""
    app.state.agent = agent
