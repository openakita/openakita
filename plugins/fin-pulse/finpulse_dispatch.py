# ruff: noqa: N999
"""Thin wrapper over :meth:`PluginAPI.send_message`.

fin-pulse **never** talks to Feishu / DingTalk / WeWork / Telegram SDKs
directly — the host already ships 7+ IM adapters behind one unified
gateway and only ``channel.send`` is needed. This module therefore
adds exactly three things on top of ``api.send_message``:

1. **Line-boundary batching** (:mod:`finpulse_notification.splitter`)
   so a 25 KB daily brief doesn't get truncated by host adapters that
   otherwise pass the payload through verbatim.
2. **Per-key cooldown** — the same ``cooldown_key`` cannot fire twice
   within ``cooldown_s`` seconds. Digests key on ``daily:{session}:{YYYY-MM-DD}``;
   radar hits key on ``radar:{sha256(text)[:8]}``.
3. **Inter-chunk pacing** (``inter_chunk_delay``) so a 6-chunk radar
   push doesn't trip rate limits on wework / telegram.

No platform-specific payload construction. No webhooks. No SDK imports.
The host adapter is responsible for translating plain text into the
native card / markdown shape for each IM.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import platform
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from finpulse_notification import DEFAULT_BATCH_BYTES, split_by_lines

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """Outcome of a single :meth:`DispatchService.send` call."""

    ok: bool
    channel: str
    chat_id: str
    sent_chunks: int = 0
    skipped: str | None = None  # "cooldown" | "empty" | "dedup"
    errors: list[str] = field(default_factory=list)
    content_kind: str = "text"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "sent_chunks": self.sent_chunks,
            "skipped": self.skipped,
            "errors": list(self.errors),
            "content_kind": self.content_kind,
        }


def _content_key(channel: str, text: str) -> str:
    """Stable short hash used when ``dedupe_by_content`` is requested."""
    h = hashlib.sha256(f"{channel}::{text}".encode("utf-8")).hexdigest()
    return h[:12]


# ---------------------------------------------------------------------------
# Permanent-error detection
# ---------------------------------------------------------------------------
#
# Some host adapters surface "permanent" failures that make any further
# retry pointless — most notably WeChat iLink Bot ``ret=-2`` (request
# rejected: usually chat_id unreachable / no friend / no fresh session)
# and ``ret=-14`` (session expired). When dispatch sees one of these we
# stop attempting follow-up sends (PDF → caption → markdown chunks) and
# attach a human-readable hint so the operator knows what to do.

_PERMANENT_ERROR_MARKERS: tuple[str, ...] = (
    "ret=-2",
    "ret=-14",
    "session expired",
    "session paused",
    "no context_token",
)

_PERMANENT_ERROR_HINT = (
    "微信会话失效或不可达（ret=-2/-14），"
    "请让目标用户先给机器人发送一条消息以刷新 context_token，"
    "或检查 chat_id 是否正确。"
)


def _looks_permanent(error: str | None) -> bool:
    if not error:
        return False
    low = error.lower()
    return any(marker.lower() in low for marker in _PERMANENT_ERROR_MARKERS)


def _strip_markdown_inline(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`>#]+", "", text)
    return " ".join(text.split()).strip()


def _build_file_caption(*, header: str = "", fallback_text: str | None = None) -> str:
    """Short friendly caption attached to PDF files when the IM supports it."""
    body = fallback_text or ""
    title = header.strip()
    highlights: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") and not title:
            title = _strip_markdown_inline(line.lstrip("#").strip())
            continue
        if re.match(r"^\d+[\).]\s+", line):
            cleaned = _strip_markdown_inline(re.sub(r"^\d+[\).]\s+", "", line))
            if cleaned and not cleaned.startswith(("http://", "https://")):
                highlights.append(cleaned)
        if len(highlights) >= 3:
            break
    title = title or "Fin Pulse 报表"
    lines = [
        f"{title}",
        "",
        "PDF 报表已生成，完整排版内容请查看附件。",
    ]
    if highlights:
        lines.append("")
        lines.append("AI 摘要：")
        lines.extend(f"- {item[:120]}" for item in highlights)
    caption = "\n".join(lines).strip()
    return caption[:900]


def _bundled_runtime_roots() -> list[Path]:
    """Return possible PyInstaller resource roots for bundled browser assets."""
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))

    exe_dir = Path(sys.executable).parent
    candidates = [exe_dir]
    if exe_dir.name != "_internal":
        candidates.append(exe_dir / "_internal")
    for candidate in candidates:
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    return roots


def _find_bundled_chromium() -> tuple[str | None, Path | None]:
    """Find bundled Playwright Chromium and its browser root, if packaged.

    Playwright's default lookup may prefer chromium_headless_shell for
    headless launches. The installer currently bundles regular Chromium, so
    PDF rendering must pass chrome.exe explicitly in packaged runtime.
    """
    system = platform.system()
    is_win = system == "Windows"
    is_mac = system == "Darwin"
    exe_name = "chrome.exe" if is_win else "chrome"

    for root in _bundled_runtime_roots():
        for browsers_name in ("playwright-browsers", "playwright-browser"):
            browsers_root = root / browsers_name
            if not browsers_root.is_dir():
                continue
            for chromium_dir in sorted(browsers_root.glob("chromium-*"), reverse=True):
                candidates: list[Path] = []
                if is_win:
                    candidates.extend(
                        chromium_dir / win_dir / exe_name
                        for win_dir in ("chrome-win64", "chrome-win")
                    )
                elif is_mac:
                    candidates.extend(
                        chromium_dir
                        / mac_dir
                        / "Chromium.app"
                        / "Contents"
                        / "MacOS"
                        / "Chromium"
                        for mac_dir in ("chrome-mac-arm64", "chrome-mac")
                    )
                else:
                    candidates.append(chromium_dir / "chrome-linux" / exe_name)

                for candidate in candidates:
                    if candidate.is_file():
                        return str(candidate), browsers_root
    return None, None


def _configure_pdf_playwright_launch() -> dict[str, Any]:
    """Build launch kwargs for PDF rendering in packaged and source runtimes."""
    launch_kwargs: dict[str, Any] = {"headless": True}
    executable, browsers_root = _find_bundled_chromium()
    if browsers_root is not None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_root)
        logger.info("[fin-pulse] Using bundled Playwright browsers: %s", browsers_root)
    if executable:
        launch_kwargs["executable_path"] = executable
        logger.info("[fin-pulse] Rendering PDF with bundled Chromium: %s", executable)
    return launch_kwargs


class DispatchService:
    """One instance per plugin load. Keeps in-memory cooldown state —
    the plugin lifetime is short-lived enough that persisting to SQLite
    only adds I/O without buying real protection.
    """

    def __init__(
        self,
        api: Any,
        *,
        batch_bytes: dict[str, int] | None = None,
        inter_chunk_delay: float = 0.3,
    ) -> None:
        self._api = api
        self._batch_bytes: dict[str, int] = dict(DEFAULT_BATCH_BYTES)
        if batch_bytes:
            self._batch_bytes.update(batch_bytes)
        self._inter_chunk_delay = max(0.0, float(inter_chunk_delay))
        self._cooldown: dict[str, float] = {}

    # ── send / broadcast ─────────────────────────────────────────────

    async def send(
        self,
        *,
        channel: str,
        chat_id: str,
        content: str,
        cooldown_key: str | None = None,
        cooldown_s: float = 0.0,
        dedupe_by_content: bool = False,
        header: str = "",
        content_kind: str = "text",
        file_name: str | None = None,
        fallback_text: str | None = None,
        file_caption: str | None = None,
    ) -> DispatchResult:
        """Push ``content`` to one ``(channel, chat_id)`` target.

        * ``cooldown_key`` + ``cooldown_s`` — if the last successful
          dispatch with the same key was less than ``cooldown_s``
          seconds ago, the call is dropped with ``skipped="cooldown"``.
        * ``dedupe_by_content=True`` additionally short-circuits when
          the exact same text was pushed to the same channel within
          the cooldown window (useful for radar repeat-fire guards).
        * ``header`` is prepended to every follow-up chunk to make
          mid-stream batches self-identify.
        """
        result = DispatchResult(ok=False, channel=channel, chat_id=chat_id, content_kind=content_kind)

        text = content or ""
        if not text.strip():
            result.skipped = "empty"
            result.ok = True
            return result

        now = time.time()
        effective_keys: list[str] = []
        if cooldown_key:
            effective_keys.append(cooldown_key)
        if dedupe_by_content:
            effective_keys.append(_content_key(channel, text))
        for k in effective_keys:
            last = self._cooldown.get(k)
            if last is not None and cooldown_s > 0 and now - last < cooldown_s:
                result.skipped = "cooldown"
                result.ok = True
                return result

        adapter = self._get_adapter(channel)
        permanent_failure = False
        if content_kind == "html" and adapter is not None:
            caption = file_caption or _build_file_caption(
                header=header, fallback_text=fallback_text
            )
            sent_file, file_error = await self._try_send_pdf_file(
                adapter,
                chat_id=chat_id,
                html=text,
                file_name=file_name,
                caption=caption,
            )
            if sent_file:
                result.sent_chunks = 1
                result.ok = True
                result.content_kind = "pdf"
                for k in effective_keys:
                    self._cooldown[k] = now
                return result
            if file_error:
                result.errors.append(f"pdf_file:{file_error}")
                if _looks_permanent(file_error):
                    permanent_failure = True
            if fallback_text is not None and not permanent_failure:
                text = fallback_text
                result.content_kind = "text"

        if permanent_failure:
            # Attempting another text fan-out would just trigger the same
            # rejection (and on WeChat re-pay 4×exponential-backoff per
            # chunk), wasting minutes per push. Stop here with a clear
            # hint instead.
            result.errors.insert(0, f"hint:{_PERMANENT_ERROR_HINT}")
            return result

        max_bytes = self._batch_bytes.get(channel, self._batch_bytes["default"])
        try:
            chunks = split_by_lines(
                text, footer="", max_bytes=max_bytes, base_header=header
            )
        except ValueError as exc:
            logger.warning("splitter rejected payload for %s: %s", channel, exc)
            result.errors.append(f"splitter:{exc}")
            return result

        if not chunks:
            result.skipped = "empty"
            result.ok = True
            return result

        sent = 0
        aborted = False
        for i, chunk in enumerate(chunks):
            try:
                if adapter is not None:
                    await adapter.send_text(chat_id, chunk)
                else:
                    # Fall back to PluginAPI's fire-and-forget bridge when
                    # the host gateway is not directly exposed.
                    self._api.send_message(channel=channel, chat_id=chat_id, text=chunk)
                sent += 1
            except Exception as exc:  # noqa: BLE001 — defensive boundary
                err_str = str(exc)
                logger.warning(
                    "dispatch chunk %d/%d failed on %s: %s",
                    i + 1,
                    len(chunks),
                    channel,
                    err_str,
                )
                result.errors.append(f"text_chunk_{i + 1}:{err_str}")
                if _looks_permanent(err_str):
                    aborted = True
                    break
                continue
            if i < len(chunks) - 1 and self._inter_chunk_delay > 0:
                try:
                    await asyncio.sleep(self._inter_chunk_delay)
                except asyncio.CancelledError:
                    raise

        result.sent_chunks = sent
        result.ok = sent > 0

        if aborted and sent == 0:
            result.errors.insert(0, f"hint:{_PERMANENT_ERROR_HINT}")

        if result.ok:
            for k in effective_keys:
                self._cooldown[k] = now
        return result

    def _get_adapter(self, channel: str) -> Any | None:
        host = getattr(self._api, "_host", {}) or {}
        gateway = host.get("gateway") if isinstance(host, dict) else None
        if gateway is None or not hasattr(gateway, "get_adapter"):
            return None
        try:
            return gateway.get_adapter(channel)
        except Exception:
            return None

    async def _try_send_pdf_file(
        self,
        adapter: Any,
        *,
        chat_id: str,
        html: str,
        file_name: str | None = None,
        caption: str | None = None,
    ) -> tuple[bool, str | None]:
        """Render ``html`` to PDF then push it via ``adapter.send_file``.

        Caption handling is **decoupled** from the file send: we pass an
        empty caption to ``send_file`` so the adapter cannot fail on a
        pre-emptive caption text send (some IMs — notably WeChat
        iLink — send the caption as a separate text message *before*
        the file body, which means a failing caption misattributes the
        error as "PDF send failed" while the PDF is never even
        attempted). After the file lands we try to push the caption as
        a follow-up text via ``send_text`` — failures there are
        intentionally swallowed because the file itself already made
        it through.
        """
        if not hasattr(adapter, "send_file"):
            return False, "unsupported"
        tmp_path: Path | None = None
        tmp_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            tmp_dir = tempfile.TemporaryDirectory(prefix="fin-pulse-")
            safe_name = Path(file_name or "fin-pulse-report.pdf").name
            if safe_name.lower().endswith(".html"):
                safe_name = safe_name[:-5] + ".pdf"
            elif not safe_name.lower().endswith(".pdf"):
                safe_name += ".pdf"
            tmp_path = Path(tmp_dir.name) / safe_name
            await self._render_html_to_pdf(html, tmp_path)
            try:
                await adapter.send_file(chat_id, str(tmp_path), caption="")
            except TypeError as exc:
                if "caption" not in str(exc):
                    raise
                await adapter.send_file(chat_id, str(tmp_path))
        except NotImplementedError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            logger.warning(
                "pdf file dispatch failed on %s: %s",
                getattr(adapter, "channel_name", "?"),
                exc,
            )
            return False, str(exc)
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            if tmp_dir is not None:
                tmp_dir.cleanup()

        # File landed. Try caption as a separate, best-effort follow-up
        # so a failed caption does not invalidate a successful PDF.
        if caption:
            try:
                if hasattr(adapter, "send_text"):
                    await adapter.send_text(chat_id, caption)
                elif hasattr(adapter, "send_message") and hasattr(
                    adapter, "channel_name"
                ):
                    self._api.send_message(
                        channel=getattr(adapter, "channel_name", ""),
                        chat_id=chat_id,
                        text=caption,
                    )
            except Exception as exc:  # noqa: BLE001 — caption is optional
                logger.info(
                    "pdf caption follow-up failed on %s (ignored, file already sent): %s",
                    getattr(adapter, "channel_name", "?"),
                    exc,
                )
        return True, None

    async def _render_html_to_pdf(self, html: str, out_path: Path) -> None:
        launch_kwargs = _configure_pdf_playwright_launch()
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("playwright_unavailable") from exc

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**launch_kwargs)
            try:
                page = await browser.new_page()
                await page.set_content(html, wait_until="load")
                await page.pdf(
                    path=str(out_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "14mm", "right": "12mm", "bottom": "14mm", "left": "12mm"},
                )
                await page.close()
            finally:
                await browser.close()

    async def broadcast(
        self,
        *,
        targets: list[dict[str, str]],
        content: str,
        cooldown_key: str | None = None,
        cooldown_s: float = 0.0,
        dedupe_by_content: bool = False,
        header: str = "",
    ) -> list[DispatchResult]:
        """Fan ``content`` out to multiple targets in order. Each entry
        must carry at least ``channel`` and ``chat_id``; a missing pair
        surfaces as a ``DispatchResult`` with ``errors=["missing_target"]``
        so callers can log the bad entry without aborting the batch.
        """
        results: list[DispatchResult] = []
        for target in targets:
            channel = (target.get("channel") or "").strip()
            chat_id = (target.get("chat_id") or "").strip()
            if not channel or not chat_id:
                results.append(
                    DispatchResult(
                        ok=False,
                        channel=channel or "",
                        chat_id=chat_id or "",
                        errors=["missing_target"],
                    )
                )
                continue
            res = await self.send(
                channel=channel,
                chat_id=chat_id,
                content=content,
                cooldown_key=cooldown_key,
                cooldown_s=cooldown_s,
                dedupe_by_content=dedupe_by_content,
                header=header,
            )
            results.append(res)
        return results

    # ── cooldown controls ────────────────────────────────────────────

    def clear_cooldown(self, key: str | None = None) -> None:
        """Reset either a single cooldown key or the entire map.

        Used by unit tests and by the Settings → Schedules 「立即再推」
        button which bypasses the daily-digest cooldown.
        """
        if key is None:
            self._cooldown.clear()
        else:
            self._cooldown.pop(key, None)

    def cooldown_snapshot(self) -> dict[str, float]:
        """Read-only view of the cooldown map — useful for ``/health``
        and manual debugging from the UI.
        """
        return dict(self._cooldown)


__all__ = ["DispatchResult", "DispatchService"]
