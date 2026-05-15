"""C18 Phase A — POLICIES.yaml 文件热更新。

为什么用 mtime polling 而不是 watchdog / inotify
==================================================

参考 4 个邻近开源项目的实现（claude-code / hermes / QwenPaw / openclaw）：

- **claude-code** 用 chokidar（JS）+ ``awaitWriteFinish`` 等文件写稳定；
  Python 同等物是 ``watchdog`` —— 但它带 C 扩展，跨平台 wheel 大且和 PyPI
  上的版本碎片，对 Windows + macOS + Linux 矩阵不友好。
- **hermes** 在 cli 主循环里 5s ``stat().st_mtime`` 轮询 ``config.yaml``，
  解析失败 silent return。
- **QwenPaw** 用 ``asyncio.sleep + st_mtime`` 2s 轮询 agent.json。
- **openclaw** 用 chokidar + 200ms ``awaitWriteFinish`` + debounce + invalid-skip
  + ``promoteSnapshot`` 维护 LKG。

我们选 **守护线程 + mtime 轮询**（无第三方依赖）+ 配置化 ``debounce_seconds``
（默认 0.5s）模仿 awaitWriteFinish 的稳定窗口 + 已存在的 ``rebuild_engine_v2``
的 LKG 路径。优势：

1. **零新依赖**：复用现成 `os.stat`，跨平台一致。
2. **复用 LKG**：C16 已把 last-known-good 接到 ``rebuild_engine_v2``——
   校验失败会自动回滚到上一次 valid 配置，并把降级写进日志。这里只需要
   再额外写一条 audit 行就完成"reload 失败可追溯"。
3. **content-hash 去重**：``mtime`` 变化但内容一致（编辑器保存空 patch、
   git checkout 同 sha 的临时回写）不应触发 rebuild。

并发与生命周期
==============

- 单例 ``_reloader`` + 模块级锁；``api/server.py`` startup hook 启动，
  shutdown hook 停止。
- 守护线程（``daemon=True``）：进程退出时不阻塞。停止由 ``Event`` 通知，
  ``join(timeout=poll_interval+1)`` 兜底。
- ``rebuild_engine_v2`` 本身是线程安全的（内部 ``_lock``）；我们这里只
  调用它，不直接动 ``_engine`` / ``_config`` 单例。

in-flight tool call 怎么办
==========================

学 QwenPaw 的 "进程级单例 engine + 原地 reload" 策略：tool 调用拿的是
``get_engine_v2()`` 返回值，每次 entry 都重新查一次。``rebuild_engine_v2``
swap engine 指针后，下一次 entry 就拿到新 engine；正在跑的 12 步决策链
持有当前栈上的 engine 引用走完它的一轮，**不会**中途被切到新规则——这是
符合预期的（单次 tool call 内规则一致）。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# 状态：单例 + 锁，让 start/stop/status 可以从多个 startup hook 反复调用。
_RELOADER_LOCK = threading.Lock()
_reloader: PolicyHotReloader | None = None


class PolicyHotReloader:
    """Polls ``identity/POLICIES.yaml`` mtime + content hash, triggers
    :func:`rebuild_engine_v2` on real change.

    Use :func:`start_hot_reloader` / :func:`stop_hot_reloader` instead of
    constructing directly — they manage the module-level singleton + lock.
    """

    def __init__(
        self,
        path: Path,
        *,
        poll_interval_seconds: float = 5.0,
        debounce_seconds: float = 0.5,
        on_reload: Callable[[bool, str], None] | None = None,
    ) -> None:
        """Args:
            path: POLICIES.yaml absolute path.
            poll_interval_seconds: how often to ``stat`` the file.
            debounce_seconds: after seeing mtime change, wait this long
                before reading content (avoids reading a half-written file
                during editor "truncate then write" cycles).
            on_reload: optional ``(ok, reason)`` callback fired after each
                reload attempt — used by tests + audit. ``ok=True`` means
                a new engine was published; ``ok=False`` means we kept the
                previous one (validation failed / reload skipped /
                content unchanged).
        """
        self.path = Path(path)
        self.poll_interval = poll_interval_seconds
        self.debounce = debounce_seconds
        self._on_reload = on_reload
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # State for change detection. We track BOTH mtime and content
        # hash: mtime alone has false positives (touch + save-no-change),
        # content alone has false negatives (mtime backwards on clock
        # skew never matters, but we still gate the hash read on mtime
        # to avoid hashing on every poll).
        self._last_mtime: float = self._read_mtime()
        self._last_hash: str = self._read_hash()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="PolicyHotReloader",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[PolicyHotReload] watching %s every %.1fs (debounce %.1fs)",
            self.path,
            self.poll_interval,
            self.debounce,
        )

    def stop(self, *, timeout: float | None = None) -> None:
        if self._thread is None:
            return
        self._stop.set()
        join_timeout = timeout if timeout is not None else self.poll_interval + 1.0
        self._thread.join(timeout=join_timeout)
        if self._thread.is_alive():
            logger.warning(
                "[PolicyHotReload] thread did not exit within %.1fs",
                join_timeout,
            )
        self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            # ``Event.wait`` returns True when stop fires — exit immediately.
            if self._stop.wait(self.poll_interval):
                return
            try:
                self._check_once()
            except Exception:  # noqa: BLE001
                # Never let a poll iteration kill the watcher thread —
                # the next iteration retries cleanly.
                logger.exception("[PolicyHotReload] poll iteration failed")

    def _check_once(self) -> None:
        """Single poll cycle. Public for testability."""
        cur_mtime = self._read_mtime()
        if cur_mtime == 0.0:
            # File missing — keep current engine. Don't spam logs; only
            # warn the first time we transition from "present" → "absent".
            if self._last_mtime != 0.0:
                logger.warning(
                    "[PolicyHotReload] %s disappeared — keeping current engine",
                    self.path,
                )
                self._last_mtime = 0.0
            return

        if cur_mtime == self._last_mtime:
            return  # fast path: nothing to do

        # mtime changed — debounce before reading content. Editors often
        # do (1) truncate file, (2) write new bytes; reading between
        # those two would feed us a half-written YAML.
        if self.debounce > 0:
            if self._stop.wait(self.debounce):
                return

        new_hash = self._read_hash()
        self._last_mtime = self._read_mtime()  # refresh after debounce

        if new_hash == self._last_hash:
            # Same content, just a metadata touch (git checkout same sha,
            # ``touch POLICIES.yaml``, etc.). Skip the rebuild entirely.
            logger.debug(
                "[PolicyHotReload] mtime bumped but content unchanged — skipping"
            )
            self._fire("noop", "content unchanged")
            return

        # Real change — attempt reload.
        self._last_hash = new_hash
        self._do_reload()

    # ------------------------------------------------------------------
    # Reload + audit
    # ------------------------------------------------------------------

    def _do_reload(self) -> None:
        """Call rebuild_engine_v2 and emit an audit row.

        We deliberately do NOT validate the new YAML *before* calling
        rebuild_engine_v2 — rebuild_engine_v2 already runs full schema
        validation under its own lock, and routes validation failure
        through ``_recover_from_load_failure`` (= LKG). Replicating that
        check here would double-validate and create a TOCTOU window
        between our validation and rebuild's. The single-validation
        contract is part of why C16's LKG is sound.
        """
        from .global_engine import _get_last_known_good, rebuild_engine_v2

        before_lkg = _get_last_known_good()
        new_engine_id = None
        ok = False
        reason = ""
        try:
            new_engine = rebuild_engine_v2(yaml_path=self.path)
            new_engine_id = id(new_engine)
            after_lkg = _get_last_known_good()
            # Reload success / failure detection. ``rebuild_engine_v2``'s
            # contract: on a successful YAML load + validate it calls
            # ``_set_last_known_good(cfg)`` → LKG identity changes. On
            # failure it routes through ``_recover_from_load_failure``
            # which returns the existing LKG (or ``PolicyConfigV2()``
            # defaults when LKG was None) and does NOT touch LKG.
            #
            # So we have three cases:
            #
            # 1. ``after_lkg is None`` — process started with a broken
            #    YAML (LKG never got promoted). Current reload also
            #    failed → rebuild silently returned defaults. This is
            #    a failed reload even though no "identity didn't change"
            #    signal fires (because before_lkg was also None).
            # 2. ``before_lkg is not None and after_lkg is before_lkg``
            #    — classic failure-with-LKG: the previous good config
            #    is preserved.
            # 3. ``after_lkg`` is a NEW object — promotion happened,
            #    reload succeeded.
            if after_lkg is None:
                ok = False
                reason = "validation failed; no last-known-good available"
                logger.warning(
                    "[PolicyHotReload] %s reload skipped (no LKG; engine "
                    "fell back to defaults)",
                    self.path,
                )
            elif before_lkg is not None and after_lkg is before_lkg:
                ok = False
                reason = "validation failed; kept last-known-good"
                logger.warning(
                    "[PolicyHotReload] %s reload skipped (validation failed)",
                    self.path,
                )
            else:
                ok = True
                reason = "engine rebuilt"
                logger.info(
                    "[PolicyHotReload] %s reloaded (engine=%s)",
                    self.path,
                    new_engine_id,
                )
        except Exception as exc:  # noqa: BLE001
            ok = False
            reason = f"rebuild raised: {type(exc).__name__}: {exc}"[:200]
            logger.exception("[PolicyHotReload] rebuild raised — keeping current")

        # Audit the reload attempt. We use the same audit chain as policy
        # decisions so verify_chain detects post-hoc tampering with the
        # reload history.
        self._write_audit(ok=ok, reason=reason, engine_id=new_engine_id)
        self._fire("ok" if ok else "fail", reason)

    def _write_audit(self, *, ok: bool, reason: str, engine_id: int | None) -> None:
        try:
            from ..audit_logger import get_audit_logger

            get_audit_logger().log(
                tool_name="<policy_hot_reload>",
                decision="reload_ok" if ok else "reload_failed",
                reason=reason[:200],
                policy="policy_hot_reload",
                metadata={
                    "policies_yaml": str(self.path),
                    "engine_id": engine_id,
                    "ts": time.time(),
                    "ok": ok,
                },
            )
        except Exception:
            logger.exception("[PolicyHotReload] failed to write audit row")

    def _fire(self, kind: str, reason: str) -> None:
        if self._on_reload is None:
            return
        try:
            self._on_reload(kind == "ok", reason)
        except Exception:
            logger.exception("[PolicyHotReload] on_reload callback raised")

    # ------------------------------------------------------------------
    # File helpers
    # ------------------------------------------------------------------

    def _read_mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def _read_hash(self) -> str:
        try:
            data = self.path.read_bytes()
        except OSError:
            return ""
        return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Module-level singleton API (this is what api/server.py wires)
# ---------------------------------------------------------------------------


def start_hot_reloader(
    *,
    yaml_path: Path | str | None = None,
    poll_interval_seconds: float | None = None,
    debounce_seconds: float | None = None,
    on_reload: Callable[[bool, str], None] | None = None,
    force: bool = False,
) -> PolicyHotReloader | None:
    """Start the singleton hot-reloader if PolicyConfigV2 enables it.

    Returns the running reloader, or ``None`` when:

    - ``hot_reload.enabled`` is False and ``force`` is False (default).
    - ``POLICIES.yaml`` cannot be located on disk.

    Idempotent: calling twice without ``stop_hot_reloader`` returns the
    existing instance (it does NOT restart with new parameters).
    """
    global _reloader
    with _RELOADER_LOCK:
        if _reloader is not None and _reloader.is_running():
            return _reloader

        path = _resolve_path(yaml_path)
        if path is None:
            logger.info(
                "[PolicyHotReload] POLICIES.yaml not found — hot-reload disabled"
            )
            return None

        if not force:
            cfg = _safe_get_hot_reload_cfg()
            if cfg is None or not cfg.enabled:
                return None
            if poll_interval_seconds is None:
                poll_interval_seconds = cfg.poll_interval_seconds
            if debounce_seconds is None:
                debounce_seconds = cfg.debounce_seconds

        _reloader = PolicyHotReloader(
            path,
            poll_interval_seconds=poll_interval_seconds or 5.0,
            debounce_seconds=debounce_seconds if debounce_seconds is not None else 0.5,
            on_reload=on_reload,
        )
        _reloader.start()
        return _reloader


def stop_hot_reloader(*, timeout: float | None = None) -> None:
    """Stop the singleton if running. Idempotent."""
    global _reloader
    with _RELOADER_LOCK:
        if _reloader is None:
            return
        _reloader.stop(timeout=timeout)
        _reloader = None


def get_hot_reloader() -> PolicyHotReloader | None:
    """Inspect the running singleton (test helper)."""
    with _RELOADER_LOCK:
        return _reloader


def _resolve_path(yaml_path: Path | str | None) -> Path | None:
    if yaml_path is not None:
        p = Path(yaml_path)
        return p if p.exists() else None
    # Re-use the same resolver global_engine uses so probe + writer +
    # reloader stay synced (parallels the C17 二轮 audit-path fix).
    try:
        from .global_engine import _resolve_yaml_path

        return _resolve_yaml_path()
    except Exception:
        return None


def _safe_get_hot_reload_cfg():
    """Pull HotReloadConfig without forcing engine init.

    We can't ``get_config_v2()`` here because it would synchronously load
    the YAML — and we want to be callable from app startup *before* the
    engine is initialized. Falls back to defaults on any error.
    """
    try:
        from .global_engine import get_config_v2

        return get_config_v2().hot_reload
    except Exception:
        from .schema import HotReloadConfig

        return HotReloadConfig()


__all__ = [
    "PolicyHotReloader",
    "get_hot_reloader",
    "start_hot_reloader",
    "stop_hot_reloader",
]
