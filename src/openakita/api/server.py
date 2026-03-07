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
    auth as auth_routes,
    bug_report,
    chat,
    chat_models,
    config,
    files,
    health,
    hub,
    identity,
    im,
    logs,
    mcp,
    memory,
    scheduler,
    sessions,
    skills,
    token_stats,
    upload,
    websocket as ws_routes,
    workspace_io,
)

logger = logging.getLogger(__name__)

API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = 18900


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

    # Mount routes
    app.include_router(auth_routes.router, tags=["认证"])
    app.include_router(agents.router, tags=["智能体"])
    app.include_router(bug_report.router, tags=["反馈"])
    app.include_router(chat.router, tags=["对话"])
    app.include_router(chat_models.router, tags=["模型"])
    app.include_router(config.router, tags=["配置"])
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
    Start the HTTP API server as a background asyncio task.

    This is designed to be called from within the `openakita serve` event loop,
    so it shares the same event loop as the Agent and IM channels.

    启动前会检测端口可用性；如果端口被占用（如 TIME_WAIT），
    最多等待 30 秒端口释放。绑定失败时带退避重试。

    Returns the server task for later cancellation.
    Raises RuntimeError if the server cannot start after all retries.
    """
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

    app = create_app(agent=agent, shutdown_event=shutdown_event, session_manager=session_manager, gateway=gateway, orchestrator=orchestrator, agent_pool=agent_pool)

    server_started = asyncio.Event()
    server_error: list[Exception] = []

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
        http="h11",  # 避免外部环境中异常的 httptools 模块导致 API 连接不可用
        log_config=None,  # 关键：禁止 uvicorn 调用 dictConfig 覆盖根日志器
    )
    server = uvicorn.Server(config)

    async def _run():
        try:
            await server.serve()
        except asyncio.CancelledError:
            logger.info("API server shutting down")
        except Exception as e:
            server_error.append(e)
            logger.error(f"API server error: {e}", exc_info=True)
        finally:
            server_started.set()

    task = asyncio.create_task(_run())
    from openakita import get_version_string
    logger.info(f"HTTP API server starting on http://{host}:{port} (version: {get_version_string()})")

    # 短暂等待确认服务器是否成功开始监听
    # uvicorn 启动监听通常在 1-2 秒内完成
    for attempt in range(max_retries):
        await asyncio.sleep(1.5)
        if server_error:
            err = server_error[0]
            err_str = str(err)
            if "address already in use" in err_str.lower() or "10048" in err_str:
                if attempt < max_retries - 1:
                    backoff = (attempt + 1) * 2
                    logger.warning(
                        f"Port {port} bind failed (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {backoff}s..."
                    )
                    await asyncio.sleep(backoff)
                    server_error.clear()
                    server = uvicorn.Server(config)
                    task = asyncio.create_task(_run())
                    continue
            raise RuntimeError(f"HTTP API server failed to start: {err}")
        # 检查服务器是否已开始监听
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect((host, port))
                logger.info(f"HTTP API server confirmed listening on http://{host}:{port}")
                return task
        except (ConnectionRefusedError, OSError, TimeoutError):
            if attempt < max_retries - 1:
                logger.debug(f"Server not yet listening (attempt {attempt + 1}), waiting...")
                continue

    # 最终检查
    if server_error:
        raise RuntimeError(f"HTTP API server failed to start: {server_error[0]}")

    # 没有报错但也没有成功连接——可能是慢启动，返回 task 让调用者继续
    logger.warning("HTTP API server startup not confirmed, but no errors detected")
    return task


def update_agent(app: FastAPI, agent: Any) -> None:
    """Update the agent reference in the running app (e.g. after initialization)."""
    app.state.agent = agent
