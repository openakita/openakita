"""
日志配置和初始化

功能:
- 配置根日志记录器
- 设置文件处理器（按天轮转 + 按大小轮转）
- 设置错误日志处理器（只记录 ERROR/CRITICAL）
- 设置控制台处理器
- 设置会话日志处理器（供 AI 查询）
- 启动时首行打印版本/git/前端构建指纹 banner，便于区分打包物与本地源码
"""

import hashlib
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .handlers import ColoredConsoleHandler, ErrorOnlyHandler, SessionLogHandler


def _compute_frontend_fingerprint() -> str:
    """尝试计算前端构建指纹。

    优先从 Vite 打出的 index.html 里抽取资产哈希（形如 index-abc1234.js），
    否则回退到 index.html 文件内容的 sha256 短哈希；都不可用时返回 "unknown"。
    """
    try:
        candidates = [
            Path(__file__).parent.parent / "web" / "index.html",
            Path(__file__).parent.parent.parent.parent
            / "apps"
            / "setup-center"
            / "dist-web"
            / "index.html",
        ]
        index_html = next((p for p in candidates if p.exists()), None)
        if not index_html:
            return "unknown"
        content = index_html.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"assets/[^\"']*?-([a-zA-Z0-9_]{6,})\.(?:js|mjs|css)", content)
        if m:
            return m.group(1)[:10]
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:10]
    except Exception:
        return "unknown"


def log_startup_banner(logger: logging.Logger) -> None:
    """在日志首行打印高辨识度的启动 banner，方便一眼识别构建来源。"""
    try:
        from openakita import __git_hash__, __version__

        frontend_fp = _compute_frontend_fingerprint()
        banner = (
            f"========== OpenAkita starting ========== "
            f"version={__version__} "
            f"git={__git_hash__} "
            f"frontend={frontend_fp} "
            f"python={sys.version.split()[0]} "
            f"platform={sys.platform}"
        )
        logger.info(banner)
    except Exception as e:
        logger.info(f"OpenAkita starting (version banner failed: {e})")


def setup_logging(
    log_dir: Path | None = None,
    log_level: str = "INFO",
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    log_file_prefix: str = "openakita",
    log_max_size_mb: int = 10,
    log_backup_count: int = 30,
    log_to_console: bool = True,
    log_to_file: bool = True,
) -> logging.Logger:
    """
    配置日志系统

    Args:
        log_dir: 日志目录
        log_level: 日志级别
        log_format: 日志格式
        log_file_prefix: 日志文件前缀
        log_max_size_mb: 单个日志文件最大大小（MB）
        log_backup_count: 保留的日志文件数量
        log_to_console: 是否输出到控制台
        log_to_file: 是否输出到文件

    Returns:
        根日志记录器
    """
    # 获取根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # 清除现有处理器
    root_logger.handlers.clear()

    # 创建格式化器
    formatter = logging.Formatter(log_format)

    # 控制台处理器
    if log_to_console:
        console_handler = ColoredConsoleHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # 文件处理器
    if log_to_file and log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # 主日志文件（按大小轮转，每个文件最大 log_max_size_mb MB）
        main_log_file = log_dir / f"{log_file_prefix}.log"
        main_handler = RotatingFileHandler(
            main_log_file,
            maxBytes=log_max_size_mb * 1024 * 1024,
            backupCount=log_backup_count,
            encoding="utf-8",
        )
        main_handler.setLevel(logging.DEBUG)
        main_handler.setFormatter(formatter)
        root_logger.addHandler(main_handler)

        # 错误日志文件（只记录 ERROR/CRITICAL，按天轮转）
        error_log_file = log_dir / "error.log"
        error_handler = ErrorOnlyHandler(
            error_log_file,
            when="midnight",
            interval=1,
            backupCount=log_backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        root_logger.addHandler(error_handler)

    # 会话日志处理器（供 AI 查询当前会话日志）
    session_handler = SessionLogHandler(logging.DEBUG)
    # 会话日志使用简化格式，只保留消息内容
    session_formatter = logging.Formatter("%(message)s")
    session_handler.setFormatter(session_formatter)
    root_logger.addHandler(session_handler)

    # 减少第三方库的日志输出
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    log_startup_banner(root_logger)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器

    Args:
        name: 日志记录器名称

    Returns:
        日志记录器
    """
    return logging.getLogger(name)
