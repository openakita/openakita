"""Desktop / IM attachment routing -- runtime helpers.

Extracted from ``core/agent.py`` (P-RC-6 P6.1) as the first
self-contained block in the 9602 LOC ``Agent`` god-module. The four
public helpers replace the legacy private names; the legacy module
re-imports them via aliases for byte-faithful backward compatibility.

* :data:`LOCAL_UPLOAD_RE` -- matches ``/api/uploads/<name>`` URLs that
  point at the local FastAPI server (private IP or no host).
* :data:`INLINE_IMAGE_MAX_BYTES` -- 5 MB cap on inlining local images
  as base64 data URLs (above this we fall back to the text reference).
* :data:`DATA_URI_RE` -- parses ``data:<mime>;<params>,<payload>`` URIs
  into named groups.
* :func:`maybe_inline_local_image` -- if a URL is a local upload,
  return a data: URL for the remote LLM; else None.
* :func:`safe_attachment_stem` -- ASCII-safe, 80-char-capped filename
  stem used when persisting data URI attachments to disk.
* :func:`save_data_uri_attachment` -- decode a data URI, write to the
  upload directory, return a routing record.
* :func:`format_desktop_attachment_reference` -- produce the prompt-
  safe text the agent should inject in place of binary content for
  documents / audio / generic attachments.

All four functions are byte-faithful copies of the legacy private
helpers; the parity tests in ``tests/runtime/test_desktop_attachments.py``
(landed in P6.1b) pin this. They are intentionally pure functions
modulo filesystem and HTTP-upload helper IO, which makes them safe to
unit-test in isolation.
"""

from __future__ import annotations

import base64
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOCAL_UPLOAD_RE = re.compile(
    r"^(?:https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0)(?::\d+)?)?/api/uploads/([\w\-.]+)$",
    re.IGNORECASE,
)
INLINE_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB cap; larger images fall back to text reference

DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[^;,]+)?(?P<params>(?:;[^,]*)*),(?P<data>.*)$",
    re.DOTALL,
)


def maybe_inline_local_image(att_url: str, att_mime: str) -> str | None:
    """If *att_url* points to a locally served upload, return a base64 data URL.

    Returns ``None`` when the URL is not local, the file is missing
    or too large, or any IO error occurs (the caller then falls back
    to its existing degraded path).

    The 5 MB ceiling (``INLINE_IMAGE_MAX_BYTES``) is sized for the
    typical vision-LLM upload limit while avoiding silently inflating
    the prompt with large screenshots.
    """
    if not att_url or att_url.startswith("data:"):
        return None
    m = LOCAL_UPLOAD_RE.match(att_url.strip())
    if not m:
        return None
    filename = m.group(1)
    try:
        from openakita.api.routes.upload import get_upload_dir

        upload_dir = get_upload_dir().resolve()
        filepath = (upload_dir / filename).resolve()
        filepath.relative_to(upload_dir)  # path-traversal guard
        if not filepath.is_file():
            return None
        size = filepath.stat().st_size
        if size > INLINE_IMAGE_MAX_BYTES:
            logger.info(
                "[InlineImage] skip %s: %.1f MB exceeds limit",
                filename,
                size / 1024 / 1024,
            )
            return None
        mime = att_mime or ""
        if not mime.startswith("image/"):
            import mimetypes

            mime = mimetypes.guess_type(str(filepath))[0] or "image/png"
        data = filepath.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as exc:
        logger.warning("[InlineImage] failed to inline %s: %s", att_url, exc)
        return None


def safe_attachment_stem(filename: str) -> str:
    """Return an ASCII-safe, 80-char-capped filename stem.

    The legacy helper normalises arbitrary user filenames into a form
    safe for filesystem persistence on Windows / macOS / Linux.
    """
    stem = Path(filename or "attachment").stem or "attachment"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return stem[:80] or "attachment"


def save_data_uri_attachment(
    att_url: str,
    *,
    att_name: str,
    att_mime: str,
) -> dict[str, Any] | None:
    """Persist non-media data URI attachments and return a routing record.

    Desktop / API clients should normally upload files through
    ``/api/upload``; this fallback prevents old clients from replaying
    large base64 payloads into the LLM prompt while still preserving
    the file for tools to inspect.
    """
    match = DATA_URI_RE.match((att_url or "").strip())
    if not match:
        return None

    try:
        import mimetypes
        from urllib.parse import unquote_to_bytes

        from openakita.api.routes.upload import (
            BLOCKED_EXTENSIONS,
            MAX_UPLOAD_SIZE,
            get_upload_dir,
        )

        mime = (att_mime or match.group("mime") or "application/octet-stream").strip()
        payload = match.group("data") or ""
        params = (match.group("params") or "").lower()
        if ";base64" in params:
            raw = base64.b64decode(payload, validate=False)
        else:
            raw = unquote_to_bytes(payload)

        if len(raw) > MAX_UPLOAD_SIZE:
            logger.warning(
                "[DesktopAttachment] data URI attachment %s exceeds upload limit: %.1f MB",
                att_name,
                len(raw) / 1024 / 1024,
            )
            return None

        original = Path(att_name or "attachment")
        suffix = original.suffix.lower()
        if suffix in BLOCKED_EXTENSIONS:
            suffix = ".bin"
        if not suffix:
            suffix = mimetypes.guess_extension(mime) or ".bin"

        filename = (
            f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe_attachment_stem(att_name)}{suffix}"
        )
        filepath = get_upload_dir() / filename
        filepath.write_bytes(raw)
        return {
            "url": f"/api/uploads/{filename}",
            "local_path": str(filepath),
            "mime_type": mime,
            "size": len(raw),
        }
    except Exception as exc:
        logger.warning(
            "[DesktopAttachment] failed to persist data URI attachment %s: %s",
            att_name,
            exc,
        )
        return None


