"""Desktop / IM attachment runtime helpers.

This package groups runtime-level helpers that classify, persist, and format
attachments arriving via desktop and IM channels. They own HTTP upload
routing, data URI decoding, filesystem persistence, and prompt-safe reference
formatting without depending on agent state.

See :mod:`openakita.runtime.desktop.attachments` for the helpers.
"""

from __future__ import annotations

from .attachments import (
    DATA_URI_RE,
    INLINE_IMAGE_MAX_BYTES,
    LOCAL_UPLOAD_RE,
    format_desktop_attachment_reference,
    maybe_inline_local_image,
    safe_attachment_stem,
    save_data_uri_attachment,
)

__all__ = [
    "DATA_URI_RE",
    "INLINE_IMAGE_MAX_BYTES",
    "LOCAL_UPLOAD_RE",
    "format_desktop_attachment_reference",
    "maybe_inline_local_image",
    "safe_attachment_stem",
    "save_data_uri_attachment",
]
