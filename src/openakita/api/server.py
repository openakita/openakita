"""
FastAPI HTTP API server for OpenAkita.

集成在 `openakita serve` 中，提供：
- Chat (SSE streaming)
- Models list
- Health check
- Skills management
- File upload

默认端口：18900
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

from .auth import WebAccessConfig, create_auth_middleware
from .routes import (
    agents,
    bug_report,
    chat,
    chat_models,
    config,
    feishu_onboard,
    files,
    qqbot_onboard,
    wecom_onboard,
    health,
    hub,
    identity,
    im,
    logs,
    mcp,
    memory,
    orgs,
    scheduler,
    sessions,
    skills,
    token_stats,
    upload,
    workspace_io,
)
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
    """检测端口是否可用（快速单次检测）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def wait_for_port_free(host: str, port: int, timeout: float = 30.0) -> bool:
    """等待端口释放，返回 True 表示端口可用。

    用于重启场景下等待旧进程释放 TCP 端口（避免 TIME_WAIT 竞态）。
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

    dev_docs = (
        Path(__file__).parent.parent.parent.parent
        / "docs-site" / ".vitepress" / "dist"
    )
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
        {"name": "认证", "description": "登录、登出、Token 刷新"},
        {"name": "对话", "description": "聊天交互、消息控制"},
        {"name": "智能体", "description": "Agent 配置文件、Bot 管理、协作拓扑"},
        {"name": "模型", "description": "可用模型/端点列表"},
        {"name": "配置", "description": "工作区配置、环境变量、端点管理"},
        {"name": "技能", "description": "技能市场、安装、配置"},
        {"name": "MCP", "description": "MCP 服务器连接与工具管理"},
        {"name": "记忆", "description": "长期记忆 CRUD 与向量检索"},
        {"name": "会话", "description": "会话历史管理"},
        {"name": "文件", "description": "文件浏览与上传"},
        {"name": "身份", "description": "AI 身份定义文件管理"},
        {"name": "定时任务", "description": "计划任务调度"},
        {"name": "即时通讯", "description": "IM 渠道与消息"},
        {"name": "Hub", "description": "Agent/Skill 导入导出与市场"},
        {"name": "工作区", "description": "备份、导入导出"},
        {"name": "健康检查", "description": "服务健康、诊断、调试"},
        {"name": "统计", "description": "Token 用量统计"},
        {"name": "日志", "description": "服务日志查询"},
        {"name": "反馈", "description": "Bug 报告与功能建议"},
        {"name": "WebSocket", "description": "实时事件推送"},
        {"name": "系统", "description": "根路径、关机等系统操作"},
    ]

    app = FastAPI(
        title="OpenAkita API",
        description=(
            "OpenAkita 智能体平台 HTTP API\n\n"
            "提供对话、Agent 管理、技能配置、MCP 工具、定时任务等完整接口。\n\n"
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
            loc = " → ".join(str(l) for l in err.get("loc", []))
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
    cors_kwargs: dict[str, Any] = dict(
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
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
    app.include_router(auth_routes.router, tags=["认证"])
    app.include_router(agents.router, tags=["智能体"])
    app.include_router(bug_report.router, tags=["反馈"])
    app.include_router(chat.router, tags=["对话"])
    app.include_router(chat_models.router, tags=["模型"])
    app.include_router(config.router, tags=["配置"])
    app.include_router(feishu_onboard.router, tags=["飞书扫码"])
    app.include_router(qqbot_onboard.router, tags=["QQ扫码"])
    app.include_router(wecom_onboard.router, tags=["企微扫码"])
    app.include_router(files.router, tags=["文件"])
    app.include_router(health.router, tags=["健康检查"])
    app.include_router(im.router, tags=["即时通讯"])
    app.include_router(logs.router, tags=["日志"])
    app.include_router(mcp.router, tags=["MCP"])
    app.include_router(memory.router, tags=["记忆"])
    app.include_router(scheduler.router, tags=["定时任务"])
    app.include_router(sessions.router, tags=["会话"])
    app.include_router(skills.router, tags=["技能"])
    app.include_router(token_stats.router, tags=["统计"])
    app.include_router(upload.router, tags=["文件"])
    app.include_router(workspace_io.router, tags=["工作区"])
    app.include_router(ws_routes.router, tags=["WebSocket"])
    app.include_router(hub.router, tags=["Hub"])
    app.include_router(identity.router, tags=["身份"])
    app.include_router(orgs.router, tags=["组织编排"])
    app.include_router(orgs.inbox_router, tags=["组织消息中心"])

    @app.get("/", tags=["系统"])
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

    # ── Serve versioned user docs ──
    from openakita import get_version_string as _get_ver
    _docs_ver = _get_ver().split("+")[0]
    _docs_root = _deploy_docs(data_dir, _docs_ver)
    if _docs_root:
        from fastapi.staticfiles import StaticFiles as _DocsStatic
        from fastapi.responses import RedirectResponse as _Redirect

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

    @app.post("/api/shutdown", tags=["系统"])
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

    # 端口预检：如果端口不可用，先等待释放（处理 TIME_WAIT 等场景）
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
        target=_api_thread, daemon=True, name="openakita-api",
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
