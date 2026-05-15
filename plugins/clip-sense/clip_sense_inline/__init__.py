# ruff: noqa: N999
"""clip-sense vendored helpers.

These modules used to live under ``openakita_plugin_sdk.contrib.*`` but were
inlined here in 0.7.0 when the SDK retracted its contrib subpackage. Each
file is a verbatim copy of the SDK 0.6.0 version it was forked from; do not
re-import the SDK contrib path — it no longer exists.

Modules:

- :mod:`clip_sense_inline.vendor_client`   — ``BaseVendorClient`` /
  ``VendorError`` / ``ERROR_KIND_*`` for HTTP clients.
- :mod:`clip_sense_inline.upload_preview`  — ``add_upload_preview_route`` /
  ``build_preview_url`` for the ``/uploads`` route.
- :mod:`clip_sense_inline.storage_stats`   — ``collect_storage_stats`` /
  ``StorageStats`` for the dashboard storage card.
- :mod:`clip_sense_inline.llm_json_parser` — ``parse_llm_json`` /
  ``parse_llm_json_object`` / ``parse_llm_json_array`` for robust
  JSON extraction from noisy LLM output.
"""
