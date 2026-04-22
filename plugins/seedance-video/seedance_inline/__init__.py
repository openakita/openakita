"""seedance-video vendored helpers.

These modules used to live under ``openakita_plugin_sdk.contrib.*`` but were
inlined here in 0.7.0 when the SDK retracted its contrib subpackage. Each
file is a verbatim copy of the SDK 0.6.0 version it was forked from; do not
re-import the SDK contrib path — it no longer exists.

Modules:

- :mod:`seedance_inline.vendor_client`     — ``BaseVendorClient`` /
  ``VendorError`` / ``ERROR_KIND_*`` for ``ark_client``.
- :mod:`seedance_inline.upload_preview`    — ``add_upload_preview_route`` /
  ``build_preview_url`` for the ``/uploads`` route.
- :mod:`seedance_inline.storage_stats`     — ``collect_storage_stats`` /
  ``StorageStats`` for the dashboard storage card.
- :mod:`seedance_inline.llm_json_parser`   — ``parse_llm_json_object`` for
  ``long_video.decompose_storyboard``'s 5-level JSON extraction.
- :mod:`seedance_inline.parallel_executor` — ``run_parallel`` for the
  parallel-mode ``ChainGenerator.generate_chain`` fan-out.
"""
