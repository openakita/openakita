"""Pure helpers for the workbench plugin asset pipeline.

Lifted from :mod:`openakita.orgs.runtime` so the 6 k-line file shrinks and
so these utilities can be unit-tested without spinning up an entire
:class:`~openakita.orgs.runtime.OrgRuntime`.

All functions in this module are pure / I/O only — they do not touch
:class:`OrgRuntime` state. The state-mutating coordinator
``_record_plugin_asset_output`` stays on the runtime for now because it
talks to ``_register_file_output``, ``get_event_store``,
``_node_plugin_failures_in_task`` and the AssetBus, all of which are still
runtime-internal.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Single file size cap to keep a malicious / runaway URL from filling the
# workspace. 50 MB lines up with seedance-video's upload cap; tweak via the
# constant below if a future plugin needs more headroom.
PLUGIN_ASSET_MAX_BYTES: int = 50 * 1024 * 1024
PLUGIN_ASSET_DOWNLOAD_TIMEOUT_S: float = 120.0


_FORBIDDEN_FILENAME_CHARS = re.compile(r'[\x00-\x1f<>:"|?*]')


def safe_asset_filename(raw: str, default_ext: str = ".bin") -> str:
    """Strip path separators and dangerous chars from a candidate filename.

    We never trust an LLM- or plugin-supplied filename to be safe; always
    resolve under a known parent and reject path traversal.
    """
    cleaned = (raw or "").strip().replace("\\", "/").split("/")[-1]
    cleaned = _FORBIDDEN_FILENAME_CHARS.sub("_", cleaned)
    if not cleaned:
        cleaned = f"asset{default_ext}"
    # Cap length so freakishly long filenames cannot blow up file systems.
    if len(cleaned) > 120:
        stem, dot, ext = cleaned.rpartition(".")
        cleaned = (
            stem[: 120 - len(ext) - 1] + "." + ext
            if dot and len(ext) <= 8
            else cleaned[:120]
        )
    return cleaned


def ext_for_url(url: str, fallback: str = ".bin") -> str:
    """Pick a file extension based on URL path (last ``.xxx``)."""
    try:
        path = urlparse(url).path
    except Exception:
        return fallback
    m = re.search(r"\.([A-Za-z0-9]{1,8})$", path or "")
    if not m:
        return fallback
    return "." + m.group(1).lower()


def copy_to_workspace(src: Path, dest: Path) -> bool:
    """Hard-link or copy a local file into ``dest``.

    Hard-link first (cheap, atomic on same volume); fall back to copy when
    hard-linking is not possible (e.g. across volumes on Windows). Returns
    True on success.
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return True
        try:
            os.link(str(src), str(dest))
            return True
        except OSError:
            shutil.copy2(str(src), str(dest))
            return True
    except Exception as exc:
        logger.debug(
            "[plugin_assets] copy %s -> %s failed: %s", src, dest, exc,
        )
        return False


async def download_to_workspace(
    url: str,
    dest: Path,
    *,
    max_bytes: int = PLUGIN_ASSET_MAX_BYTES,
    timeout_s: float = PLUGIN_ASSET_DOWNLOAD_TIMEOUT_S,
) -> bool:
    """Stream-download ``url`` into ``dest`` using httpx, with size cap.

    Returns True on success, False on any failure (network error, 4xx/5xx,
    oversize, IO error). Never raises.
    """
    if not url:
        return False
    try:
        import httpx
    except Exception:
        logger.debug("[plugin_assets] httpx unavailable, cannot download %s", url)
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    total = 0
    try:
        async with (
            httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout_s,
            ) as client,
            client.stream("GET", url) as resp,
        ):
            if resp.status_code != 200:
                logger.info(
                    "[plugin_assets] download %s returned HTTP %s",
                    url, resp.status_code,
                )
                return False
            with open(tmp, "wb") as fh:
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        logger.warning(
                            "[plugin_assets] download %s aborted, exceeded %d bytes",
                            url, max_bytes,
                        )
                        tmp.unlink(missing_ok=True)
                        return False
                    fh.write(chunk)
        os.replace(str(tmp), str(dest))
        return dest.exists() and dest.stat().st_size > 0
    except Exception as exc:
        logger.info("[plugin_assets] download %s failed: %s", url, exc)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


__all__ = [
    "PLUGIN_ASSET_MAX_BYTES",
    "PLUGIN_ASSET_DOWNLOAD_TIMEOUT_S",
    "safe_asset_filename",
    "ext_for_url",
    "copy_to_workspace",
    "download_to_workspace",
]
