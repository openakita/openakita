"""FastAPI 应用主文件"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import logging
import os

from .database import init_db
from .routes import auth, workflows

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    logger.info("Starting MVP Backend Service...")
    
    # 初始化数据库
    if os.getenv("INIT_DB_ON_START", "false").lower() == "true":
        logger.info("Initializing database...")
        init_db()
        logger.info("Database initialized")
    
    logger.info("MVP Backend Service started")
    
    yield
    
    # 关闭时
    logger.info("Shutting down MVP Backend Service...")


# 创建 FastAPI 应用
app = FastAPI(
    title="Project Phoenix MVP API",
    description="执行型 Agent - 企业工作流自动化平台",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# 注册路由
app.include_router(auth.router, prefix="/api")
app.include_router(workflows.router, prefix="/api")


# 健康检查
@app.get("/health", tags=["健康检查"])
async def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0"
    }


# 根路径
@app.get("/", tags=["根"])
async def root():
    return {
        "message": "Welcome to Project Phoenix MVP API",
        "docs": "/docs",
        "health": "/health"
    }
