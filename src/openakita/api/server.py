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
import socket
import time
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import chat, chat_models, config, files, health, im, logs, skills, upload

logger = logging.getLogger(__name__)

API_HOST = "127.0.0.1"
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


def create_app(
    agent: Any = None,
    shutdown_event: asyncio.Event | None = None,
    session_manager: Any = None,
    gateway: Any = None,
) -> FastAPI:
    """Create the FastAPI application with all routes mounted."""

    app = FastAPI(
        title="OpenAkita API",
        description="OpenAkita HTTP API for Chat, Health, Skills",
        version="1.0.0",
    )

    # CORS: 允许 Setup Center (localhost) 访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Setup Center 从 Tauri webview 请求
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store references in app state
    app.state.agent = agent
    app.state.shutdown_event = shutdown_event
    app.state.session_manager = session_manager
    app.state.gateway = gateway

    # Mount routes
    app.include_router(chat.router)
    app.include_router(chat_models.router)
    app.include_router(config.router)
    app.include_router(files.router)
    app.include_router(health.router)
    app.include_router(im.router)
    app.include_router(logs.router)
    app.include_router(skills.router)
    app.include_router(upload.router)

    @app.get("/")
    async def root():
        return {
            "service": "openakita",
            "api_version": "1.0.0",
            "status": "running",
        }

    @app.post("/api/shutdown")
    async def shutdown():
        """Gracefully shut down the OpenAkita service process.

        Uses the shared shutdown_event to trigger the same graceful cleanup
        path as SIGINT/SIGTERM (sessions saved, IM adapters stopped, etc.).
        """
        logger.info("Shutdown requested via API")
        if app.state.shutdown_event is not None:
            app.state.shutdown_event.set()
            return {"status": "shutting_down"}
        # Fallback: no shutdown_event (e.g. running outside of `openakita serve`)
        logger.warning("No shutdown_event available, shutdown request ignored")
        return {"status": "error", "message": "shutdown not available in this mode"}

    return app


async def start_api_server(
    agent: Any = None,
    shutdown_event: asyncio.Event | None = None,
    session_manager: Any = None,
    gateway: Any = None,
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

    app = create_app(agent=agent, shutdown_event=shutdown_event, session_manager=session_manager, gateway=gateway)

    server_started = asyncio.Event()
    server_error: list[Exception] = []

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
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
    logger.info(f"HTTP API server starting on http://{host}:{port}")

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
