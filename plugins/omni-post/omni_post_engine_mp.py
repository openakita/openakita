"""omni-post MultiPost Compat engine.

This is the second publish path next to :mod:`omni_post_engine_pw`.
Instead of driving Chromium ourselves, we cooperate with the user's
installed `leaperone/MultiPost-Extension` browser extension and let it
reuse the user's existing logged-in sessions. The extension lives in
the browser, so the actual DOM work happens client-side; the plugin
backend stays a pure choreographer:

1. The frontend's ``MultiPostGuide`` component probes the extension
   (``MULTIPOST_EXTENSION_CHECK_SERVICE_STATUS`` postMessage) and reports
   status up to the host via :func:`MultiPostCompatEngine.record_status`.
2. When the pipeline picks the ``mp`` engine for a task, this module
   builds a platform-agnostic dispatch payload, registers a
   per-task future on an in-memory waitlist, and asks the UI to perform
   the actual postMessage via a ``mp_dispatch`` event broadcast.
3. The UI (or any automation standing in for it) calls back into
   ``POST /mp/ack`` with the final verdict, which resolves the future
   and returns an :class:`AdapterOutcome` the pipeline already knows
   how to handle.

The module is intentionally pure Python + asyncio — no Playwright
dependency — so hosts without a local Chromium can still route traffic
through the extension path.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from omni_post_adapters import AdapterOutcome
from omni_post_models import ErrorKind

logger = logging.getLogger("openakita.plugins.omni-post")


# The published MultiPost-Extension JSON contract version we target.
# Rather than pin a single version, we treat this as the **minimum**
# supported; anything older gets refused with a typed error. Consumers
# can still override via ``settings["mp_extension_min_version"]``.
DEFAULT_MIN_VERSION = "1.3.8"

# How long we wait for the browser-side extension to return a verdict
# before declaring a timeout. The pipeline's own retry machinery will
# pick this up as a retryable error.
DEFAULT_ACK_TIMEOUT_SECONDS = 120.0


# ── Version helpers ────────────────────────────────────────────────


_VERSION_TUPLE_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _parse_version(v: str) -> tuple[int, int, int]:
    """Parse a semver-ish string into a 3-int tuple.

    We deliberately stay loose: the MultiPost extension is third-party
    so we accept things like ``"1.3"`` or ``"1.3.8-beta"`` and simply
    ignore anything past the numeric prefix. Missing components default
    to zero. Returning a tuple means ``<`` / ``>=`` comparisons are free.
    """

    m = _VERSION_TUPLE_RE.match((v or "").strip())
    if m is None:
        return (0, 0, 0)
    return (int(m.group(1) or 0), int(m.group(2) or 0), int(m.group(3) or 0))


def version_satisfies(seen: str, minimum: str) -> bool:
    """Return True iff ``seen`` is newer than or equal to ``minimum``.

    Kept as a module-level function so tests can pin it directly and so
    the UI's "too old" banner can reuse the same rule without guessing.
    """

    return _parse_version(seen) >= _parse_version(minimum)


# ── Payload shaping ────────────────────────────────────────────────


# Map our internal platform ids to the MultiPost-Extension platform
# strings. Values here mirror the extension's own catalog; when they
# differ from our id, the mapping is explicit (e.g. rednote → xiaohongshu).
_PLATFORM_MP_MAP: dict[str, str] = {
    "douyin": "douyin",
    "rednote": "xiaohongshu",
    "bilibili": "bilibili",
    "wechat_channels": "weixin_video",
    "wechat_mp": "weixin_mp",
    "kuaishou": "kuaishou",
    "youtube": "youtube",
    "tiktok": "tiktok",
    "zhihu": "zhihu",
    "weibo": "weibo",
}


def build_mp_payload(
    *,
    task: dict[str, Any],
    asset_info: dict[str, Any] | None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Translate an omni-post task into the MultiPost dispatch payload.

    Output shape follows the extension's documented contract:
    ``{ action: "MULTIPOST_EXTENSION_REQUEST_PUBLISH", data: {...} }``.
    The pipeline does NOT send cookies through this path — the
    extension owns the session — so this function is safe to log.
    """

    payload = task.get("payload") or {}
    platform_mp = _PLATFORM_MP_MAP.get(task["platform"], task["platform"])
    data: dict[str, Any] = {
        "platform": platform_mp,
        "title": payload.get("title") or "",
        "content": payload.get("content") or payload.get("description") or "",
        "hashtags": list(payload.get("hashtags") or []),
        "mentions": list(payload.get("mentions") or []),
        "task_id": task["id"],
        "client_trace_id": task.get("client_trace_id"),
    }
    if asset_info is not None:
        data["asset"] = {
            "kind": asset_info.get("kind"),
            "path": asset_info.get("storage_path"),
            "duration_ms": asset_info.get("duration_ms"),
            "filename": asset_info.get("filename"),
        }
    if settings:
        data["auto_submit"] = bool(settings.get("auto_submit", True))
    return {
        "action": "MULTIPOST_EXTENSION_REQUEST_PUBLISH",
        "contract_version": DEFAULT_MIN_VERSION,
        "data": data,
    }


