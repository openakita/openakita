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
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import chat, chat_models, health, skills, upload

logger = logging.getLogger(__name__)

API_HOST = "127.0.0.1"
API_PORT = 18900


def create_app(agent: Any = None) -> FastAPI:
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

    # Store agent reference in app state
    app.state.agent = agent

    # Mount routes
    app.include_router(chat.router)
    app.include_router(chat_models.router)
    app.include_router(health.router)
    app.include_router(skills.router)
    app.include_router(upload.router)

    @app.get("/")
    async def root():
        return {
            "service": "openakita",
            "api_version": "1.0.0",
            "status": "running",
        }

    return app


async def start_api_server(agent: Any = None, host: str = API_HOST, port: int = API_PORT) -> asyncio.Task:
    """
    Start the HTTP API server as a background asyncio task.

    This is designed to be called from within the `openakita serve` event loop,
    so it shares the same event loop as the Agent and IM channels.

    Returns the server task for later cancellation.
    """
    import uvicorn

    app = create_app(agent=agent)

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)

    async def _run():
        try:
            await server.serve()
        except asyncio.CancelledError:
            logger.info("API server shutting down")
        except Exception as e:
            logger.error(f"API server error: {e}", exc_info=True)

    task = asyncio.create_task(_run())
    logger.info(f"HTTP API server starting on http://{host}:{port}")
    return task


def update_agent(app: FastAPI, agent: Any) -> None:
    """Update the agent reference in the running app (e.g. after initialization)."""
    app.state.agent = agent
