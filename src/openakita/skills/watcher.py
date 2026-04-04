"""
技能文件热更新监视器

监视技能目录的文件变更，触发自动重新加载。
使用 watchdog 库（可选依赖），不可用时热更新功能静默禁用。

特性:
- 500ms 防抖：合并短时间内的多次变更
- Graceful 降级：watchdog 不可用时静默禁用（不阻塞启动）
- 资源清理：stop() 方法释放所有资源
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.5


class SkillWatcher:
    """Watch skill directories for changes and trigger reload callbacks."""

    def __init__(
        self,
        directories: list[Path],
        on_change: Callable[[], None],
    ) -> None:
        self._directories = [d for d in directories if d.exists()]
        self._on_change = on_change
        self._observer = None
        self._running = False
        self._debounce_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start watching directories. Uses watchdog if available, else no-op."""
        if self._running or not self._directories:
            return

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class _Handler(FileSystemEventHandler):
                def __init__(self, watcher: SkillWatcher):
                    self._watcher = watcher

                def on_any_event(self, event):
                    if event.src_path and event.src_path.lower().endswith(
                        (".md", ".py", ".yaml", ".json", ".yml")
                    ):
                        self._watcher._schedule_reload()

            observer = Observer()
            handler = _Handler(self)
            for d in self._directories:
                observer.schedule(handler, str(d), recursive=True)
            observer.daemon = True
            observer.start()
            self._observer = observer
            self._running = True
            logger.info(
                "SkillWatcher started (watchdog) for %d directories",
                len(self._directories),
            )
        except ImportError:
            logger.debug(
                "watchdog not installed — skill hot-reload disabled. "
                "Install with: pip install watchdog"
            )
        except Exception as e:
            logger.warning("Failed to start SkillWatcher: %s", e)

    def stop(self) -> None:
        """Stop watching and release resources."""
        self._running = False
        with self._lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=3)
            except Exception as e:
                logger.debug("SkillWatcher observer stop error: %s", e)
            self._observer = None
        logger.debug("SkillWatcher stopped")

    def _schedule_reload(self) -> None:
        """Schedule a debounced reload callback."""
        with self._lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                DEBOUNCE_SECONDS, self._fire_reload,
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _fire_reload(self) -> None:
        """Execute the reload callback."""
        if not self._running:
            return
        try:
            logger.info("Skill files changed — triggering reload")
            self._on_change()
        except Exception as e:
            logger.warning("Skill reload callback failed: %s", e)

    @property
    def is_running(self) -> bool:
        return self._running


def clear_all_skill_caches() -> None:
    """Clear all skill-related caches across the system.

    Unified cache invalidation entry point called by:
    - Hot-reload watcher callback
    - Manual reload operations
    - Skill install/uninstall flows
    """
    logger.debug("Clearing all skill caches")

    # F13: Clear loader internal caches
    try:
        from .loader import SkillLoader
        if hasattr(SkillLoader, "_load_cache"):
            SkillLoader._load_cache = {}
    except Exception:
        pass

    # F13: Clear parser memoization cache
    try:
        from .parser import SkillParser
        if hasattr(SkillParser, "_parse_cache"):
            SkillParser._parse_cache.clear()
    except Exception:
        pass
