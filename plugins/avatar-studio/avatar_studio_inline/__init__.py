"""avatar-studio vendored helpers.

These modules used to live under ``openakita_plugin_sdk.contrib.*`` but were
inlined here in 0.7.0 when the SDK retracted its contrib subpackage. Each
file is a verbatim copy of the seedance-video ``seedance_inline/`` peer (which
itself was forked from SDK 0.6.0); do not re-import the SDK contrib path —
it no longer exists.

Modules:

- :mod:`avatar_studio_inline.vendor_client`     — ``BaseVendorClient`` /
  ``VendorError`` / ``ERROR_KIND_*`` for ``avatar_dashscope_client``.
- :mod:`avatar_studio_inline.upload_preview`    — ``add_upload_preview_route`` /
  ``build_preview_url`` for the ``/uploads`` route.
- :mod:`avatar_studio_inline.storage_stats`     — ``collect_storage_stats`` /
  ``StorageStats`` for the Settings storage section.
- :mod:`avatar_studio_inline.llm_json_parser`   — ``parse_llm_json_object`` for
  parsing qwen-vl-max scene/prompt suggestions during avatar_compose.
- :mod:`avatar_studio_inline.parallel_executor` — ``run_parallel`` for the
  multi-frame face check fan-out used by future video_reface refinements.
"""
