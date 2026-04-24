# ruff: noqa: N999
"""subtitle-craft vendored helpers.

These modules used to live under ``openakita_plugin_sdk.contrib.*`` but were
inlined here in 1.0.0 when the SDK retracted its ``contrib`` subpackage. Each
file is a verbatim copy of the SDK 0.6.0 version it was forked from; do not
re-import the SDK contrib path — it no longer exists. Forked from
``plugins/clip-sense/clip_sense_inline`` (and ``plugins/avatar-studio/avatar_studio_inline``
for ``parallel_executor``).

Modules:

- :mod:`subtitle_craft_inline.vendor_client`     — ``BaseVendorClient`` /
  ``VendorError`` / ``ERROR_KIND_*`` for HTTP clients.
- :mod:`subtitle_craft_inline.upload_preview`    — ``add_upload_preview_route`` /
  ``build_preview_url`` for the ``/uploads`` route.
- :mod:`subtitle_craft_inline.storage_stats`     — ``collect_storage_stats`` /
  ``StorageStats`` for the dashboard storage card.
- :mod:`subtitle_craft_inline.llm_json_parser`   — ``parse_llm_json`` /
  ``parse_llm_json_object`` / ``parse_llm_json_array`` for robust JSON
  extraction from noisy LLM output (used by Qwen-MT / Qwen-VL fallback).
- :mod:`subtitle_craft_inline.parallel_executor` — bounded-concurrency
  executor reserved for v1.1+ fan-out (translation chunks, batch repair).
- :mod:`subtitle_craft_inline.system_deps`       — ``SystemDepsManager`` for
  detecting + auto-installing system binaries (FFmpeg) on Windows / macOS /
  Linux. Forked verbatim from ``plugins/seedance-video/seedance_inline``;
  the module is dep-agnostic (parameterised by ``dep_id``) so it sits
  comfortably in any plugin that needs to gate on a system binary.
"""

