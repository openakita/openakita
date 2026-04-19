"""
Configuration system hardening: file watching + hot reload

Inspired by Claude Code's configuration management:
- File-lock-protected writes (filelock)
- Only persist fields that differ from defaults
- File change detection and automatic reload
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL = 5.0  # seconds


class ConfigWatcher:
    """Configuration file change watcher.

    Polls the file's mtime for changes and triggers a callback when detected.
    Lighter than watchdog with no extra dependencies.
    """

    def __init__(
        self,
        path: str | Path,
        callback: Callable[[dict], None],
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._path = Path(path)
        self._callback = callback
        self._poll_interval = poll_interval
        self._last_mtime: float = 0
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start file watching (background thread)."""
        if self._running:
            return
        self._running = True
        self._last_mtime = self._get_mtime()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.debug("ConfigWatcher started for %s", self._path)

    def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=self._poll_interval + 1)
        logger.debug("ConfigWatcher stopped for %s", self._path)

    def _poll_loop(self) -> None:
        while self._running:
            time.sleep(self._poll_interval)
            if not self._running:
                break
            current_mtime = self._get_mtime()
            if current_mtime > self._last_mtime:
                self._last_mtime = current_mtime
                logger.info("Config file changed: %s", self._path)
                try:
                    data = self._load_file()
                    self._callback(data)
                except Exception as e:
                    logger.warning("ConfigWatcher callback error: %s", e)

    def _get_mtime(self) -> float:
        try:
            return self._path.stat().st_mtime
        except OSError:
            return 0

    def _load_file(self) -> dict:
        with open(self._path, encoding="utf-8") as f:
            return json.load(f)


def write_config_safe(
    path: str | Path,
    data: dict,
    *,
    defaults: dict | None = None,
) -> None:
    """Safely write a configuration file (file lock + diff-only write).

    Args:
        path: Configuration file path
        data: Configuration data to write
        defaults: Default values dict; only fields differing from defaults are persisted
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Only write non-default values
    if defaults:
        write_data = {}
        for key, value in data.items():
            if key not in defaults or defaults[key] != value:
                write_data[key] = value
    else:
        write_data = data

    # Atomic write with file lock
    try:
        from filelock import FileLock

        lock = FileLock(str(path) + ".lock", timeout=10)
        with lock:
            _atomic_write(path, write_data)
    except ImportError:
        _atomic_write(path, write_data)


def _atomic_write(path: Path, data: dict) -> None:
    """Atomic write: write to a temporary file first, then rename."""
    tmp_path = path.with_suffix(".tmp")
    content = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)