# ── Engine implementation ──────────────────────────────────────────


@dataclass
class _Waiter:
    """In-memory rendezvous between the pipeline and the extension ack.

    The pipeline-side coroutine awaits ``future``; the HTTP ``/mp/ack``
    handler resolves it. We stash the broadcast payload so the UI can
    still fetch pending dispatches on poll (``/mp/pending``) if the
    extension missed the initial event (e.g. the user opened the tab
    after the broadcast fired).
    """

    future: asyncio.Future[AdapterOutcome]
    dispatch: dict[str, Any]
    started_at: float
    auto_submit: bool = True
    attempts: int = 0


@dataclass
class MultiPostStatus:
    """Snapshot of the most recent extension detection.

    Pushed by the UI via :func:`MultiPostCompatEngine.record_status`;
    surfaced to settings / tool routes via
    :func:`MultiPostCompatEngine.snapshot_status`.
    """

    installed: bool = False
    version: str | None = None
    trusted_domain_ok: bool = False
    checked_at: str | None = None
    min_version: str = DEFAULT_MIN_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "version": self.version,
            "trusted_domain_ok": self.trusted_domain_ok,
            "checked_at": self.checked_at,
            "min_version": self.min_version,
            "version_ok": bool(self.version and version_satisfies(self.version, self.min_version)),
        }


