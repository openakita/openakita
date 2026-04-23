# ruff: noqa: N999  (parent dir name "media-post" is fixed by plugin id)
"""Vendored helpers for ``media-post``.

The modules here are 1:1 copies of corresponding helpers in sister
plugins (``clip_sense_inline.upload_preview``, ``clip_sense_inline.storage_stats``,
``seedance_inline.system_deps``). They are vendored — not imported from a
sibling plugin — because v1.0 red-line §13 forbids cross-plugin code imports
(each first-class plugin owns its inline copy of the SDK contrib helpers it
needs). Updates to the upstream copies must be brought across explicitly.
"""
