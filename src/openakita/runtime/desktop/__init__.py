"""Desktop / IM attachment runtime helpers.

This package groups runtime-level helpers that classify, persist, and
format attachments arriving via the desktop/IM channels.  The legacy
module ``openakita.core.agent`` historically owned these as private
module-level functions next to the 9000+ LOC ``Agent`` class. Per
continuation plan section 7 (P-RC-6) the helpers are concrete
runtime concerns -- HTTP upload routing, data URI base64 decoding,
filesystem persistence, prompt-safe reference formatting -- and do
not depend on any agent state, so they live under
``openakita.runtime.desktop.*`` and the legacy module re-exports the
public names with a leading underscore for backward compatibility
during the cutover.

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
