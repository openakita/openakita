"""
Logging configuration and initialization

Features:
- Configure the root logger
- Set up file handler (daily rotation + size-based rotation)
- Set up error log handler (ERROR/CRITICAL only)
- Set up console handler
- Set up session log handler (for AI queries)
- Print a version/git/frontend build fingerprint banner on startup to distinguish packaged builds from local source
"""

import hashlib
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .handlers import ColoredConsoleHandler, ErrorOnlyHandler, SessionLogHandler


def _compute_frontend_fingerprint() -> str:
    """Try to compute a frontend build fingerprint.

    Prefers extracting the asset hash from the Vite-generated index.html (e.g. index-abc1234.js).
    Falls back to a short sha256 of the index.html file contents.
    Returns "unknown" if neither is available.
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
    """Print a high-visibility startup banner as the first log line to quickly identify the build source."""
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
    Configure the logging system.

    Args:
        log_dir: Log directory
        log_level: Log level
        log_format: Log format
        log_file_prefix: Log file prefix
        log_max_size_mb: Maximum size of a single log file (MB)
        log_backup_count: Number of log files to retain
        log_to_console: Whether to output to console
        log_to_file: Whether to output to file

    Returns:
        Root logger
    """
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(log_format)

    # Console handler
    if log_to_console:
        console_handler = ColoredConsoleHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # File handler
    if log_to_file and log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Main log file (size-based rotation, max log_max_size_mb MB per file)
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

        # Error log file (ERROR/CRITICAL only, daily rotation)
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

    # Session log handler (for AI queries of current session logs)
    session_handler = SessionLogHandler(logging.DEBUG)
    # Session log uses simplified format, keeping only the message content
    session_formatter = logging.Formatter("%(message)s")
    session_handler.setFormatter(session_formatter)
    root_logger.addHandler(session_handler)

    # Reduce third-party library log output
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    log_startup_banner(root_logger)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the given name.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