class MultiPostCompatEngine:
    """Asyncio-friendly choreographer for the MultiPost extension path."""

    def __init__(
        self,
        *,
        settings: dict[str, Any] | None = None,
        ack_timeout_seconds: float = DEFAULT_ACK_TIMEOUT_SECONDS,
        broadcaster: Any = None,
    ) -> None:
        self._settings = dict(settings or {})
        self._ack_timeout = float(ack_timeout_seconds)
        self._broadcaster = broadcaster
        self._waiters: dict[str, _Waiter] = {}
        self._status = MultiPostStatus(
            min_version=str(
                self._settings.get("mp_extension_min_version", DEFAULT_MIN_VERSION)
            ),
        )
        self._lock = asyncio.Lock()

    # ── public status API ─────────────────────────────────────────

    def record_status(
        self,
        *,
        installed: bool,
        version: str | None,
        trusted_domain_ok: bool,
        checked_at: str | None = None,
    ) -> dict[str, Any]:
        """Register the latest extension probe result.

        Called by the frontend's ``MultiPostGuide`` after a postMessage
        ping. The return value is the full status snapshot so the UI
        can also refresh its own banner.
        """

        self._status.installed = bool(installed)
        self._status.version = (version or None) if installed else None
        self._status.trusted_domain_ok = bool(trusted_domain_ok)
        self._status.checked_at = checked_at
        return self._status.to_dict()

    def snapshot_status(self) -> dict[str, Any]:
        return self._status.to_dict()

    def is_available(self) -> bool:
        """Cheap "can we route through MP right now?" check.

        Returns True iff the extension probed OK, the trusted domain
        is configured, and the version meets the minimum bar. The
        pipeline's ``engine="auto"`` logic uses this to fall through to
        Playwright gracefully.
        """

        s = self._status
        if not s.installed or not s.trusted_domain_ok:
            return False
        return bool(s.version and version_satisfies(s.version, s.min_version))

    # ── pending/ack surface ───────────────────────────────────────

    async def dispatch(
        self,
        *,
        task: dict[str, Any],
        asset_info: dict[str, Any] | None,
        settings: dict[str, Any] | None = None,
    ) -> AdapterOutcome:
        """Run one task through the MP extension. Blocks until ack."""

        merged_settings = {**self._settings, **(settings or {})}
        if not self.is_available():
            return AdapterOutcome(
                success=False,
                error_kind=ErrorKind.DEPENDENCY.value,
                error_message="MultiPost extension unavailable or too old",
            )

        payload = build_mp_payload(task=task, asset_info=asset_info, settings=merged_settings)
        future: asyncio.Future[AdapterOutcome] = asyncio.get_event_loop().create_future()
        waiter = _Waiter(
            future=future,
            dispatch=payload,
            started_at=asyncio.get_event_loop().time(),
            auto_submit=bool(merged_settings.get("auto_submit", True)),
        )
        async with self._lock:
            self._waiters[task["id"]] = waiter

        if self._broadcaster is not None:
            try:
                self._broadcaster(
                    "mp_dispatch",
                    {"task_id": task["id"], "payload": payload},
                )
            except Exception as e:  # noqa: BLE001
                # Broadcasting is best-effort; the UI also has a poll
                # fallback via list_pending_dispatches(), so a missed
                # event doesn't strand the task.
                logger.warning("mp_dispatch broadcast failed: %s", e)

        try:
            outcome = await asyncio.wait_for(future, timeout=self._ack_timeout)
            return outcome
        except TimeoutError:
            return AdapterOutcome(
                success=False,
                error_kind=ErrorKind.TIMEOUT.value,
                error_message=(
                    f"MultiPost extension did not ack within "
                    f"{self._ack_timeout:.0f}s"
                ),
            )
        finally:
            async with self._lock:
                self._waiters.pop(task["id"], None)

    async def ack(
        self,
        *,
        task_id: str,
        success: bool,
        published_url: str | None = None,
        error_kind: str | None = None,
        error_message: str = "",
        metrics: dict[str, Any] | None = None,
    ) -> bool:
        """Resolve the waiting task. Returns True iff a waiter existed.

        Idempotent: repeated acks for the same task are silently
        dropped so a flaky extension that retries the postMessage
        callback cannot accidentally double-fire the pipeline.
        """

        async with self._lock:
            waiter = self._waiters.pop(task_id, None)
        if waiter is None:
            return False
        if waiter.future.done():
            return False
        waiter.future.set_result(
            AdapterOutcome(
                success=bool(success),
                published_url=published_url,
                error_kind=error_kind if not success else None,
                error_message=error_message or "",
                metrics=dict(metrics or {}),
            )
        )
        return True

    def list_pending_dispatches(self) -> list[dict[str, Any]]:
        """UI polling helper — returns the currently-pending dispatches.

        Shape matches what the frontend needs to actually perform the
        postMessage: ``[{task_id, payload, auto_submit}]``. We omit the
        asyncio Future because it is not serialisable and never of
        interest to the extension.
        """

        out: list[dict[str, Any]] = []
        for tid, w in self._waiters.items():
            out.append(
                {
                    "task_id": tid,
                    "payload": w.dispatch,
                    "auto_submit": w.auto_submit,
                    "waiting_ms": int(
                        (asyncio.get_event_loop().time() - w.started_at) * 1000
                    ),
                }
            )
        return out