def format_desktop_attachment_reference(
    *,
    att_type: str,
    att_name: str,
    att_mime: str,
    att_url: str,
    att_local_path: str | None = None,
    att_size: int | None = None,
) -> str:
    """Return a prompt-safe text reference for non-image/video attachments.

    For data URIs we route through :func:`save_data_uri_attachment`
    so the LLM only ever sees a short local path / URL, never the
    raw base64 payload. For HTTP-served uploads we resolve the
    on-disk path via the upload route helper and surface it so
    downstream tools can read the file directly.
    """
    if (att_url or "").strip().startswith("data:"):
        saved = save_data_uri_attachment(att_url, att_name=att_name, att_mime=att_mime)
        if saved:
            return (
                f"[附件: {att_name} ({saved['mime_type']})。"
                f"已保存到本地路径: {saved['local_path']}、"
                f"URL: {saved['url']}，大小: {saved['size']} bytes。"
                "如需读取内容，请使用文件或数据处理工具打开该本地路径。]"
            )
        return (
            f"[附件: {att_name} ({att_mime or att_type}) 是内联 data URI。"
            "为避免超长 base64 内容进入模型上下文，已隐藏原始内容。"
            "请使用上传文件 URL 或重新上传附件后继续处理。]"
        )

    local_path = att_local_path
    if not local_path and att_url:
        try:
            from openakita.api.routes.upload import resolve_upload_path

            resolved = resolve_upload_path(att_url)
            if resolved:
                local_path = str(resolved)
                att_size = resolved.stat().st_size
        except Exception as exc:
            logger.debug(
                "[DesktopAttachment] failed to resolve upload path %s: %s",
                att_url,
                exc,
            )

    if att_type == "document":
        label = "文档"
    elif att_type == "voice" or (att_mime or "").startswith("audio/"):
        label = "音频"
    else:
        label = "附件"

    size_text = f"，大小: {att_size} bytes" if att_size is not None else ""
    if local_path:
        return (
            f"[{label}: {att_name} ({att_mime or att_type})。"
            f"已保存到本地路径: {local_path}，URL: {att_url or '无'}{size_text}。"
            "如需读取、转写或分析，请直接使用文件/音频处理工具打开该本地路径。]"
        )
    return f"[{label}: {att_name} ({att_mime or att_type})] URL: {att_url}"


def format_vision_unavailable_notice(
    *,
    count: int,
    names: list[str] | None = None,
    paths: list[str] | None = None,
    source: str = "图片",
) -> str:
    """Build a prompt notice that must surface to the user when images are unseen.

    Injected on image-bearing turns where no configured LLM endpoint has
    vision capability. The notice tells the model to admit it cannot read
    the image rather than responding as if no image existed.
    """
    item_label = "张图片" if source == "图片" else source
    details: list[str] = []
    clean_names = [n for n in (names or []) if n]
    clean_paths = [p for p in (paths or []) if p]
    if clean_names:
        details.append(f"文件名: {'; '.join(clean_names)}")
    if clean_paths:
        details.append(f"本地路径: {'; '.join(clean_paths)}")
    detail_text = f"（{'；'.join(details)}）" if details else ""
    return (
        f"[系统提示：用户本轮发送了 {count} {item_label}{detail_text}，但当前所有可用 LLM "
        "端点都没有 vision/图片理解能力，所以你无法查看、识别或描述图片内容。"
        "必须在回复开头明确告知用户：我收到了图片，但当前没有配置支持图片识别的模型端点，"
        "因此不能判断图片里是什么；不要猜测图片内容，不要回答成自我介绍或闲聊。"
        "请提示用户在 OpenAkita 设置中心配置带 vision 能力的 LLM 端点"
        "（例如支持图片输入的 OpenAI、Claude、Qwen-VL/GLM-4V 等模型），"
        "或改用文字描述图片后再继续。]"
    )


def has_pending_media_or_attachments(
    *,
    pending_images: Any = None,
    pending_videos: Any = None,
    pending_audio: Any = None,
    pending_files: Any = None,
    attachments: Any = None,
) -> bool:
    """True when the current turn carries any media/file payload."""
    return any(
        bool(item)
        for item in (pending_images, pending_videos, pending_audio, pending_files, attachments)
    )
